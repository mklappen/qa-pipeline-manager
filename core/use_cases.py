import re
from typing import Callable, Awaitable

import httpx
from bs4 import BeautifulSoup
from core.llm import call_llm

LogCallback = Callable[[str], Awaitable[None]]


def generate_acronym(title_text: str) -> str:
    exclude = {"and", "it", "the", "in", "on", "at", "to", "for", "of", "with", "a", "an", "by", "is", "case", "ai"}
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in title_text)
    words = cleaned.split()
    return "".join(w[0].upper() for w in words if w.lower() not in exclude)[:3]


async def fetch_confluence_page(page_id: str, url: str, email: str, token: str, log: LogCallback):
    await log(f"Reading Confluence page [{page_id}]...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{url}/wiki/api/v2/pages/{page_id}?body-format=storage",
            auth=(email, token),
        )
        resp.raise_for_status()
        data = resp.json()

    page_title = data["title"]
    html_content = data["body"]["storage"]["value"]

    soup = BeautifulSoup(html_content, "html.parser")
    for header in soup.find_all(["h1", "h2", "h3"]):
        header.insert_before("\n\n## ")
        header.insert_after("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n* ")
    for row in soup.find_all("tr"):
        row.insert_before("\n| ")

    raw_text = soup.get_text()
    body = "\n".join(line.strip() for line in raw_text.splitlines() if line.strip())
    return page_title, body


async def fetch_clickup_context(task_id: str, token: str, log: LogCallback):
    await log(f"Reading ClickUp task [{task_id}]...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api.clickup.com/api/v2/task/{task_id}",
            headers={"Authorization": token},
        )
        resp.raise_for_status()
        data = resp.json()

    title = data.get("name", "Untitled Task")
    description = data.get("description", "")
    test_instructions = ""

    for field in data.get("custom_fields", []):
        if field.get("name", "").lower() == "test instructions":
            val = field.get("value", "")
            test_instructions = val.get("value", str(val)) if isinstance(val, dict) else val
            break

    return title, description, test_instructions


async def generate_use_cases(context: str, llm_settings: dict, system_prompt: str, log: LogCallback) -> str:
    return await call_llm(
        context, system_prompt, llm_settings, log,
        progress_pattern=r"### USE CASE:", progress_label="use cases",
    )


def parse_use_case_blocks(raw_markdown: str, prefix_code: str) -> list:
    blocks = re.split(r"(?=### USE CASE:)", raw_markdown)
    compiled = []
    idx = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if "### USE CASE:" in lines[0]:
            idx += 1
            case_number = f"{prefix_code}-{idx}"
            raw_title = lines[0].replace("### USE CASE:", "").strip()
            priority_m = re.search(r"\*\*Priority:\*\*\s*(Critical|High|Normal|Low)", block, re.IGNORECASE)
            priority = priority_m.group(1).title() if priority_m else "Normal"
            compiled.append({
                "case_number": case_number,
                "title": f"[{case_number}] - {raw_title}",
                "raw_title": raw_title,
                "priority": priority,
                "content": block,
            })
    return compiled


def format_use_cases_markdown(prefix_code: str, cases: list) -> str:
    parts = [f"# System Use Case Specifications ({prefix_code})\n"]
    for idx, case in enumerate(cases, 1):
        parts.append(f"## {idx}. {case['title']}\n\n{case['content']}\n\n---\n")
    return "\n".join(parts)


async def push_use_cases_to_clickup(cases: list, clickup_settings: dict, log: LogCallback):
    url = f"https://api.clickup.com/api/v2/list/{clickup_settings['use_case_list_id']}/task"
    headers = {"Authorization": clickup_settings["api_token"], "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as session:
        for case in cases:
            payload = {
                "name": case["title"],
                "markdown_content": case["content"],
                "status": clickup_settings["status"],
            }
            resp = await session.post(url, json=payload, headers=headers)
            if resp.status_code in [200, 201]:
                await log(f"  Created: {case['title']}")
            else:
                await log(f"  Failed '{case['title']}': {resp.text}")
