from fastapi import APIRouter, HTTPException
import db

router = APIRouter()


@router.get("")
def list_history():
    rows = db.list_runs()
    return [dict(row) for row in rows]


@router.get("/{run_id}")
def get_history_item(run_id: int):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return dict(row)


@router.delete("/{run_id}")
def delete_history_item(run_id: int):
    db.delete_run(run_id)
    return {"status": "ok"}
