"""Хаб живых лекций: комнаты, рассылка субтитров, мгновенный перевод, релей аудио."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from ..db import db, new_id, now_iso
from . import translate
from .annotate import glossary_for

log = logging.getLogger("shunde.hub")

MAX_HISTORY = 200
PREVIEW_MIN_INTERVAL = 1.1  # сек между «быстрыми» переводами промежуточной речи


@dataclass
class Client:
    client_id: str
    ws: WebSocket
    role: str = "viewer"          # host | viewer
    name: str = ""
    target_lang: str | None = None  # личный язык перевода (опционально)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        async with self.send_lock:
            await self.ws.send_bytes(data)


@dataclass
class TranscriptItem:
    seq: int
    text: str
    final: bool
    line_id: str | None = None
    target_lang: str = "en"
    ts: float = field(default_factory=time.time)


class Room:
    def __init__(self, lecture: dict) -> None:
        self.code: str = lecture["code"]
        self.lecture_id: str = lecture["id"]
        self.title: str = lecture["title"]
        self.document_id: str | None = lecture.get("document_id")
        self.source_lang: str = lecture.get("source_lang") or "zh-CN"
        self.target_lang: str = lecture.get("target_lang") or "en"
        self.status: str = lecture.get("status") or "idle"
        self.clients: dict[str, Client] = {}
        self.host_present = False
        self.seq = 0
        self.partial: str = ""
        self.recent: deque[str] = deque(maxlen=6)     # контекст для переводчика
        self.queue: asyncio.Queue[TranscriptItem | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.last_preview_at = 0.0
        self.glossary: dict[str, str] = glossary_for(self.document_id)
        # аудио-релей
        self.audio_header: bytes | None = None
        self.audio_active = False
        self.audio_bytes = 0
        self.personal_cache: dict[tuple[str, str], str] = {}

    # -------------------------------------------------- служебное
    def counts(self) -> dict:
        viewers = sum(1 for c in self.clients.values() if c.role == "viewer")
        return {"viewers": viewers, "hosts": 1 if self.host_present else 0, "total": len(self.clients)}

    def state(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "status": self.status,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "document_id": self.document_id,
            "partial": self.partial,
            "audio": self.audio_active,
            **self.counts(),
        }

    async def broadcast(self, payload: dict, exclude: str | None = None, roles: set[str] | None = None) -> None:
        dead: list[str] = []
        for client in list(self.clients.values()):
            if exclude and client.client_id == exclude:
                continue
            if roles and client.role not in roles:
                continue
            try:
                await client.send(payload)
            except Exception:  # noqa: BLE001
                dead.append(client.client_id)
        for cid in dead:
            self.clients.pop(cid, None)

    async def broadcast_bytes(self, data: bytes, exclude: str | None = None) -> None:
        dead: list[str] = []
        for client in list(self.clients.values()):
            if client.client_id == exclude or client.role == "host":
                continue
            try:
                await client.send_bytes(data)
            except Exception:  # noqa: BLE001
                dead.append(client.client_id)
        for cid in dead:
            self.clients.pop(cid, None)

    async def send_history(self, client: Client) -> None:
        rows = db.query(
            "SELECT * FROM lecture_lines WHERE lecture_id = ? ORDER BY seq DESC LIMIT 80",
            (self.lecture_id,),
        )
        lines = [
            {
                "id": r["id"], "seq": r["seq"], "text": r["source_text"],
                "translation": r["translated"], "target_lang": r["target_lang"],
                "final": bool(r["is_final"]), "created_at": r["created_at"], "history": True,
            }
            for r in reversed(rows)
        ]
        await client.send({"type": "history", "lines": lines, "state": self.state()})

    # -------------------------------------------------- воркер перевода
    async def start(self) -> None:
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._worker(), name=f"translator-{self.code}")

    async def stop(self) -> None:
        if self.worker and not self.worker.done():
            await self.queue.put(None)
            try:
                await asyncio.wait_for(self.worker, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.worker.cancel()
        self.worker = None

    async def submit(self, item: TranscriptItem) -> None:
        await self.start()
        await self.queue.put(item)

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            if item.final:
                delay = 0.0
            else:
                delay = 0.45
            if delay:
                await asyncio.sleep(delay)
            # коалесцируем: берём самую свежую запись того же типа
            while not self.queue.empty():
                nxt = self.queue.get_nowait()
                if nxt is None:
                    return
                if nxt.final == item.final or (not item.final and not nxt.final):
                    item = nxt if nxt.seq >= item.seq else item
                else:
                    await self.queue.put(nxt)
                    break
            await self._translate_item(item)

    async def _translate_item(self, item: TranscriptItem) -> None:
        if not item.text.strip():
            return
        if not item.final:
            if time.time() - self.last_preview_at < PREVIEW_MIN_INTERVAL:
                return
            self.last_preview_at = time.time()
        payload = {
            "type": "translation_preview" if not item.final else "translation",
            "seq": item.seq,
            "line_id": item.line_id,
            "target": item.target_lang,
            "text": "",
            "pending": True,
        }
        if not item.final:
            await self.broadcast(payload)
        try:
            text = await translate.translate(
                item.text,
                target=item.target_lang,
                source=self.source_lang,
                context=list(self.recent),
                glossary=self.glossary,
                style="subtitle",
                max_tokens=1200 if not item.final else 1500,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("перевод не удался: %s", exc)
            text = ""
        payload["pending"] = False
        payload["text"] = text
        await self.broadcast(payload)
        if item.final and item.line_id and text:
            try:
                db.execute("UPDATE lecture_lines SET translated = ? WHERE id = ?", (text, item.line_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("не удалось сохранить перевод: %s", exc)

    # -------------------------------------------------- приём речи
    async def on_transcript(self, data: dict, client: Client) -> None:
        if client.role != "host":
            return
        await self.push_transcript(
            str(data.get("text") or ""),
            final=bool(data.get("final", True)),
            target=str(data.get("target") or self.target_lang),
        )

    async def push_transcript(self, text: str, final: bool = True, target: str | None = None) -> None:
        """Публикует распознанную речь (из браузера или серверного ASR)."""
        text = (text or "").strip()
        if not text:
            return
        target = target or self.target_lang
        self.seq += 1
        seq = self.seq

        if not final:
            self.partial = text
            await self.broadcast({"type": "partial", "seq": seq, "text": text, "lang": self.source_lang})
            await self.submit(TranscriptItem(seq=seq, text=text, final=False, target_lang=target))
            return

        line_id = new_id("line_")
        created = now_iso()
        try:
            db.insert("lecture_lines", {
                "id": line_id, "lecture_id": self.lecture_id, "seq": seq,
                "source_text": text, "source_lang": self.source_lang,
                "translated": None, "target_lang": target, "is_final": 1,
                "created_at": created,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось записать строку транскрипта: %s", exc)
        self.partial = ""
        self.recent.append(text)
        await self.broadcast({
            "type": "line",
            "line": {"id": line_id, "seq": seq, "text": text, "translation": None,
                     "target_lang": target, "final": True, "created_at": created},
        })
        await self.submit(TranscriptItem(seq=seq, text=text, final=True, line_id=line_id, target_lang=target))

    async def on_personal_translate(self, data: dict, client: Client) -> None:
        text = str(data.get("text") or "").strip()
        target = str(data.get("target") or "en")
        if not text:
            return
        key = (text[:400], target)
        cached = self.personal_cache.get(key)
        if cached:
            await client.send({"type": "personal_translation", "line_id": data.get("line_id"), "target": target, "text": cached})
            return
        try:
            out = await translate.translate(
                text, target=target, source=self.source_lang, context=list(self.recent),
                glossary=self.glossary, style="subtitle", max_tokens=1500,
            )
        except Exception as exc:  # noqa: BLE001
            out = ""
            log.warning("личный перевод не удался: %s", exc)
        if out:
            if len(self.personal_cache) > 500:
                self.personal_cache.clear()
            self.personal_cache[key] = out
        await client.send({
            "type": "personal_translation", "line_id": data.get("line_id"),
            "target": target, "text": out,
        })


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code)

    def get_or_create(self, lecture: dict) -> Room:
        room = self.rooms.get(lecture["code"])
        if room is None:
            room = Room(lecture)
            self.rooms[lecture["code"]] = room
        else:
            room.title = lecture["title"]
            room.document_id = lecture.get("document_id")
            room.source_lang = lecture.get("source_lang") or room.source_lang
            room.target_lang = lecture.get("target_lang") or room.target_lang
        return room

    async def close(self) -> None:
        for room in list(self.rooms.values()):
            await room.stop()
        self.rooms.clear()


hub = Hub()


async def attach(room: Room, client: Client, resume_lang: str | None = None) -> None:
    """Регистрирует клиента в комнате и отдаёт ему состояние."""
    room.clients[client.client_id] = client
    if client.role == "host":
        room.host_present = True
    await room.start()
    await client.send({"type": "hello", "client_id": client.client_id, "role": client.role, "state": room.state()})
    await room.send_history(client)
    if room.audio_active and room.audio_header and client.role == "viewer":
        try:
            await client.send_bytes(room.audio_header)
        except Exception:  # noqa: BLE001
            pass
    await room.broadcast({"type": "participants", **room.counts()})


async def detach(room: Room, client: Client) -> None:
    room.clients.pop(client.client_id, None)
    if client.role == "host":
        room.host_present = any(c.role == "host" for c in room.clients.values())
        if not room.host_present:
            room.audio_active = False
            room.audio_header = None
            await room.broadcast({"type": "host_left", "message": "The teacher has disconnected"})
    await room.broadcast({"type": "participants", **room.counts()})
    if not room.clients:
        await room.stop()


def serialize_line(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"], "seq": row["seq"], "text": row["source_text"],
        "translation": row["translated"], "target_lang": row["target_lang"],
        "final": bool(row["is_final"]), "created_at": row["created_at"],
    }
