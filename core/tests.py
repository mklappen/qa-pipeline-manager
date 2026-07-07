import re
import json
from typing import Callable, Awaitable

import httpx
from core.llm import call_llm

LogCallback = Callable[[str], Awaitable[None]]


async def generate_test_cases(context: str, llm_settings: dict, system_prompt: str, log: LogCallback) -> list:
    raw = await call_llm(context, system_prompt, llm_settings, log)
    return _parse_test_cases_json(raw, log)


def _parse_test_cases_json(raw: str, log: LogCallback = None) -> list:
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
        if cleaned.startswith("json"):
            cleaned = cleaned.split("\n", 1)[1].strip()

    cleaned = re.sub(r'(?<!\\)\\(?!"|\\|/|b|f|n|r|t|u)', r"\\\\", cleaned)
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    try:
        start = cleaned.find("[")
        end = cleaned.rfind("]") + 1
        return json.loads(cleaned[start:end] if start != -1 else cleaned)
    except Exception:
        compiled = []
        for block in re.split(r"\}\s*,\s*\{|\}\s*\{", cleaned):
            block = block.strip().strip("[]{}")
            if not block:
                continue
            title_m = re.search(r'"title"\s*:\s*"([\s\S]*?)"', block)
            uc_m = re.search(r'"use_case"\s*:\s*"([\s\S]*?)"', block)
            desc_m = re.search(r'"description"\s*:\s*"([\s\S]*?)"', block)
            if title_m and desc_m:
                compiled.append({
                    "title": title_m.group(1).replace('\\"', '"'),
                    "use_case": uc_m.group(1) if uc_m else "",
                    "description": desc_m.group(1).replace('\\"', '"').replace("\\n", "\n"),
                })
        return compiled


def parse_use_cases_from_markdown(markdown_text: str) -> list:
    """Extract use case blocks (with case number + priority, when present) from use-case markdown output."""
    # "## N. [case_number] - Title" headers appear once per case, in document order, ahead of
    # their "### USE CASE:" block — collect them separately since the split below discards them.
    header_numbers = re.findall(r"^##\s*\d+\.\s*\[([^\]]+)\]", markdown_text, re.MULTILINE)

    blocks = re.split(r"(?=### USE CASE:)", markdown_text)
    cases = []
    for block in blocks:
        block = block.strip()
        if not block or "### USE CASE:" not in block:
            continue
        raw_title = block.splitlines()[0].replace("### USE CASE:", "").strip()
        # Strip trailing separators and next-section headers that bleed in
        # because ## N. [case_number] headers appear before the next ### USE CASE: split point
        lines = block.rstrip().splitlines()
        while lines and (
            lines[-1].strip() in ("---", "")
            or re.match(r"^##\s+\d+", lines[-1].strip())
        ):
            lines.pop()
        content = "\n".join(lines)
        priority_m = re.search(r"\*\*Priority:\*\*\s*(Critical|High|Normal|Low)", content, re.IGNORECASE)
        priority = priority_m.group(1).title() if priority_m else "Normal"
        cases.append({"title": raw_title, "priority": priority, "content": content})

    # Only trust the collected header numbers if they line up 1:1 with parsed cases
    # (arbitrary pasted text may not include our "## N. [id]" header convention at all).
    if len(header_numbers) == len(cases):
        for c, num in zip(cases, header_numbers):
            c["case_number"] = num.strip()
    else:
        for c in cases:
            c["case_number"] = ""
    return cases


def parse_test_cases_from_markdown(markdown_text: str) -> list:
    """Extract individual test case blocks from formatted test case markdown output."""
    blocks = re.split(r"(?=## \d+\. \[)", markdown_text)
    cases = []
    for block in blocks:
        block = block.strip()
        if not block or not re.match(r"^## \d+\. \[", block):
            continue
        title_line = block.splitlines()[0]
        title_m = re.match(r"^## \d+\. \[[^\]]+\] - (.+)$", title_line)
        title = title_m.group(1).strip() if title_m else title_line
        prefix_m = re.match(r"^## \d+\. \[([^\]]+)\]", title_line)
        prefix = prefix_m.group(1) if prefix_m else ""
        uc_m = re.search(r"\*\*USE CASE:\*\* (.+)", block)
        use_case_ref = uc_m.group(1).strip() if uc_m else ""
        priority_m = re.search(r"\*\*PRIORITY:\*\*\s*(Critical|High|Normal|Low)", block, re.IGNORECASE)
        priority = priority_m.group(1).title() if priority_m else "Normal"
        lines = block.rstrip().splitlines()
        while lines and lines[-1].strip() in ("---", ""):
            lines.pop()
        cases.append({
            "title": title,
            "prefix_code": prefix,
            "use_case_ref": use_case_ref,
            "priority": priority,
            "content": "\n".join(lines),
        })
    return cases


def format_test_cases_markdown(prefix_code: str, cases: list) -> str:
    parts = [f"# Consolidated Test Specifications ({prefix_code})\n"]
    for idx, case in enumerate(cases, 1):
        prefixed_title = f"[{prefix_code}] - {case['title']}"
        parts.append(f"## {idx}. {prefixed_title}\n")
        if case.get("use_case"):
            parts.append(f"**USE CASE:** {case['use_case']}\n")
        parts.append(f"**PRIORITY:** {case.get('priority', 'Normal')}\n")
        parts.append(f"{case['description']}\n\n---\n")
    return "\n".join(parts)


async def fetch_approved_use_cases_from_clickup(list_id: str, token: str, log: LogCallback):
    await log(f"Fetching all tasks from ClickUp list [{list_id}]...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api.clickup.com/api/v2/list/{list_id}/task",
            headers={"Authorization": token},
        )
        resp.raise_for_status()
        all_tasks = resp.json().get("tasks", [])

    # ClickUp returns status as a nested object: task["status"]["status"]
    # Filter client-side (case-insensitive) so we're not reliant on API query param behaviour
    tasks = [t for t in all_tasks if t.get("status", {}).get("status", "").lower() == "approved"]

    await log(f"Found {len(tasks)} approved use cases out of {len(all_tasks)} total tasks.")

    if not tasks:
        raise ValueError(
            f"No tasks with status 'Approved' found in ClickUp list [{list_id}]. "
            f"Found {len(all_tasks)} total tasks. Check the status name in ClickUp matches 'Approved'."
        )

    cases = []
    prefix_code = "QA"
    for task in tasks:
        t_title = task.get("name", "")
        t_desc = task.get("description", "")
        case_number = ""
        m = re.search(r"\[([A-Za-z0-9]+)-(\d+)\]", t_title)
        if m:
            prefix_code = m.group(1)
            case_number = f"{m.group(1)}-{m.group(2)}"
        else:
            legacy_m = re.search(r"\[UC\s*-\s*([A-Za-z0-9]+)\]", t_title)
            if legacy_m:
                prefix_code = legacy_m.group(1)
        priority_m = re.search(r"\*\*Priority:\*\*\s*(Critical|High|Normal|Low)", t_desc, re.IGNORECASE)
        priority = priority_m.group(1).title() if priority_m else "Normal"
        cases.append({
            "id": task.get("id"),
            "title": t_title,
            "content": t_desc,
            "case_number": case_number,
            "priority": priority,
        })

    return cases, prefix_code


async def update_use_cases_to_pass(cases: list, token: str, log: LogCallback):
    await log("Updating processed Use Cases to status 'PASS'...")
    async with httpx.AsyncClient(timeout=30) as client:
        for case in cases:
            task_id = case.get("id")
            if not task_id:
                continue
            resp = await client.put(
                f"https://api.clickup.com/api/v2/task/{task_id}",
                json={"status": "PASS"},
                headers={"Authorization": token, "Content-Type": "application/json"},
            )
            if resp.status_code in [200, 201]:
                await log(f"  ✓ '{case['title']}' → PASS")
            else:
                await log(f"  Failed to update '{case['title']}': {resp.text}")


async def push_test_cases_to_clickup(cases: list, prefix_code: str, clickup_settings: dict, log: LogCallback):
    if not clickup_settings.get("test_list_id", "").strip():
        raise ValueError("ClickUp Test Case List ID is not configured. Set it in Settings → ClickUp.")
    url = f"https://api.clickup.com/api/v2/list/{clickup_settings['test_list_id']}/task"
    headers = {"Authorization": clickup_settings["api_token"], "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as session:
        for case in cases:
            prefixed_title = f"[{prefix_code}] - {case['title']}"
            payload = {
                "name": prefixed_title,
                "markdown_content": case["description"],
                "status": clickup_settings["status"],
            }
            resp = await session.post(url, json=payload, headers=headers)
            if resp.status_code in [200, 201]:
                await log(f"  Created: {prefixed_title}")
            else:
                await log(f"  Failed '{prefixed_title}': {resp.text}")
