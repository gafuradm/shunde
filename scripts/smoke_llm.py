"""Сквозная проверка ИИ-функций на живом адресе (локальном или задеплоенном).

Сценарий:
  1. загрузка документа (文言文 + английский текст);
  2. запуск ИИ-разметки (POST /api/documents/{id}/annotations/ai) и ожидание результата;
  3. объяснение выделенного фрагмента (POST /api/explain) — как это делает студент при наведении;
  4. удаление документа.

Запуск:
    backend/.venv/bin/python scripts/smoke_llm.py https://shunde-tutor.onrender.com
    backend/.venv/bin/python scripts/smoke_llm.py http://127.0.0.1:8000

Токен преподавателя берётся из TEACHER_TOKEN (по умолчанию `teacher`).
Зависимостей нет — только стандартная библиотека.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("TEACHER_TOKEN", "teacher")

TEXT = (
    "子曰：「学而时习之，不亦说乎？有朋自远方来，不亦乐乎？人不知而不愠，不亦君子乎？」\n"
    "曾子曰：「吾日三省吾身：为人谋而不忠乎？与朋友交而不信乎？传不习乎？」\n"
    "The Analects (论语) is a collection of sayings attributed to Confucius and his disciples.\n"
)


def req(method, path, data=None, token=False, timeout=240):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Teacher-Token"] = TOKEN
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw.decode()) if raw else None)


def upload(path, filename, content, fields=None):
    boundary = "----shunde" + uuid.uuid4().hex
    parts = []
    for k, v in (fields or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n".encode()
    )
    parts.append(content.encode("utf-8"))
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    r = urllib.request.Request(
        BASE + path,
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(r, timeout=240) as resp:
        return resp.status, json.loads(resp.read().decode())


def items_of(payload):
    if isinstance(payload, dict):
        return payload.get("items") or payload.get("annotations") or []
    return payload or []


def main() -> int:
    ok = True
    doc_id = None
    print(f"Адрес: {BASE}")

    status, health = req("GET", "/api/health")
    feats = (health or {}).get("features", {})
    print(f"1) /api/health -> HTTP {status}, llm={feats.get('llm')}, model={feats.get('llm_model')}")
    if not feats.get("llm"):
        print("   ВНИМАНИЕ: LLM не сконфигурирован — объяснения будут в деградированном режиме")
    ok &= status == 200

    try:
        status, doc = upload("/api/documents", "lunyu.txt", TEXT, {"title": "smoke_llm_文言文"})
        doc_id = (doc or {}).get("id")
        print(f"2) POST /api/documents -> HTTP {status}, id={doc_id}")
        ok &= status in (200, 201) and bool(doc_id)

        status, _ = req("POST", f"/api/documents/{doc_id}/annotations/ai", {"mode": "quick"}, token=True)
        print(f"3) POST .../annotations/ai -> HTTP {status}")
        ok &= status in (200, 202)

        anns = []
        for i in range(30):
            _, st = req("GET", f"/api/documents/{doc_id}/status")
            _, anns = req("GET", f"/api/documents/{doc_id}/annotations")
            anns = items_of(anns)
            state = (st or {}).get("annotation_status") or (st or {}).get("status") or "?"
            print(f"   [{i}] статус={state}, аннотаций={len(anns)}")
            if anns and state in ("annotated", "ready", "done", "idle", "ok"):
                break
            if state in ("failed", "error"):
                break
            time.sleep(6)
        ok &= len(anns) > 0

        kinds: dict[str, int] = {}
        quote = None
        for a in anns:
            kinds[a.get("kind")] = kinds.get(a.get("kind"), 0) + 1
            if not quote and (a.get("quote") or "").strip():
                quote = a["quote"].strip()
        print(f"4) разметка: {len(anns)} шт, типы={kinds}")

        if quote:
            t0 = time.time()
            status, exp = req("POST", "/api/explain", {
                "document_id": doc_id, "quote": quote, "mode": "explain",
                "level": "standard", "ui_lang": "ru",
            })
            answer = (exp or {}).get("answer") or ""
            srcs = (exp or {}).get("sources") or []
            print(f"5) POST /api/explain «{quote}» -> HTTP {status} за {time.time()-t0:.1f} с")
            print(f"   engine={((exp or {}).get('engine'))}, degraded={(exp or {}).get('degraded')}, "
                  f"ответ {len(answer)} симв., источников {len(srcs)}")
            print("   " + " ".join(answer.split())[:300])
            ok &= status == 200 and bool(answer) and (exp or {}).get("degraded") is False
        else:
            ok = False
            print("5) объяснение пропущено: ИИ не разметил ни одного фрагмента")
    finally:
        if doc_id:
            try:
                status, _ = req("DELETE", f"/api/documents/{doc_id}", token=True)
                print(f"6) DELETE /api/documents/{doc_id} -> HTTP {status}")
            except Exception as exc:  # noqa: BLE001
                print(f"6) удаление не удалось: {exc}")

    print("\nИТОГ:", "ИИ-СЦЕНАРИЙ РАБОТАЕТ" if ok else "ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
