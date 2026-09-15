"""Живые лекции: создание комнаты, WebSocket-вещание, серверный ASR, экспорт."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response

from ..config import get_settings
from ..db import db, new_id, now_iso
from ..deps import require_teacher
from ..schemas import LectureCreate
from ..services.annotate import glossary_for
from ..services.hub import Client, attach, detach, hub

log = logging.getLogger("shunde.lectures")
router = APIRouter(prefix="/api/lectures", tags=["lectures"])
ws_router = APIRouter(tags=["live"])

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen_code() -> str:
    for _ in range(50):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not db.one("SELECT 1 AS x FROM lectures WHERE code = ?", (code,)):
            return code
    raise HTTPException(500, "Could not generate a room code")


def _get_lecture(code: str) -> dict:
    row = db.one("SELECT * FROM lectures WHERE code = ?", (code.upper(),))
    if not row:
        raise HTTPException(404, "Lecture not found")
    return row


# ------------------------------------------------------------------ CRUD
@router.get("")
async def list_lectures() -> Any:
    return db.query("SELECT * FROM lectures ORDER BY created_at DESC LIMIT 100")


@router.post("")
async def create_lecture(payload: LectureCreate, _: None = Depends(require_teacher)) -> Any:
    lecture_id = new_id("lec_")
    code = _gen_code()
    title = (payload.title or "").strip()
    if not title and payload.document_id:
        doc = db.one("SELECT title FROM documents WHERE id = ?", (payload.document_id,))
        title = doc["title"] if doc else ""
    db.insert("lectures", {
        "id": lecture_id, "code": code, "title": title or f"Lecture {code}",
        "document_id": payload.document_id, "target_lang": payload.target_lang,
        "source_lang": payload.source_lang, "status": "idle",
        "created_at": now_iso(), "started_at": None, "ended_at": None,
    })
    row = db.one("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    hub.get_or_create(row)
    return row


@router.get("/{code}")
async def get_lecture(code: str) -> Any:
    lecture = _get_lecture(code)
    room = hub.get(lecture["code"])
    count = db.one("SELECT COUNT(*) AS c FROM lecture_lines WHERE lecture_id = ?", (lecture["id"],)) or {"c": 0}
    return {**lecture, "participants": room.counts() if room else {"viewers": 0, "hosts": 0, "total": 0}, "lines": int(count["c"])}


@router.post("/{code}/start")
async def start_lecture(code: str, _: None = Depends(require_teacher)) -> Any:
    lecture = _get_lecture(code)
    db.update("lectures", lecture["id"], {"status": "live", "started_at": now_iso(), "ended_at": None})
    room = hub.get_or_create(db.one("SELECT * FROM lectures WHERE id = ?", (lecture["id"],)))
    room.status = "live"
    await room.broadcast({"type": "status", "status": "live", "state": room.state()})
    return {"status": "live", "code": lecture["code"]}


@router.post("/{code}/end")
async def end_lecture(code: str, _: None = Depends(require_teacher)) -> Any:
    lecture = _get_lecture(code)
    db.update("lectures", lecture["id"], {"status": "ended", "ended_at": now_iso()})
    room = hub.get_or_create(db.one("SELECT * FROM lectures WHERE id = ?", (lecture["id"],)))
    room.status = "ended"
    room.audio_active = False
    await room.broadcast({"type": "status", "status": "ended", "state": room.state()})
    return {"status": "ended", "code": lecture["code"]}


@router.delete("/{code}")
async def delete_lecture(code: str, _: None = Depends(require_teacher)) -> Any:
    lecture = _get_lecture(code)
    room = hub.get(lecture["code"])
    if room:
        await room.broadcast({"type": "status", "status": "closed"})
        await room.stop()
        hub.rooms.pop(lecture["code"], None)
    db.execute("DELETE FROM lecture_lines WHERE lecture_id = ?", (lecture["id"],))
    db.execute("DELETE FROM lectures WHERE id = ?", (lecture["id"],))
    return {"deleted": lecture["code"]}


@router.get("/{code}/lines")
async def lecture_lines(code: str, limit: int = 500) -> Any:
    lecture = _get_lecture(code)
    return db.query(
        "SELECT * FROM lecture_lines WHERE lecture_id = ? ORDER BY seq LIMIT ?",
        (lecture["id"], limit),
    )


@router.post("/{code}/config")
async def set_config(code: str, payload: dict, _: None = Depends(require_teacher)) -> Any:
    lecture = _get_lecture(code)
    data: dict[str, Any] = {}
    for key in ("target_lang", "source_lang", "title"):
        if payload.get(key):
            data[key] = str(payload[key])[:40]
    if data:
        db.update("lectures", lecture["id"], data)
    room = hub.get_or_create(db.one("SELECT * FROM lectures WHERE id = ?", (lecture["id"],)))
    if data.get("target_lang"):
        room.target_lang = data["target_lang"]
    if data.get("source_lang"):
        room.source_lang = data["source_lang"]
    await room.broadcast({"type": "config", "state": room.state()})
    return room.state()


# ------------------------------------------------------------------ серверный ASR
@router.post("/{code}/transcribe")
async def transcribe(code: str, file: UploadFile = File(...), _: None = Depends(require_teacher)) -> Any:
    settings = get_settings()
    if not settings.asr_available:
        raise HTTPException(400, "Server-side ASR is not configured (ASR_API_KEY in backend/.env)")
    lecture = _get_lecture(code)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty audio chunk")

    room = hub.get_or_create(lecture)
    lang = (room.source_lang or "zh-CN").split("-")[0]
    terms = list(glossary_for(lecture.get("document_id")).keys())[:30]
    prompt = (lecture["title"] + "。" + "、".join(terms))[:900] if terms else lecture["title"]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(
                settings.asr_base_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.asr_api_key}"},
                files={"file": (file.filename or "chunk.webm", data, file.content_type or "audio/webm")},
                data={
                    "model": settings.asr_model,
                    "language": lang,
                    "prompt": prompt,
                    "response_format": "json",
                    "temperature": "0",
                },
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ASR error: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(502, f"ASR returned {resp.status_code}: {resp.text[:200]}")

    text = (resp.json().get("text") or "").strip()
    if text:
        await room.push_transcript(text, final=True)
    return {"text": text}


# ------------------------------------------------------------------ экспорт
@router.get("/{code}/export/srt")
async def export_srt(code: str) -> Response:
    lecture = _get_lecture(code)
    lines = db.query(
        "SELECT * FROM lecture_lines WHERE lecture_id = ? ORDER BY seq", (lecture["id"],)
    )
    start_dt = _parse_dt(lecture.get("started_at")) or _parse_dt(lecture["created_at"])
    blocks: list[str] = []

    def stamp(seconds: float) -> str:
        ms = int(seconds * 1000)
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    prev = start_dt
    for i, row in enumerate(lines, start=1):
        current = _parse_dt(row["created_at"]) or start_dt
        begin = max(0.0, (prev - start_dt).total_seconds()) if prev else 0.0
        end = max(0.0, (current - start_dt).total_seconds()) if current else begin + 4
        if end <= begin:
            end = begin + 3.0
        body = row["source_text"]
        if row.get("translated"):
            body += "\n" + row["translated"]
        blocks.append(f"{i}\n{stamp(begin)} --> {stamp(end)}\n{body}\n")
        prev = current
    content = "\n".join(blocks) or "1\n00:00:00,000 --> 00:00:03,000\n(empty)\n"
    return Response(
        content,
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{lecture["code"]}.srt"'},
    )


@router.get("/{code}/export/markdown", response_class=PlainTextResponse)
async def export_markdown(code: str) -> Any:
    lecture = _get_lecture(code)
    lines = db.query(
        "SELECT * FROM lecture_lines WHERE lecture_id = ? ORDER BY seq", (lecture["id"],)
    )
    out = [f"# {lecture['title']}\n", f"Room code: `{lecture['code']}`\n",
           f"Language: {lecture['source_lang']} → {lecture['target_lang']}\n", "\n---\n"]
    for row in lines:
        out.append(f"\n**{row['source_text']}**\n")
        if row.get("translated"):
            out.append(f"\n{row['translated']}\n")
    return PlainTextResponse("".join(out))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ WebSocket
async def _handle_message(room, client: Client, payload: dict) -> None:
    kind = payload.get("type")
    if kind == "transcript":
        await room.on_transcript(payload, client)
    elif kind == "translate_request":
        await room.on_personal_translate(payload, client)
    elif kind == "set_config" and client.role == "host":
        if payload.get("target_lang"):
            room.target_lang = str(payload["target_lang"])[:12]
            db.update("lectures", room.lecture_id, {"target_lang": room.target_lang})
        if payload.get("source_lang"):
            room.source_lang = str(payload["source_lang"])[:12]
            db.update("lectures", room.lecture_id, {"source_lang": room.source_lang})
        await room.broadcast({"type": "config", "state": room.state()})
    elif kind == "audio_state" and client.role == "host":
        room.audio_active = bool(payload.get("active"))
        if not room.audio_active:
            room.audio_header = None
        await room.broadcast({"type": "audio_state", "active": room.audio_active})
    elif kind == "audio_request" and client.role == "viewer":
        if room.audio_header:
            try:
                await client.send_bytes(room.audio_header)
            except Exception:  # noqa: BLE001
                pass
    elif kind == "ping":
        await client.send({"type": "pong"})
    elif kind == "rename":
        client.name = str(payload.get("name") or "")[:40]
        await room.broadcast({"type": "participants", **room.counts()})


# Роли, которые трактуются как ведущий (преподаватель). Фронтенд/документация
# могут использовать любое из этих имён — каноническое имя в API: «host».
_HOST_ROLES = {"host", "teacher", "presenter", "tutor"}


@ws_router.websocket("/ws/lectures/{code}")
async def lecture_socket(
    websocket: WebSocket,
    code: str,
    role: str = "viewer",
    token: str = "",
    name: str = "",
) -> None:
    settings = get_settings()
    is_host = (role or "").strip().lower() in _HOST_ROLES
    await websocket.accept()
    lecture = db.one("SELECT * FROM lectures WHERE code = ?", (code.upper(),))
    if not lecture:
        await websocket.send_json({"type": "error", "message": "Lecture not found"})
        await websocket.close(code=4404)
        return
    if is_host and token.strip() != settings.teacher_token:
        await websocket.send_json({"type": "error", "message": "Invalid teacher token"})
        await websocket.close(code=4403)
        return

    room = hub.get_or_create(lecture)
    client = Client(
        client_id=new_id("cl_"), ws=websocket,
        role="host" if is_host else "viewer", name=name[:40],
    )
    await attach(room, client)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                data = message["bytes"]
                if client.role == "host" and data:
                    if room.audio_header is None:
                        room.audio_header = data
                        room.audio_active = True
                        await room.broadcast({"type": "audio_state", "active": True})
                    room.audio_bytes += len(data)
                    await room.broadcast_bytes(data, exclude=client.client_id)
                continue
            raw = message.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                await _handle_message(room, client, payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("websocket %s завершился с ошибкой: %s", code, exc)
    finally:
        await detach(room, client)
