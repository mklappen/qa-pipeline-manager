import asyncio
import hashlib
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
from core.llm import apply_learning_context as _apply_learning_context

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
    auto_approve_test_cases: bool = False
    prefix_override: Optional[str] = None
    system_prompt_override: Optional[str] = None


@router.post("/run")
async def run_pipeline(req: PipelineRunRequest):
    if req.pipeline_type in [1, 3] and not req.confluence_page_id and not req.clickup_task_id:
        raise HTTPException(400, "Provide at least one source: Confluence Page ID or ClickUp Task ID.")
    if req.pipeline_type == 4 and not req.requirements_text:
        raise HTTPException(400, "Requirements text is required.")

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
        llm_base = settings.get("llm", {})
        conf = settings.get("confluence", {})
        cu = settings.get("clickup", {})

        # Use separate temperatures for use-case vs test-case pipelines
        llm_uc = {**llm_base, "temperature": llm_base.get("use_case_temperature", "0")}
        llm_tc = {**llm_base, "temperature": llm_base.get("test_case_temperature", "0.2")}

        if req.pipeline_type == 1:
            result_md = await _p1_use_cases_from_remote(req, llm_uc, conf, cu, log)
        elif req.pipeline_type == 2:
            result_md = await _p2_tests_from_clickup(req, llm_tc, cu, log)
        elif req.pipeline_type == 3:
            result_md = await _p3_tests_from_remote(req, llm_tc, conf, cu, log)
        elif req.pipeline_type == 4:
            result_md = await _p4_use_cases_from_text(req, llm_uc, log)
        elif req.pipeline_type == 5:
            result_md = await _p5_tests_from_use_cases(req, llm_tc, log)
        else:
            raise ValueError(f"Unknown pipeline type: {req.pipeline_type}")

        # For pipeline 5, build a descriptive history label from the output prefix
        descriptive_name = None
        if req.pipeline_type == 5:
            m = re.search(r'^# .+\(([A-Za-z0-9]+)\)', result_md, re.MULTILINE)
            if m:
                descriptive_name = f"Test Cases - {m.group(1)}"

        await asyncio.to_thread(db.update_run, run_id, "complete", result_md, descriptive_name)

        # After P2/P3/P5 test-case runs: store individual test cases for review
        if req.pipeline_type in (2, 3, 5):
            tc_cases = tc_core.parse_test_cases_from_markdown(result_md)
            tc_batch = [
                {
                    "prefix_code": c["prefix_code"],
                    "title": c["title"],
                    "use_case_ref": c["use_case_ref"],
                    "priority": c["priority"],
                    "original_text": c["content"],
                }
                for c in tc_cases
            ]
            if tc_batch:
                tc_status = "Approved" if req.auto_approve_test_cases else "Ready for Review"
                await asyncio.to_thread(db.create_test_cases_batch, run_id, tc_batch, tc_status)
                await log(f"Stored {len(tc_batch)} test cases for review.")

        # After P1/P4 use-case runs: store individual use cases + content hash for duplicate detection
        if req.pipeline_type in (1, 4):
            uc_cases = tc_core.parse_use_cases_from_markdown(result_md)
            prefix_m = re.search(r'^# .+\(([A-Za-z0-9]+)\)', result_md, re.MULTILINE)
            prefix = prefix_m.group(1) if prefix_m else "UC"
            uc_batch = [
                {
                    "prefix_code": prefix,
                    "case_number": c["case_number"],
                    "title": c["title"],
                    "priority": c["priority"],
                    "original_text": c["content"],
                }
                for c in uc_cases
            ]
            if uc_batch:
                await asyncio.to_thread(db.create_use_cases_batch, run_id, uc_batch)
                await log(f"Stored {len(uc_batch)} use cases for review.")
            if req.pipeline_type == 4 and req.requirements_text:
                content_hash = hashlib.sha256(req.requirements_text.encode()).hexdigest()
                await asyncio.to_thread(db.store_run_content, run_id, content_hash, req.requirements_text)
                await asyncio.to_thread(db.supersede_previous_use_cases, content_hash, run_id)

        await queue.put({"type": "result", "markdown": result_md, "run_id": run_id})
        await queue.put({"type": "done", "run_id": run_id})

    except Exception as exc:
        err = str(exc)
        await asyncio.to_thread(db.update_run, run_id, "error", f"Error: {err}")
        await queue.put({"type": "error", "message": err, "run_id": run_id})


# ── individual pipeline implementations ──────────────────────────────────────

def _uc_context_header(c: dict) -> str:
    """Build the '=== USE CASE: ... ===' context header, surfacing the case number if not already in the title."""
    num = c.get("case_number", "")
    if num and not c["title"].startswith(f"[{num}]"):
        return f"=== USE CASE: [{num}] {c['title']} ==="
    return f"=== USE CASE: {c['title']} ==="


def _extract_case_id(text: str) -> str:
    """Pull a 'PREFIX-N' style case identifier out of free text the LLM returned."""
    m = re.search(r"[A-Za-z0-9]+-\d+", text or "")
    return m.group(0) if m else (text or "").strip()


def _apply_use_case_priority(test_cases: list, cases: list):
    """Normalize each test case's 'use_case' reference to a clean case id and copy over its source use case's priority."""
    priority_lookup = {c["case_number"]: c.get("priority", "Normal") for c in cases if c.get("case_number")}
    for tc in test_cases:
        case_id = _extract_case_id(tc.get("use_case", ""))
        tc["use_case"] = case_id
        tc["priority"] = priority_lookup.get(case_id, "Normal")


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
    learning_ctx = await asyncio.to_thread(db.get_learning_context)
    system = await _apply_learning_context(learning_ctx, system, log)
    raw = await uc_core.generate_use_cases(combined, llm, system, log)
    cases = uc_core.parse_use_case_blocks(raw, prefix)
    await log(f"Generated {len(cases)} use cases.")

    if req.push_to_clickup:
        await uc_core.push_use_cases_to_clickup(cases, cu, log)

    return uc_core.format_use_cases_markdown(prefix, cases)


async def _p2_tests_from_clickup(req, llm, cu, log):
    cases, prefix = await tc_core.fetch_approved_use_cases_from_clickup(cu["use_case_list_id"], cu["api_token"], log)
    context = "\n\n".join(f"{_uc_context_header(c)}\n{c['content']}" for c in cases)
    system = req.system_prompt_override or llm["test_case_system_prompt"]
    tc_learning_ctx = await asyncio.to_thread(db.get_test_case_learning_context)
    system = await _apply_learning_context(tc_learning_ctx, system, log)
    test_cases = await tc_core.generate_test_cases(context, llm, system, log)
    _apply_use_case_priority(test_cases, cases)
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
    tc_learning_ctx = await asyncio.to_thread(db.get_test_case_learning_context)
    system = await _apply_learning_context(tc_learning_ctx, system, log)
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
    learning_ctx = await asyncio.to_thread(db.get_learning_context)
    system = await _apply_learning_context(learning_ctx, system, log)
    raw = await uc_core.generate_use_cases(combined, llm, system, log)
    cases = uc_core.parse_use_case_blocks(raw, prefix)
    await log(f"Generated {len(cases)} use cases.")
    return uc_core.format_use_cases_markdown(prefix, cases)


async def _p5_tests_from_use_cases(req, llm, log):
    approved = None
    if req.use_case_text:
        cases = tc_core.parse_use_cases_from_markdown(req.use_case_text)
        if not cases:
            raise ValueError("No use cases (### USE CASE: headers) found in the provided text.")
        m = re.search(r"^#.+\(([A-Za-z0-9]+)\)", req.use_case_text, re.MULTILINE)
        prefix = req.prefix_override or (m.group(1) if m else "QA")
    else:
        # No text pasted and no specific run selected (or a specific run given): pull Approved
        # use cases straight from the Use Cases review module, scoped to that run if given.
        approved = await asyncio.to_thread(db.list_use_cases, req.use_case_run_id, "Approved")
        if not approved:
            scope = f"history run #{req.use_case_run_id}" if req.use_case_run_id else "the Use Cases list"
            raise ValueError(
                f"No Approved use cases found in {scope}. "
                f"Approve at least one use case on the Use Cases review page before generating test cases."
            )
        cases = [
            {
                # Older use cases predate the case_number column and have it blank; fall back to
                # a stable id-based reference so priority can still be matched back after generation.
                "case_number": uc["case_number"] or f"UC-{uc['id']}",
                "prefix_code": uc["prefix_code"],
                "title": uc["title"],
                "priority": uc["priority"],
                "content": uc["current_text"],
            }
            for uc in approved
        ]
        prefix = req.prefix_override or cases[0].get("prefix_code") or "QA"

    await log(f"Found {len(cases)} use cases.")

    context = "\n\n".join(f"{_uc_context_header(c)}\n{c['content']}" for c in cases)
    system = req.system_prompt_override or llm["test_case_system_prompt"]
    tc_learning_ctx = await asyncio.to_thread(db.get_test_case_learning_context)
    system = await _apply_learning_context(tc_learning_ctx, system, log)
    test_cases = await tc_core.generate_test_cases(context, llm, system, log)
    _apply_use_case_priority(test_cases, cases)
    await log(f"Generated {len(test_cases)} test cases.")

    if approved:
        for uc in approved:
            await asyncio.to_thread(db.complete_use_case, uc["id"])
        await log(f"Marked {len(approved)} use case(s) as Complete.")

    return tc_core.format_test_cases_markdown(prefix, test_cases)
