"""Зависимости FastAPI (простая авторизация преподавателя по токену)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from .config import get_settings


def require_teacher(x_teacher_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if (x_teacher_token or "").strip() != settings.teacher_token:
        raise HTTPException(status_code=401, detail="Invalid teacher token (X-Teacher-Token header)")
