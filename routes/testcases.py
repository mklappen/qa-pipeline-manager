import asyncio
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import db
from core.llm import call_llm
from core import export as export_core

_REGEN_SYSTEM_PROMPT = """\
You are revising a single test case based on human reviewer feedback.
Your output must use this exact structure:

## [N]. [PREFIX] - [Title]

**USE CASE:** [Use case reference]

**Pre-conditions:**
- [...]

**Test Steps:**
1. [...]
2. [...]

**Expected Result:**
- [...]

Output ONLY the revised test case block. No introduction, no conclusion, no explanation.\
"""

router = APIRouter()


class ApproveRequest(BaseModel):
    current_text: str


class RejectRequest(BaseModel):
    reason: str


class DraftRequest(BaseModel):
    current_text: str


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class ExportRequest(BaseModel):
    ids: list[int]


@router.post("/bulk-delete")
async def bulk_delete_test_cases(req: BulkDeleteRequest):
    if not req.ids:
        raise HTTPException(400, "No IDs provided.")
    await asyncio.to_thread(db.delete_test_cases_bulk, req.ids)
    return {"ok": True, "deleted": len(req.ids)}


@router.post("/export")
async def export_test_cases(req: ExportRequest):
    if not req.ids:
        raise HTTPException(400, "No test case IDs provided.")
    rows = []
    for tc_id in req.ids:
        row = await asyncio.to_thread(db.get_test_case, tc_id)
        if row:
            rows.append(row)
    if not rows:
        raise HTTPException(404, "None of the requested test cases were found.")

    content = export_core.test_cases_to_excel(rows)
    filename = f"test_cases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("")
async def list_test_cases(run_id: Optional[int] = None, status: Optional[str] = None):
    rows = await asyncio.to_thread(db.list_test_cases, run_id, status)
    return rows


@router.get("/{tc_id}")
async def get_test_case(tc_id: int):
    row = await asyncio.to_thread(db.get_test_case, tc_id)
    if not row:
        raise HTTPException(404, "Test case not found")
    return row


@router.put("/{tc_id}")
async def save_draft(tc_id: int, req: DraftRequest):
    row = await asyncio.to_thread(db.get_test_case, tc_id)
    if not row:
        raise HTTPException(404, "Test case not found")
    await asyncio.to_thread(db.save_test_case_draft, tc_id, req.current_text)
    return {"ok": True}


@router.post("/{tc_id}/approve")
async def approve_test_case(tc_id: int, req: ApproveRequest):
    try:
        await asyncio.to_thread(db.approve_test_case, tc_id, req.current_text)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.post("/{tc_id}/reject")
async def reject_test_case(tc_id: int, req: RejectRequest):
    if not req.reason.strip():
        raise HTTPException(400, "A rejection reason is required.")
    try:
        await asyncio.to_thread(db.reject_test_case, tc_id, req.reason.strip())
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.delete("/{tc_id}")
async def delete_test_case(tc_id: int):
    row = await asyncio.to_thread(db.get_test_case, tc_id)
    if not row:
        raise HTTPException(404, "Test case not found")
    await asyncio.to_thread(db.delete_test_case, tc_id)
    return {"ok": True}


@router.post("/{tc_id}/push-clickup")
async def push_tc_to_clickup(tc_id: int):
    tc = await asyncio.to_thread(db.get_test_case, tc_id)
    if not tc:
        raise HTTPException(404, "Test case not found")
    if tc["status"] != "Approved":
        raise HTTPException(400, "Only approved test cases can be pushed to ClickUp.")

    settings = await asyncio.to_thread(db.get_all_settings)
    cu = settings.get("clickup", {})
    if not cu.get("test_list_id", "").strip():
        raise HTTPException(400, "ClickUp Test Case List ID is not configured in Settings.")
    if not cu.get("api_token", "").strip():
        raise HTTPException(400, "ClickUp API Token is not configured in Settings.")

    cu_title = f"[{tc['prefix_code']}] - {tc['title']}"
    url = f"https://api.clickup.com/api/v2/list/{cu['test_list_id']}/task"
    headers = {"Authorization": cu["api_token"], "Content-Type": "application/json"}
    payload = {
        "name": cu_title,
        "markdown_content": tc["current_text"],
        "status": cu.get("status", "AI - READY FOR REVIEW"),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"ClickUp API error {resp.status_code}: {resp.text}")

    await asyncio.to_thread(db.complete_test_case, tc_id)
    return {"ok": True, "clickup_id": resp.json().get("id")}


@router.post("/{tc_id}/regenerate")
async def regenerate_test_case(tc_id: int):
    tc = await asyncio.to_thread(db.get_test_case, tc_id)
    if not tc:
        raise HTTPException(404, "Test case not found")
    if not tc.get("rejection_reason"):
        raise HTTPException(400, "Test case has no rejection reason to regenerate from.")

    settings = await asyncio.to_thread(db.get_all_settings)
    llm_base = settings.get("llm", {})
    llm = {**llm_base, "temperature": llm_base.get("test_case_temperature", "0.2")}

    context = (
        f"REJECTED TEST CASE:\n{tc['original_text']}\n\n"
        f"REVIEWER REJECTION REASON:\n{tc['rejection_reason']}\n\n"
        f"Rewrite this test case to fully address the reviewer's feedback while keeping all other aspects intact."
    )

    async def _log(msg: str):
        pass

    new_text = await call_llm(context, _REGEN_SYSTEM_PROMPT, llm, _log)
    await asyncio.to_thread(db.reset_test_case_for_review, tc_id, new_text.strip())
    return {"new_text": new_text.strip()}
