import asyncio
import json
import re
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
from core import use_cases as uc_core
from core import tests as tc_core

router = APIRouter()

# In-memory job registry: job_id → asyncio.Queue
_jobs: Dict[str, asyncio.Queue] = {}

PIPELINE_NAMES = {
    1: "Create Use Cases from Confluence / ClickUp Requirements",
    2: "Generate Test Cases from Approved ClickUp Use Cases",
    3: "Generate Test Cases from Confluence / ClickUp Requirements",
    4: "Create Use Cases from Requirements file or text",
    5: "Generate Test Cases from Use Cases",
}


class PipelineRunRequest(BaseModel):
    pipeline_type: int
    confluence_page_id: Optional[str] = None
    clickup_task_id: Optional[str] = None
    requirements_text: Optional[str] = None
    use_case_text: Optional[str] = None
    use_case_run_id: Optional[int] = None
    push_to_clickup: bool = False
    prefix_override: Optional[str] = None
    system_prompt_override: Optional[str] = None


@router.post("/run")
async def run_pipeline(req: PipelineRunRequest):
    if req.pipeline_type in [1, 3] and not req.confluence_page_id and not req.clickup_task_id:
        raise HTTPException(400, "Provide at least one source: Confluence Page ID or ClickUp Task ID.")
    if req.pipeline_type == 4 and not req.requirements_text:
        raise HTTPException(400, "Requirements text is required.")
    if req.pipeline_type == 5 and not req.use_case_text and not req.use_case_run_id:
        raise HTTPException(400, "Provide use case text or select a history run.")

    settings = await asyncio.to_thread(db.get_all_settings)
    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue

    asyncio.create_task(_execute_pipeline(job_id, req, settings, queue))
    return {"job_id": job_id}


@router.get("/stream/{job_id}")
async def stream_pipeline(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")

    async def generator():
        queue = _jobs[job_id]
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=600)
                yield f"data: {json.dumps(item)}\n\n"
                if item["type"] in ("done", "error"):
                    _jobs.pop(job_id, None)
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Pipeline timed out after 10 minutes'})}\n\n"
                _jobs.pop(job_id, None)
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── pipeline executor ─────────────────────────────────────────────────────────

async def _execute_pipeline(job_id: str, req: PipelineRunRequest, settings: dict, queue: asyncio.Queue):
    async def log(msg: str):
        await queue.put({"type": "log", "message": msg})

    pipeline_name = PIPELINE_NAMES.get(req.pipeline_type, "Unknown")
    source_info = json.dumps({k: v for k, v in {
        "confluence_page_id": req.confluence_page_id,
        "clickup_task_id": req.clickup_task_id,
        "push_to_clickup": req.push_to_clickup,
    }.items() if v is not None})

    run_id = await asyncio.to_thread(
        db.create_run,
        req.pipeline_type,
        pipeline_name,
        source_info,
        json.dumps({"model": settings.get("llm", {}).get("model")}),
    )
    await queue.put({"type": "run_id", "run_id": run_id})

    try:
        llm = settings.get("llm", {})
        conf = settings.get("confluence", {})
        cu = settings.get("clickup", {})

        if req.pipeline_type == 1:
            result_md = await _p1_use_cases_from_remote(req, llm, conf, cu, log)
        elif req.pipeline_type == 2:
            result_md = await _p2_tests_from_clickup(req, llm, cu, log)
        elif req.pipeline_type == 3:
            result_md = await _p3_tests_from_remote(req, llm, conf, cu, log)
        elif req.pipeline_type == 4:
            result_md = await _p4_use_cases_from_text(req, llm, log)
        elif req.pipeline_type == 5:
            result_md = await _p5_tests_from_use_cases(req, llm, log)
        else:
            raise ValueError(f"Unknown pipeline type: {req.pipeline_type}")

        # For pipeline 5, build a descriptive history label from the output prefix
        descriptive_name = None
        if req.pipeline_type == 5:
            m = re.search(r'^# .+\(([A-Za-z0-9]+)\)', result_md, re.MULTILINE)
            if m:
                descriptive_name = f"Test Cases - {m.group(1)}"

        await asyncio.to_thread(db.update_run, run_id, "complete", result_md, descriptive_name)
        await queue.put({"type": "result", "markdown": result_md, "run_id": run_id})
        await queue.put({"type": "done", "run_id": run_id})

    except Exception as exc:
        err = str(exc)
        await asyncio.to_thread(db.update_run, run_id, "error", f"Error: {err}")
        await queue.put({"type": "error", "message": err, "run_id": run_id})


# ── individual pipeline implementations ──────────────────────────────────────

async def _p1_use_cases_from_remote(req, llm, conf, cu, log):
    combined, source_title = "", "SYS"

    if req.confluence_page_id:
        title, body = await uc_core.fetch_confluence_page(
            req.confluence_page_id, conf["url"], conf["email"], conf["api_token"], log
        )
        source_title = title
        combined += f"=== CONFLUENCE SOURCE: {title} ===\n{body}\n\n"

    if req.clickup_task_id:
        title, desc, inst = await uc_core.fetch_clickup_context(req.clickup_task_id, cu["api_token"], log)
        if not req.confluence_page_id:
            source_title = title
        combined += f"=== CLICKUP TASK: {title} ===\nDescription:\n{desc}\n\n"
        if inst:
            combined += f"Instructions:\n{inst}\n\n"

    prefix = req.prefix_override or uc_core.generate_acronym(source_title)
    system = req.system_prompt_override or llm["use_case_system_prompt"]
    raw = await uc_core.generate_use_cases(combined, llm, system, log)
    cases = uc_core.parse_use_case_blocks(raw, prefix)
    await log(f"Generated {len(cases)} use cases.")

    if req.push_to_clickup:
        await uc_core.push_use_cases_to_clickup(cases, cu, log)

    return uc_core.format_use_cases_markdown(prefix, cases)


async def _p2_tests_from_clickup(req, llm, cu, log):
    cases, prefix = await tc_core.fetch_approved_use_cases_from_clickup(cu["use_case_list_id"], cu["api_token"], log)
    context = "\n\n".join(f"=== USE CASE: {c['title']} ===\n{c['content']}" for c in cases)
    system = req.system_prompt_override or llm["test_case_system_prompt"]
    test_cases = await tc_core.generate_test_cases(context, llm, system, log)
    await log(f"Generated {len(test_cases)} test cases.")

    if req.push_to_clickup:
        await tc_core.push_test_cases_to_clickup(test_cases, prefix, cu, log)
        await tc_core.update_use_cases_to_pass(cases, cu["api_token"], log)

    return tc_core.format_test_cases_markdown(prefix, test_cases)


async def _p3_tests_from_remote(req, llm, conf, cu, log):
    combined, source_title = "", "SYS"

    if req.confluence_page_id:
        title, body = await uc_core.fetch_confluence_page(
            req.confluence_page_id, conf["url"], conf["email"], conf["api_token"], log
        )
        source_title = title
        combined += f"=== CONFLUENCE SOURCE: {title} ===\n{body}\n\n"

    if req.clickup_task_id:
        title, desc, inst = await uc_core.fetch_clickup_context(req.clickup_task_id, cu["api_token"], log)
        if not req.confluence_page_id:
            source_title = title
        combined += f"=== CLICKUP TASK: {title} ===\nDescription:\n{desc}\nInstructions:\n{inst}\n\n"

    prefix = req.prefix_override or uc_core.generate_acronym(source_title)
    system = req.system_prompt_override or llm["test_case_system_prompt"]
    test_cases = await tc_core.generate_test_cases(combined, llm, system, log)
    await log(f"Generated {len(test_cases)} test cases.")

    if req.push_to_clickup:
        await tc_core.push_test_cases_to_clickup(test_cases, prefix, cu, log)

    return tc_core.format_test_cases_markdown(prefix, test_cases)


async def _p4_use_cases_from_text(req, llm, log):
    combined = f"=== LOCAL REQUIREMENTS ===\n{req.requirements_text}\n\n"
    m = re.search(r"Requirements Specification\s*[-:|]\s*(.+)", req.requirements_text, re.IGNORECASE)
    prefix = req.prefix_override or (uc_core.generate_acronym(m.group(1).strip()) if m else "REQ")
    system = req.system_prompt_override or llm["use_case_system_prompt"]
    raw = await uc_core.generate_use_cases(combined, llm, system, log)
    cases = uc_core.parse_use_case_blocks(raw, prefix)
    await log(f"Generated {len(cases)} use cases.")
    return uc_core.format_use_cases_markdown(prefix, cases)


async def _p5_tests_from_use_cases(req, llm, log):
    use_case_md = req.use_case_text

    if req.use_case_run_id and not use_case_md:
        row = await asyncio.to_thread(db.get_run, req.use_case_run_id)
        if not row:
            raise ValueError(f"History run #{req.use_case_run_id} not found.")
        use_case_md = row["output_markdown"]

    cases = tc_core.parse_use_cases_from_markdown(use_case_md)
    if not cases:
        raise ValueError("No use cases (### USE CASE: headers) found in the provided text.")

    await log(f"Found {len(cases)} use cases.")

    m = re.search(r"^#.+\(([A-Za-z0-9]+)\)", use_case_md, re.MULTILINE)
    prefix = req.prefix_override or (m.group(1) if m else "QA")

    context = "\n\n".join(f"=== USE CASE: {c['title']} ===\n{c['content']}" for c in cases)
    system = req.system_prompt_override or llm["test_case_system_prompt"]
    test_cases = await tc_core.generate_test_cases(context, llm, system, log)
    await log(f"Generated {len(test_cases)} test cases.")

    return tc_core.format_test_cases_markdown(prefix, test_cases)
