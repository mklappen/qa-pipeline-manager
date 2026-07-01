from typing import Dict
from fastapi import APIRouter
from pydantic import BaseModel
import db

router = APIRouter()


class SettingsUpdate(BaseModel):
    settings: Dict[str, Dict[str, str]]


@router.get("")
def get_settings():
    return db.get_all_settings()


@router.put("")
def update_settings(body: SettingsUpdate):
    db.update_settings_batch(body.settings)
    return {"status": "ok"}
