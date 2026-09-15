"""Загрузка документов, ручная и ИИ-разметка, экспорт."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from ..config import get_settings
from ..db import db, new_id, now_iso
from ..schemas import (
    AiAnnotateRequest,
    AnnotationCreate,
    AnnotationOut,
    AnnotationPatch,
    DocumentBrief,
    DocumentFull,
)
from ..services import llm
from ..services.annotate import KINDS, annotate_document, annotations_for
from ..services.extract import ExtractError, extract_text
from ..services.text import clip_quote, detect_language

log = logging.getLogger("shunde.docs")
router = APIRouter(prefix="/api/documents", tags=["documents"])
ann_router = APIRouter(prefix="/api/annotations", tags=["annotations"])


# ------------------------------------------------------------------ helpers
def _doc_row_to_full(row: dict) -> dict:
    meta = db.loads(row.get("meta"), {}) or {}
    count = db.one("SELECT COUNT(*) AS c FROM annotations WHERE document_id = ?", (row["id"],)) or {"c": 0}
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "ext": row.get("ext"),
        "language": row.get("language"),
        "script": row.get("script"),
        "classical": float(row.get("classical") or 0),
        "char_count": int(row.get("char_count") or 0),
        "status": row.get("status") or "uploaded",
        "error": row.get("error"),
        "annotation_count": int(count["c"]),
        "created_at": row["created_at"],
        "text": row.get("text") or "",
        "progress": float(meta.get("progress") or (1.0 if row.get("status") == "annotated" else 0.0)),
        "stage": meta.get("stage"),
        "engine": meta.get("engine"),
        "glossary": meta.get("glossary") or {},
    }


async def _extract_with_fallback(data: bytes, filename: str, title_hint: str) -> tuple[str, dict]:
    """Извлекает текст; для картинок без OCR пробует vision-LLM."""
    settings = get_settings()
    result = await asyncio.to_thread(extract_text, data, filename)
    text = result.text
    warnings = list(result.warnings)
    if not text.strip() and settings.vision_available and (result.extractor == "image"):
        try:
            text = await llm.chat_vision(
                "Extract ALL text from this image verbatim (preserve line breaks, Chinese characters, "
                f"punctuation and numbers). If the image contains a diagram or table, transcribe it as text. Context: {title_hint}",
                data,
                mime="image/png",
                max_tokens=3000,
            )
            warnings.append("text recognised by the vision model")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"vision OCR failed: {exc}")
    return text, {
        "extractor": result.extractor,
        "encoding": result.encoding,
        "warnings": warnings,
        "meta": result.meta,
    }


# ------------------------------------------------------------------ документы
@router.post("", response_model=DocumentFull)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    annotate: bool = Form(False),
    mode: str = Form("quick"),
) -> Any:
    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")

    filename = file.filename or "document"
    ext = os.path.splitext(filename)[1].lower()
    doc_id = new_id("doc_")

    try:
        text, info = await _extract_with_fallback(data, filename, title or filename)
    except ExtractError as exc:
        raise HTTPException(422, str(exc)) from exc

    text = (text or "").strip()
    if not text:
        warnings = "; ".join(info.get("warnings") or [])
        raise HTTPException(422, f"Could not extract any text from the file. {warnings}".strip())

    lang = detect_language(text)
    doc_title = (title or "").strip() or os.path.splitext(filename)[0][:200]

    try:
        with open(settings.upload_path / f"{doc_id}{ext}", "wb") as fh:
            fh.write(data)
    except OSError as exc:
        log.warning("не удалось сохранить исходный файл: %s", exc)

    db.insert("documents", {
        "id": doc_id,
        "title": doc_title,
        "filename": filename,
        "ext": ext,
        "mime": file.content_type or "",
        "size": len(data),
        "language": lang.language,
        "script": lang.script,
        "classical": lang.classical,
        "text": text,
        "char_count": len(text),
        "status": "extracted",
        "error": None,
        "meta": db.dumps({
            "extractor": info["extractor"],
            "encoding": info["encoding"],
            "warnings": info["warnings"],
            "progress": 0.0,
            "stage": "Text extracted",
        }),
        "created_at": now_iso(),
    })

    if annotate:
        background.add_task(annotate_document, doc_id, {"mode": mode, "replace_existing": True})

    row = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    return _doc_row_to_full(row)


@router.get("", response_model=list[DocumentBrief])
async def list_documents() -> Any:
    rows = db.query("SELECT * FROM documents ORDER BY created_at DESC LIMIT 200")
    return [_doc_row_to_full(r) for r in rows]


@router.get("/{doc_id}", response_model=DocumentFull)
async def get_document(doc_id: str) -> Any:
    row = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    return _doc_row_to_full(row)


@router.get("/{doc_id}/status")
async def document_status(doc_id: str) -> Any:
    row = db.one("SELECT status, error, meta FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    meta = db.loads(row["meta"], {}) or {}
    count = db.one("SELECT COUNT(*) AS c FROM annotations WHERE document_id = ?", (doc_id,)) or {"c": 0}
    return {
        "status": row["status"],
        "error": row["error"],
        "progress": float(meta.get("progress") or 0.0),
        "stage": meta.get("stage"),
        "engine": meta.get("engine"),
        "annotations": int(count["c"]),
    }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str) -> Any:
    db.execute("DELETE FROM annotations WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return {"deleted": doc_id}


@router.get("/{doc_id}/text", response_class=PlainTextResponse)
async def get_plain_text(doc_id: str) -> Any:
    row = db.one("SELECT text FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    return PlainTextResponse(row["text"] or "")


# ------------------------------------------------------------------ разметка
@router.get("/{doc_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(doc_id: str) -> Any:
    return annotations_for(doc_id)


@router.post("/{doc_id}/annotations", response_model=AnnotationOut)
async def create_annotation(doc_id: str, payload: AnnotationCreate) -> Any:
    row = db.one("SELECT text FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    text: str = row["text"] or ""
    start = max(0, min(payload.start_char, len(text)))
    end = max(start, min(payload.end_char, len(text)))
    if end - start < 1:
        raise HTTPException(400, "Invalid selection range")
    quote = payload.quote or clip_quote(text, start, end)
    ann_id = new_id("ann_")
    db.insert("annotations", {
        "id": ann_id, "document_id": doc_id, "start_char": start, "end_char": end,
        "quote": quote, "kind": payload.kind, "title": payload.title or quote,
        "rationale": payload.rationale or "Marked by the teacher",
        "difficulty": payload.difficulty, "created_by": "teacher",
        "meta": db.dumps({}), "created_at": now_iso(),
    })
    if row_ann := db.one("SELECT * FROM annotations WHERE id = ?", (ann_id,)):
        return row_ann
    raise HTTPException(500, "Could not save the annotation")


@router.post("/{doc_id}/annotations/ai")
async def ai_annotate(doc_id: str, payload: AiAnnotateRequest, background: BackgroundTasks) -> Any:
    row = db.one("SELECT id, status FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    if row["status"] == "annotating":
        raise HTTPException(409, "Markup is already running")
    db.update("documents", doc_id, {"status": "annotating", "error": None})
    options = payload.model_dump(exclude_none=True)
    background.add_task(annotate_document, doc_id, options)
    return {
        "status": "started",
        "engine": "llm" if llm.available() else "heuristic",
        "kinds": payload.kinds or KINDS,
    }


@router.delete("/{doc_id}/annotations")
async def clear_annotations(doc_id: str, only_ai: bool = False) -> Any:
    if only_ai:
        cur = db.execute("DELETE FROM annotations WHERE document_id = ? AND created_by = 'ai'", (doc_id,))
    else:
        cur = db.execute("DELETE FROM annotations WHERE document_id = ?", (doc_id,))
    return {"deleted": cur.rowcount}


@router.get("/{doc_id}/export", response_class=PlainTextResponse)
async def export_markdown(doc_id: str) -> Any:
    row = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not row:
        raise HTTPException(404, "Document not found")
    text: str = row["text"] or ""
    anns = sorted(annotations_for(doc_id), key=lambda a: a["start_char"])

    def render() -> str:
        out: list[str] = []
        cursor = 0
        for i, ann in enumerate(anns, start=1):
            if ann["start_char"] < cursor:
                continue
            out.append(text[cursor:ann["start_char"]])
            out.append(f"=={text[ann['start_char']:ann['end_char']]}==[{i}]")
            cursor = ann["end_char"]
        out.append(text[cursor:])
        return "".join(out)

    header = (
        f"# {row['title']}\n\n"
        f"*Source: {row['filename']} · language: {row['language']} · "
        f"classical score: {float(row['classical'] or 0):.2f}*\n\n---\n\n"
    )
    glossary = ["\n\n---\n\n## Markup / Glossary\n"]
    for i, ann in enumerate(anns, start=1):
        glossary.append(
            f"\n**[{i}] {ann['quote']}** ({ann['kind']}, difficulty {ann['difficulty']}/5)\n"
            + (f"- {ann['title']}\n" if ann.get("title") else "")
            + (f"- {ann['rationale']}\n" if ann.get("rationale") else "")
            + (f"- EN: {ann['en']}\n" if ann.get("en") else "")
        )
    return PlainTextResponse(header + render() + "".join(glossary))


# ------------------------------------------------------------------ отдельные аннотации
@ann_router.patch("/{ann_id}", response_model=AnnotationOut)
async def patch_annotation(ann_id: str, payload: AnnotationPatch) -> Any:
    row = db.one("SELECT * FROM annotations WHERE id = ?", (ann_id,))
    if not row:
        raise HTTPException(404, "Annotation not found")
    data = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if data:
        db.update("annotations", ann_id, data)
    return db.one("SELECT * FROM annotations WHERE id = ?", (ann_id,))


@ann_router.delete("/{ann_id}")
async def delete_annotation(ann_id: str) -> Any:
    db.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
    return {"deleted": ann_id}
