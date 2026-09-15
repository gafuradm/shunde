"""Пайплайн объяснений: наведение курсора → поиск в интернете → синтез объяснения (стрим).

Возвращает асинхронный генератор событий: status / sources / delta / done.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, AsyncGenerator

from ..config import get_settings
from ..db import db, new_id, now_iso
from ..schemas import ExplainRequest
from . import llm, search
from .annotate import glossary_for
from .text import context_window

log = logging.getLogger("shunde.explain")

HEADINGS = {
    "ru": {
        "short": "Коротко",
        "context": "В контексте отрывка",
        "why": "Почему это важно",
        "mistake": "Частая ошибка / что упускают",
        "sources": "Источники",
    },
    "en": {
        "short": "In short",
        "context": "In this passage",
        "why": "Why it matters",
        "mistake": "Common misunderstanding",
        "sources": "Sources",
    },
    "zh": {
        "short": "简要说明",
        "context": "在本文语境中",
        "why": "为什么重要",
        "mistake": "常见误解",
        "sources": "参考来源",
    },
}

LANG_NAMES = {
    "ru": "Russian", "en": "English", "zh": "Simplified Chinese",
    "zh-classical": "Classical Chinese", "ja": "Japanese", "ko": "Korean",
}

MODE_TASKS = {
    "explain": "Explain the highlighted span so the student truly understands it.",
    "simplify": "Rewrite the highlighted span in simpler language and then explain it in one or two sentences.",
    "translate": "Translate the highlighted span into {ui_language} and explain the translation choices for any tricky part.",
    "example": "Explain the highlighted span and give 1-3 concrete examples or analogies that make it click.",
}

LEVEL_TASKS = {
    "simple": "Audience: a first-year student. Use short sentences and everyday language, define every technical word the first time you use it.",
    "standard": "Audience: an undergraduate who knows the basics of the discipline. Be precise and concise.",
    "advanced": "Audience: a strong graduate student. Add nuances, edge cases, scholarly debate and precise terminology.",
}


def cache_key(req: ExplainRequest, quote: str, context: str) -> str:
    raw = "|".join([req.document_id or "", quote, (context or "")[:400], req.mode, req.ui_lang, req.level])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _sse(event: str, data: Any) -> dict:
    return {"event": event, "data": data}


def _fmt_sources(hits: list[search.SearchHit]) -> list[dict]:
    return [search.hit_to_source(h) for h in hits]


_PUNCT_SPLIT = re.compile(r"[\s，。、；：？！（）()\[\]「」『』“”\"'·…—\-]+")
# Служебные слова вэньяня, которые мешают поисковому запросу
_TRIM_HEAD = ("子曰", "不亦", "若夫", "今夫", "是以", "是故", "盖", "夫", "其", "故")
_TRIM_TAIL = ("而已", "也", "矣", "焉", "哉", "乎", "者", "耳", "兮", "耶", "邪", "云")


def _cjk_core(quote: str) -> str:
    """Ключевое ядро китайской цитаты — самая длинная часть без служебных частиц.

    «学而时习之，不亦说乎？» → «学而时习»: именно такое ядро даёт релевантные
    результаты в Wikipedia и поисковиках (без LLM мы не можем переформулировать).
    """
    parts = [p for p in _PUNCT_SPLIT.split(quote) if p]
    if not parts:
        return quote.strip()
    longest = max(parts, key=len)
    core = longest
    for head in _TRIM_HEAD:
        if core.startswith(head) and len(core) - len(head) >= 2:
            core = core[len(head):]
            break
    changed = True
    while changed:
        changed = False
        for tail in _TRIM_TAIL:
            if core.endswith(tail) and len(core) - len(tail) >= 2:
                core = core[: -len(tail)]
                changed = True
    return core if len(core) >= 2 else longest


# ------------------------------------------------------------------ вспомогательное
async def _build_queries(
    quote: str, context: str, ui_lang: str, doc_title: str | None, extra: str | None
) -> tuple[list[str], list[str], str]:
    """Просит LLM сформулировать поисковые запросы и выделить ключевые термины."""
    fallback_terms = [t for t in re.split(r"[\s，。、；：（）()\[\]「」“”\"']+", quote) if len(t) >= 2][:6]
    if re.search(r"[\u4e00-\u9fff]", quote):
        core = _cjk_core(quote)
        fallback_queries = [f"{core} 意思 解释", f"{core} 出处 典故 文言文"]
        if core and core not in fallback_terms:
            fallback_terms.insert(0, core)
    else:
        fallback_queries = [quote]
    if doc_title and fallback_queries:
        fallback_queries.append(f"{fallback_queries[0]} {doc_title}"[:120])
    if not llm.available():
        if extra:
            fallback_queries.insert(0, extra)
        return fallback_queries[:3], fallback_terms, ""

    prompt = f"""A student is reading this passage and does not understand the marked span.

MARKED SPAN: {quote}
CONTEXT: {context[:1200]}
DOCUMENT: {doc_title or "—"}
STUDENT LANGUAGE: {ui_lang}

Produce search queries that will find authoritative explanations (encyclopedias, textbooks, dictionaries).
Rules: 2-4 queries, each <= 12 words. Use the original language of the span for at least one query
(for Chinese terminology also add one query with 意思/意思解释/典故; for Classical Chinese add 文言文 释义).
"terms" = 3-6 key terms for relevance filtering.
"focus" = one sentence (in {ui_lang}) describing exactly what must be clarified.

Return JSON: {{"queries":["..."],"terms":["..."],"focus":"..."}}"""
    try:
        data = await llm.chat_json([{"role": "user", "content": prompt}], max_tokens=1200)
        queries = [str(q) for q in (data.get("queries") or []) if str(q).strip()][:4]
        terms = [str(t) for t in (data.get("terms") or []) if str(t).strip()][:8]
        focus = str(data.get("focus") or "").strip()
        if extra:
            queries.insert(0, extra)
        return (queries or fallback_queries)[:4], (terms or fallback_terms), focus
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось построить запросы: %s", exc)
        return fallback_queries[:3], fallback_terms, ""


def _answer_prompt(
    req: ExplainRequest,
    quote: str,
    context: str,
    doc_title: str | None,
    language: str,
    focus: str,
    hits: list[search.SearchHit],
    sources_block: str,
) -> list[dict]:
    headings = HEADINGS.get(req.ui_lang, HEADINGS["en"])
    ui_language = LANG_NAMES.get(req.ui_lang, req.ui_lang)
    task = MODE_TASKS.get(req.mode, MODE_TASKS["explain"]).format(ui_language=ui_language)
    src_lang = LANG_NAMES.get(language, language)

    sources_text = sources_block or "(the web search returned nothing — rely on your own knowledge and say so)"
    system = (
        "You are an outstanding university tutor and simultaneously an expert in Chinese philology "
        "(including Classical Chinese 文言文), history, linguistics and general academia. "
        f"You always answer in {ui_language}. You never invent sources."
    )
    user = f"""A student hovered over a marked fragment while reading and needs a crystal-clear explanation.

DOCUMENT: {doc_title or "—"}
SOURCE LANGUAGE OF THE TEXT: {src_lang}
MARKED FRAGMENT: {quote}
SURROUNDING CONTEXT: \"\"\"{context[:1500]}\"\"\"
{f"FOCUS: {focus}" if focus else ""}

TASK: {task}
{LEVEL_TASKS.get(req.level, LEVEL_TASKS["standard"])}

SEARCH RESULTS (use them, cite as [n]):
{sources_text}

Write the answer as markdown using exactly these sections in {ui_language}:
## {headings["short"]}  — 1-3 sentences: what it is / what it means (give a literal gloss for any Chinese word).
## {headings["context"]} — what it does inside this passage; explain the grammar if it is Classical Chinese.
## {headings["why"]} — why the teacher marked it; what it connects to.
## {headings["mistake"]} — the typical misunderstanding and how to avoid it, only if useful.
## {headings["sources"]} — short list: "[n] title — url" for the sources you actually used (max 5).

Rules: be concrete, no filler, no "as an AI". If the sources contradict each other, say so.
For Classical Chinese always give: literal gloss, modern Chinese rendering, and English meaning."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _fallback_answer(req: ExplainRequest, quote: str, hits: list[search.SearchHit]) -> str:
    headings = HEADINGS.get(req.ui_lang, HEADINGS["en"])
    lines = [f"## {headings['short']}"]
    if hits:
        first = hits[0]
        lines.append(f"**{quote}** — explained from the sources found (no LLM configured, showing excerpts):")
        for i, hit in enumerate(hits[:4], start=1):
            snippet = re.sub(r"\s+", " ", hit.snippet or hit.text)[:400]
            if snippet:
                lines.append(f"- **[{i}] {hit.title}** — {snippet}")
        lines.append("")
        lines.append(f"## {headings['sources']}")
        for i, hit in enumerate(hits[:5], start=1):
            lines.append(f"[{i}] {hit.title} — {hit.url}")
    else:
        lines.append(
            "Could not fetch anything from the web and no LLM is configured. "
            "Set LLM_API_KEY in backend/.env to get full explanations."
        )
    lines.append(
        "\n> No-LLM mode: connect LLM_API_KEY (DeepSeek/OpenAI/OpenRouter/Ollama) "
        "so that the AI synthesises the explanation itself."
    )
    return "\n".join(lines)


async def _save(req: ExplainRequest, key: str, quote: str, answer: str, hits: list[search.SearchHit], model: str) -> None:
    try:
        db.execute(
            "INSERT OR REPLACE INTO explanations (id, cache_key, document_id, quote, mode, ui_lang, level, answer, sources, model, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("exp_"), key, req.document_id, quote[:400], req.mode, req.ui_lang, req.level,
                answer, db.dumps(_fmt_sources(hits)), model, now_iso(),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось закешировать объяснение: %s", exc)


# ------------------------------------------------------------------ основной генератор
async def explain_stream(req: ExplainRequest) -> AsyncGenerator[dict, None]:
    settings = get_settings()
    text = ""
    doc_title: str | None = None
    language = "other"
    glossary: dict[str, str] = {}

    if req.document_id:
        doc = db.one("SELECT title, text, language FROM documents WHERE id = ?", (req.document_id,))
        if doc:
            text = doc["text"] or ""
            doc_title = doc["title"]
            language = doc["language"] or "other"
            glossary = glossary_for(req.document_id)

    quote = (req.quote or "").strip()
    context = req.context or ""
    if req.document_id and text and req.start_char is not None and req.end_char is not None:
        start = max(0, min(req.start_char, len(text)))
        end = max(start, min(req.end_char, len(text)))
        quote = text[start:end] or quote
        context = context or context_window(text, start, end)

    if not quote:
        yield _sse("error", {"message": "No fragment to explain was provided"})
        return

    key = cache_key(req, quote, context)
    if not req.force:
        cached = db.one("SELECT * FROM explanations WHERE cache_key = ?", (key,))
        if cached:
            hits_data = db.loads(cached["sources"], []) or []
            yield _sse("meta", {"quote": quote, "cached": True, "model": cached.get("model") or ""})
            yield _sse("sources", {"sources": hits_data})
            yield _sse("delta", {"text": cached["answer"]})
            yield _sse("done", {"cached": True, "sources": hits_data, "answer": cached["answer"]})
            return

    yield _sse("meta", {"quote": quote, "cached": False, "llm": llm.available()})

    yield _sse("status", {"stage": "planning", "label": "Analysing the fragment…"})
    queries, terms, focus = await _build_queries(quote, context, req.ui_lang, doc_title, req.extra_query)

    yield _sse("status", {"stage": "searching", "label": "Searching the web…", "queries": queries})
    hits, sources_block = await search.gather_context(
        queries, lang_hint=language if language.startswith("zh") else "en", terms=[quote, *terms]
    )

    # добавляем найденные ранее термины документа как подсказку модели
    if glossary:
        extra = "\n".join(f"- {k} → {v}" for k, v in list(glossary.items())[:15])
        sources_block += f"\n\nGLOSSARY (already established in this course):\n{extra}"

    sources = _fmt_sources(hits)
    yield _sse("sources", {"sources": sources})

    if not llm.available():
        answer = _fallback_answer(req, quote, hits)
        yield _sse("delta", {"text": answer})
        yield _sse("done", {"cached": False, "sources": sources, "answer": answer, "degraded": True})
        return

    yield _sse("status", {"stage": "writing", "label": "Writing the explanation…"})
    messages = _answer_prompt(req, quote, context, doc_title, language, focus, hits, sources_block)
    collected: list[str] = []
    try:
        async for piece in llm.chat_stream(messages, max_tokens=1800):
            collected.append(piece)
            yield _sse("delta", {"text": piece})
    except Exception as exc:  # noqa: BLE001
        log.warning("стриминг объяснения прерван: %s", exc)
        if not collected:
            answer = _fallback_answer(req, quote, hits)
            yield _sse("delta", {"text": answer})
            collected = [answer]

    answer = "".join(collected).strip()
    await _save(req, key, quote, answer, hits, settings.llm_model)
    yield _sse("done", {"cached": False, "sources": sources, "answer": answer})
