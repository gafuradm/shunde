"""Живой перевод лекции: LLM-переводчик (высокое качество) или бесплатный fallback."""
from __future__ import annotations

import logging
from typing import AsyncGenerator, Sequence
from urllib.parse import quote as urlquote

import httpx

from ..config import get_settings
from . import llm

log = logging.getLogger("shunde.translate")

_client: httpx.AsyncClient | None = None

LANG_NAMES = {
    "en": "English", "ru": "Russian", "zh": "Simplified Chinese",
    "zh-classical": "Classical Chinese (文言文)", "ja": "Japanese", "ko": "Korean",
    "zh-CN": "Chinese (Mandarin)", "zh-TW": "Chinese (Taiwan)", "de": "German",
    "fr": "French", "es": "Spanish", "auto": "the detected language",
}

GT_CODE = {
    "zh": "zh-CN", "zh-CN": "zh-CN", "zh-classical": "zh-CN", "zh-TW": "zh-TW",
    "en": "en", "ru": "ru", "ja": "ja", "ko": "ko", "de": "de", "fr": "fr", "es": "es",
}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=6.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _system_prompt(target: str, source: str | None, style: str) -> str:
    target_name = LANG_NAMES.get(target, target)
    source_name = LANG_NAMES.get(source, source or "auto")
    if style == "subtitle":
        style_note = (
            "You translate live university lectures into natural spoken English subtitles. "
            "Keep it tight and readable: no filler, no repetitions of the speaker's hesitations."
        )
    elif style == "literal":
        style_note = "Translate precisely, keeping sentence structure as close to the source as possible."
    else:
        style_note = "Translate fluently and idiomatically."
    return (
        f"You are a professional simultaneous interpreter. {style_note}\n"
        f"Source language: {source_name}. Target language: {target_name}.\n"
        "Rules: translate faithfully; keep proper nouns, names of scholars, book titles and 成语 meaning intact; "
        "keep numbers and dates exact; if a term has an established English rendering, use it; "
        "never add explanations, notes or quotation marks; output ONLY the translation."
    )


def _user_prompt(text: str, context: Sequence[str], glossary: dict[str, str] | None) -> str:
    parts = []
    if context:
        joined = "\n".join(f"- {c}" for c in list(context)[-4:])
        parts.append(f"PREVIOUS LINES (context only, do not retranslate):\n{joined}")
    if glossary:
        gloss = "\n".join(f"- {k} = {v}" for k, v in list(glossary.items())[:25])
        parts.append(f"COURSE GLOSSARY (use these renderings):\n{gloss}")
    parts.append(f"NEW UTTERANCE TO TRANSLATE:\n{text.strip()}")
    return "\n\n".join(parts)


async def _google_free(text: str, source: str | None, target: str) -> str:
    s = get_settings()
    if not s.allow_google_free_translate:
        return ""
    sl = GT_CODE.get(source or "auto", "auto")
    tl = GT_CODE.get(target, target)
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={urlquote(text[:4000])}"
    )
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data and isinstance(data[0], list):
            return "".join(part[0] for part in data[0] if part and part[0]).strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("google-free перевод не удался: %s", exc)
    return ""


async def translate(
    text: str,
    target: str = "en",
    source: str | None = None,
    context: Sequence[str] | None = None,
    glossary: dict[str, str] | None = None,
    style: str = "subtitle",
    # с запасом: reasoning-модели могут потратить часть лимита до ответа
    max_tokens: int = 1200,
) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if llm.available():
        try:
            messages = [
                {"role": "system", "content": _system_prompt(target, source, style)},
                {"role": "user", "content": _user_prompt(text, context or [], glossary)},
            ]
            out = await llm.chat(messages, model=get_settings().fast_model, temperature=0.0, max_tokens=max_tokens)
            if out:
                return out.strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM-перевод не удался, переключаюсь на fallback: %s", exc)
    return await _google_free(text, source, target)


async def translate_stream(
    text: str,
    target: str = "en",
    source: str | None = None,
    context: Sequence[str] | None = None,
    glossary: dict[str, str] | None = None,
    style: str = "subtitle",
) -> AsyncGenerator[str, None]:
    """Стриминговый перевод — отдаёт дельты (для эффекта «мгновенного» перевода)."""
    text = (text or "").strip()
    if not text:
        return
    if llm.available():
        messages = [
            {"role": "system", "content": _system_prompt(target, source, style)},
            {"role": "user", "content": _user_prompt(text, context or [], glossary)},
        ]
        got_anything = False
        try:
            async for piece in llm.chat_stream(messages, model=get_settings().fast_model, temperature=0.0, max_tokens=1200):
                got_anything = True
                yield piece
            if got_anything:
                return
            log.warning("LLM-перевод (стрим) вернул пустой ответ — переключаюсь на fallback")
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM-перевод (стрим) не удался: %s", exc)
    fallback = await _google_free(text, source, target)
    if fallback:
        yield fallback
