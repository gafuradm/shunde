"""Объяснения «по наведению»: SSE-стрим и обычный JSON-ответ."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..db import db
from ..schemas import ExplainRequest
from ..services import llm
from ..services.explain import explain_stream

log = logging.getLogger("shunde.explain.api")
router = APIRouter(prefix="/api/explain", tags=["explain"])


@router.post("/stream")
async def explain_sse(req: ExplainRequest) -> StreamingResponse:
    """SSE: status → sources → delta → done. Используется всплывающей подсказкой."""

    async def generator():
        try:
            async for event in explain_stream(req):
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event['event']}\ndata: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("ошибка в пайплайне объяснений")
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("")
async def explain_once(req: ExplainRequest) -> Any:
    """Тот же пайплайн, но без стрима — удобно для мобильных клиентов и тестов."""
    answer = ""
    sources: list[dict] = []
    degraded = False
    try:
        async for event in explain_stream(req):
            if event["event"] == "sources":
                sources = event["data"].get("sources", [])
            elif event["event"] == "done":
                answer = event["data"].get("answer") or answer
                degraded = bool(event["data"].get("degraded"))
            elif event["event"] == "error":
                raise HTTPException(400, event["data"].get("message", "Explanation failed"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Could not produce an explanation: {exc}") from exc
    return {"answer": answer, "sources": sources, "degraded": degraded, "engine": "llm" if llm.available() else "sources-only"}


@router.get("/history")
async def explain_history(document_id: str | None = None, limit: int = 50) -> Any:
    if document_id:
        rows = db.query(
            "SELECT * FROM explanations WHERE document_id = ? ORDER BY created_at DESC LIMIT ?",
            (document_id, limit),
        )
    else:
        rows = db.query("SELECT * FROM explanations ORDER BY created_at DESC LIMIT ?", (limit,))
    return [
        {
            "id": row["id"], "quote": row["quote"], "mode": row["mode"], "ui_lang": row["ui_lang"],
            "level": row["level"], "answer": row["answer"], "sources": db.loads(row["sources"], []),
            "model": row["model"], "created_at": row["created_at"],
        }
        for row in rows
    ]
