#!/usr/bin/env python3
"""Проверка задеплоенного Shunde Tutor снаружи: HTTP + WebSocket + live-перевод.

Запуск:
    backend/.venv/bin/python scripts/smoke_deploy.py https://<домен> [teacher_token]
    backend/.venv/bin/python scripts/smoke_deploy.py    # возьмёт адрес из /tmp/shunde_public_url.txt

Скрипт создаёт временную комнату и документ и удаляет их в конце.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets

TOKEN = os.environ.get("TEACHER_TOKEN", "teacher")
PHRASE = "学而时习之，不亦说乎"
# websockets >= 15 читает системный прокси macOS через scutil: отключаем его.
WS_KW = {"proxy": None} if "proxy" in inspect.signature(websockets.connect).parameters else {}
# urllib на macOS тоже читает системный прокси: ходим напрямую.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{(' -> ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(label)
    return ok


def http(url: str, method: str = "GET", *, token: str | None = None,
         body: bytes | None = None, headers: dict[str, str] | None = None,
         timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Accept", "application/json")
    req.add_header("Connection", "close")
    if token:
        req.add_header("X-Teacher-Token", token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    last = ""
    for attempt in range(3):
        try:
            with OPENER.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(3 * (attempt + 1))
    return 0, last.encode()


def upload(base: str, filename: str, content: bytes, token: str) -> tuple[int, bytes]:
    boundary = "----shundeSmoke7f3a19"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'
    ).encode()
    body = head + content + f"\r\n--{boundary}--\r\n".encode()
    return http(f"{base}/api/documents", "POST", token=token, body=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def http_suite(base: str) -> dict[str, int | str]:
    """HTTP-часть: health, SPA-маршруты, статика, документы, загрузка."""
    info: dict[str, int | str] = {}
    print("1) HTTP")

    code, raw = http(f"{base}/api/health")
    try:
        health = json.loads(raw)
    except Exception:  # noqa: BLE001
        health = {}
    feats = health.get("features", {}) or {}
    info["llm"] = feats.get("llm")
    info["model"] = feats.get("llm_model")
    check("/api/health", code == 200 and health.get("status") == "ok",
          f"HTTP {code}, llm={feats.get('llm')}, model={feats.get('llm_model')}")

    for path in ("/", "/documents", "/lectures", "/live/ABCDEF"):
        code, _ = http(f"{base}{path}")
        check(f"SPA {path}", code == 200, f"HTTP {code}")

    code, html = http(f"{base}/")
    assets = sorted(set(re.findall(r"/assets/[A-Za-z0-9_.\-]+",
                                   html.decode("utf-8", "replace"))))
    check("статика в index.html", bool(assets), f"найдено {len(assets)}")
    for asset in assets:
        code, raw = http(f"{base}{asset}")
        check(f"GET {asset}", code == 200, f"HTTP {code}, {len(raw)} байт")

    code, raw = http(f"{base}/api/documents")
    docs = json.loads(raw) if code == 200 else []
    check("GET /api/documents", code == 200, f"HTTP {code}, документов {len(docs)}")

    print("2) загрузка документа")
    sample = (PHRASE + "。有朋自遠方來，不亦樂乎。\nSmoke test upload.\n").encode()
    code, raw = upload(base, "smoke_upload.txt", sample, TOKEN)
    doc_id = ""
    if code == 200:
        try:
            payload = json.loads(raw)
            doc_id = str(payload.get("id") or payload.get("doc_id") or "")
        except Exception:  # noqa: BLE001
            payload = {}
        check("POST /api/documents", bool(doc_id),
              f"id={doc_id} title={payload.get('title')}")
    else:
        check("POST /api/documents", False,
              f"HTTP {code} {raw[:120].decode('utf-8', 'replace')}")

    if doc_id:
        path = f"/api/documents/{urllib.parse.quote(doc_id, safe='')}"
        code, raw = http(f"{base}{path}")
        check("GET загруженного документа", code == 200,
              f"HTTP {code}, {len(raw)} байт")
        code, _ = http(f"{base}{path}", "DELETE", token=TOKEN)
        check("DELETE загруженного документа", code == 200, f"HTTP {code}")
    return info


async def ws_viewer(url: str, label: str) -> bool:
    """Подключение зрителя: ждём hello и отвечаем на ping."""
    try:
        async with websockets.connect(url, open_timeout=45, **WS_KW) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
            ok = hello.get("type") == "hello"
            await ws.send(json.dumps({"type": "ping"}))
            reply = ""
            for _ in range(5):  # после hello сервер шлёт историю строк, pong приходит позже
                raw = await asyncio.wait_for(ws.recv(), timeout=25)
                reply = json.loads(raw).get("type", "")
                if reply == "pong":
                    break
            ok &= reply == "pong"
            check(label, ok, f"role={hello.get('role')}, ответ на ping: {reply}")
            return ok
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"{type(exc).__name__}: {str(exc)[:90]}")
        return False


async def ws_rejected(url: str, label: str) -> bool:
    """Ведущий с неверным токеном обязан получить error / быть отключён."""
    try:
        async with websockets.connect(url, open_timeout=45, **WS_KW) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=25)
            ok = json.loads(raw).get("type") == "error"
            check(label, ok, str(raw)[:80])
            return ok
    except Exception as exc:  # noqa: BLE001
        check(label, True, f"соединение отклонено ({type(exc).__name__})")
        return True


async def ws_live(host: str, code: str) -> bool:
    """Ведущий говорит фразу — зритель получает строку и перевод."""
    viewer_url = f"wss://{host}/ws/lectures/{code}?role=viewer&name=Student"
    host_url = (f"wss://{host}/ws/lectures/{code}"
                f"?role=host&name=Teacher&token={urllib.parse.quote(TOKEN)}")
    viewer = await websockets.connect(viewer_url, open_timeout=45, **WS_KW)
    speaker = await websockets.connect(host_url, open_timeout=45, **WS_KW)
    try:
        await asyncio.wait_for(viewer.recv(), timeout=25)  # hello зрителя
        await asyncio.wait_for(speaker.recv(), timeout=25)  # hello ведущего
        await speaker.send(json.dumps({"type": "transcript", "text": PHRASE, "final": True}))
        got_line = False
        got_translation = ""
        deadline = asyncio.get_event_loop().time() + 180
        while asyncio.get_event_loop().time() < deadline:
            left = max(1.0, deadline - asyncio.get_event_loop().time())
            msg = json.loads(await asyncio.wait_for(viewer.recv(), timeout=left))
            kind = msg.get("type")
            if kind == "line":
                got_line = msg["line"].get("text") == PHRASE
                print(f"  строка зрителю: {msg['line'].get('text')}")
            elif kind == "translation":
                got_translation = str(msg.get("text") or "").strip()
                print(f"  перевод зрителю: {got_translation or '(пусто)'}")
                break
        check("зритель получил строку ведущего", got_line)
        check("live-перевод дошёл до зрителя", bool(got_translation),
              got_translation[:90])
        return got_line and bool(got_translation)
    except Exception as exc:  # noqa: BLE001
        check("live-перевод", False, f"{type(exc).__name__}: {str(exc)[:90]}")
        return False
    finally:
        await viewer.close()
        await speaker.close()


async def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "").strip().rstrip("/")
    if not base:
        try:
            with open("/tmp/shunde_public_url.txt", encoding="utf-8") as fh:
                base = fh.read().strip().rstrip("/")
        except OSError:
            pass
    if not base:
        print("укажите адрес: scripts/smoke_deploy.py https://<домен>")
        return 2
    host = urllib.parse.urlsplit(base).netloc or base.split("://", 1)[-1]
    print(f"Адрес: {base}\n")

    http_suite(base)

    print("3) WebSocket комнаты")
    code, raw = http(f"{base}/api/lectures", "POST", token=TOKEN,
                     body=json.dumps({"title": "smoke test"}).encode(),
                     headers={"Content-Type": "application/json"})
    if code != 200:
        check("POST /api/lectures", False, f"HTTP {code} {raw[:120].decode()}")
        return 1
    room = json.loads(raw)
    code_str = room.get("code", "")
    check("POST /api/lectures", bool(code_str), f"код комнаты {code_str}")

    await ws_viewer(f"wss://{host}/ws/lectures/{code_str}?role=viewer&name=Student",
                    "зритель подключается")
    await ws_viewer(f"wss://{host}/ws/lectures/{code_str}?role=host&name=T&token={TOKEN}",
                    "ведущий подключается")
    await ws_rejected(f"wss://{host}/ws/lectures/{code_str}?role=host&name=X&token=wrong",
                      "неверный токен отклонён")

    check("GET /api/lectures/{code}", http(f"{base}/api/lectures/{code_str}")[0] == 200)
    check("GET /api/lectures/{code}/lines",
          http(f"{base}/api/lectures/{code_str}/lines")[0] == 200)
    check("GET /api/lectures/{code}/export/markdown",
          http(f"{base}/api/lectures/{code_str}/export/markdown")[0] == 200)
    check("GET /api/lectures/{code}/export/srt",
          http(f"{base}/api/lectures/{code_str}/export/srt")[0] == 200)

    print("4) сквозной live-перевод")
    await ws_live(host, code_str)

    print("5) уборка")
    check("DELETE /api/lectures/{code}",
          http(f"{base}/api/lectures/{code_str}", "DELETE", token=TOKEN)[0] == 200)

    print()
    if FAILED:
        print(f"ИТОГ: ЕСТЬ ПРОБЛЕМЫ ({len(FAILED)}): " + ", ".join(FAILED))
        return 1
    print("ИТОГ: ВСЁ РАБОТАЕТ")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
