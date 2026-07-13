import asyncio
import hashlib
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from core.llm import call_llm

_REGEN_SYSTEM_PROMPT = """\
You are revising a single system use case based on human reviewer feedback.
Your output must use this exact structure:

### USE CASE: [Short Descriptive Title]
- **Description:** [Brief summary of interaction objective]
- **Actors:** [User, API Client, etc.]
- **Pre-conditions:** [System state required before execution]
- **Main Success Scenario Steps:**
  1. [Step description]
- **Alternative Scenario Steps (Negative Path):**
  1. [What happens on validation failure]
- **Exception Scenario Steps (Edge Case):**
  1. [What happens on system/network failure]

Output ONLY the revised use case block. No introduction, no conclusion, no explanation.\
"""

router = APIRouter()


class ApproveRequest(BaseModel):
    current_text: str


class RejectRequest(BaseModel):
    reason: str


class DraftRequest(BaseModel):
    current_text: str


class PriorityRequest(BaseModel):
    priority: str


class DuplicateCheckRequest(BaseModel):
    text: str


_VALID_PRIORITIES = {"Critical", "High", "Normal", "Low"}


class BulkDeleteRequest(BaseModel):
    ids: list[int]


@router.post("/check-duplicate")
async def check_duplicate(req: DuplicateCheckRequest):
    text = req.text.strip()
    if not text:
        return {"match": "none"}
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    result = await asyncio.to_thread(db.find_similar_run, content_hash, text)
    return result


@router.post("/bulk-delete")
async def bulk_delete_use_cases(req: BulkDeleteRequest):
    if not req.ids:
        raise HTTPException(400, "No IDs provided.")
    await asyncio.to_thread(db.delete_use_cases_bulk, req.ids)
    return {"ok": True, "deleted": len(req.ids)}


@router.get("")
async def list_use_cases(run_id: Optional[int] = None, status: Optional[str] = None):
    rows = await asyncio.to_thread(db.list_use_cases, run_id, status)
    return rows


@router.get("/{uc_id}")
async def get_use_case(uc_id: int):
    row = await asyncio.to_thread(db.get_use_case, uc_id)
    if not row:
        raise HTTPException(404, "Use case not found")
    return row


@router.put("/{uc_id}")
async def save_draft(uc_id: int, req: DraftRequest):
    row = await asyncio.to_thread(db.get_use_case, uc_id)
    if not row:
        raise HTTPException(404, "Use case not found")
    await asyncio.to_thread(db.save_use_case_draft, uc_id, req.current_text)
    return {"ok": True}


@router.put("/{uc_id}/priority")
async def update_priority(uc_id: int, req: PriorityRequest):
    if req.priority not in _VALID_PRIORITIES:
        raise HTTPException(400, f"Priority must be one of {sorted(_VALID_PRIORITIES)}.")
    row = await asyncio.to_thread(db.get_use_case, uc_id)
    if not row:
        raise HTTPException(404, "Use case not found")
    await asyncio.to_thread(db.update_use_case_priority, uc_id, req.priority)
    return {"ok": True}


@router.post("/{uc_id}/approve")
async def approve_use_case(uc_id: int, req: ApproveRequest):
    try:
        await asyncio.to_thread(db.approve_use_case, uc_id, req.current_text)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.post("/{uc_id}/reject")
async def reject_use_case(uc_id: int, req: RejectRequest):
    if not req.reason.strip():
        raise HTTPException(400, "A rejection reason is required.")
    try:
        await asyncio.to_thread(db.reject_use_case, uc_id, req.reason.strip())
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.delete("/{uc_id}")
async def delete_use_case(uc_id: int):
    row = await asyncio.to_thread(db.get_use_case, uc_id)
    if not row:
        raise HTTPException(404, "Use case not found")
    await asyncio.to_thread(db.delete_use_case, uc_id)
    return {"ok": True}


@router.post("/{uc_id}/push-clickup")
async def push_uc_to_clickup(uc_id: int):
    uc = await asyncio.to_thread(db.get_use_case, uc_id)
    if not uc:
        raise HTTPException(404, "Use case not found")
    if uc["status"] != "Approved":
        raise HTTPException(400, "Only approved use cases can be pushed to ClickUp.")

    settings = await asyncio.to_thread(db.get_all_settings)
    cu = settings.get("clickup", {})
    if not cu.get("use_case_list_id", "").strip():
        raise HTTPException(400, "ClickUp Use Case List ID is not configured in Settings.")
    if not cu.get("api_token", "").strip():
        raise HTTPException(400, "ClickUp API Token is not configured in Settings.")

    cu_title = f"[UC - {uc['prefix_code']}] - {uc['title']}"
    url = f"https://api.clickup.com/api/v2/list/{cu['use_case_list_id']}/task"
    headers = {"Authorization": cu["api_token"], "Content-Type": "application/json"}
    payload = {
        "name": cu_title,
        "markdown_content": uc["current_text"],
        "status": cu.get("status", "AI - READY FOR REVIEW"),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"ClickUp API error {resp.status_code}: {resp.text}")

    await asyncio.to_thread(db.complete_use_case, uc_id)
    return {"ok": True, "clickup_id": resp.json().get("id")}


@router.post("/{uc_id}/reactivate")
async def reactivate_use_case(uc_id: int):
    uc = await asyncio.to_thread(db.get_use_case, uc_id)
    if not uc:
        raise HTTPException(404, "Use case not found")
    if uc["status"] != "Complete":
        raise HTTPException(400, "Only Complete use cases can be reactivated.")
    await asyncio.to_thread(db.reactivate_use_case, uc_id)
    return {"ok": True}


@router.post("/{uc_id}/regenerate")
async def regenerate_use_case(uc_id: int):
    uc = await asyncio.to_thread(db.get_use_case, uc_id)
    if not uc:
        raise HTTPException(404, "Use case not found")
    if not uc.get("rejection_reason"):
        raise HTTPException(400, "Use case has no rejection reason to regenerate from.")

    settings = await asyncio.to_thread(db.get_all_settings)
    llm_base = settings.get("llm", {})
    llm = {**llm_base, "temperature": llm_base.get("use_case_temperature", "0")}

    context = (
        f"REJECTED USE CASE:\n{uc['original_text']}\n\n"
        f"REVIEWER REJECTION REASON:\n{uc['rejection_reason']}\n\n"
        f"Rewrite this use case to fully address the reviewer's feedback while keeping all other aspects intact."
    )

    logs = []
    async def _log(msg: str):
        logs.append(msg)

    new_text = await call_llm(context, _REGEN_SYSTEM_PROMPT, llm, _log)

    await asyncio.to_thread(db.reset_use_case_for_review, uc_id, new_text.strip())
    return {"new_text": new_text.strip()}
