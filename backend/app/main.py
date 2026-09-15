"""Shunde Tutor — backend приложения для вузовского курса.

Возможности:
  * загрузка документов любого формата (PDF/DOCX/PPTX/XLSX/EPUB/ODT/RTF/HTML/OCR/…);
  * ИИ-разметка важных понятий, фактов и непонятных мест (в т.ч. вэньянь);
  * объяснение выделенного фрагмента «по наведению» с поиском в интернете (SSE-стрим);
  * живые лекции: студенты подключаются по коду, субтитры и мгновенный перевод в реальном времени.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import get_settings
from .db import db
from .routers import documents, explain, lectures
from .services import hub as hub_module
from .services import llm, search, translate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("shunde")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init()
    log.info("База данных: %s", settings.db_path)
    log.info("Данные: %s", settings.data_path)
    asyncio.create_task(llm.warmup())
    try:
        yield
    finally:
        await hub_module.hub.close()
        await asyncio.gather(
            llm.aclose(), search.aclose(), translate.aclose(), return_exceptions=True
        )


settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list or ["*"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(documents.ann_router)
app.include_router(explain.router)
app.include_router(lectures.router)
app.include_router(lectures.ws_router)


@app.get("/api/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "app": s.app_name,
        "features": {
            "llm": s.llm_available,
            "llm_model": s.llm_model if s.llm_available else None,
            "llm_base_url": s.llm_base_url if s.llm_available else None,
            "vision_ocr": s.vision_available,
            "server_asr": s.asr_available,
            "web_search": {
                "wikipedia": True,
                "duckduckgo": True,
                "mojeek": True,
                "bing_html": True,
                "tavily": bool(s.tavily_api_key),
                "serper": bool(s.serper_api_key),
                "bing": bool(s.bing_search_key),
            },
            "free_translate_fallback": s.allow_google_free_translate,
            "teacher_token_required": True,
        },
    }


# ---------------------------------------------------------------- SPA (frontend/dist)
if FRONTEND_DIST.exists():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(404, "Not found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(FRONTEND_DIST)):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "app": settings.app_name,
            "docs": "/docs",
            "hint": "Build the frontend (cd frontend && npm run dev) — the API lives under /api/*",
        }
