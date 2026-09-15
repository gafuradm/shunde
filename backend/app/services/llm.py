"""Клиент для OpenAI-совместимых LLM (DeepSeek, OpenAI, OpenRouter, Ollama, vLLM)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, AsyncGenerator, Iterable

import httpx

from ..config import get_settings

log = logging.getLogger("shunde.llm")

_client: httpx.AsyncClient | None = None


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return get_settings().llm_available


def _get_client() -> httpx.AsyncClient:
    global _client
    s = get_settings()
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(s.llm_timeout, connect=15.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _endpoint() -> str:
    return get_settings().llm_base_url.rstrip("/") + "/chat/completions"


def _headers() -> dict[str, str]:
    s = get_settings()
    headers = {"Content-Type": "application/json"}
    if s.llm_api_key:
        headers["Authorization"] = f"Bearer {s.llm_api_key}"
    headers["User-Agent"] = "ShundeTutor/1.0"
    return headers


def _payload(
    messages: list[dict],
    model: str | None,
    temperature: float | None,
    max_tokens: int,
    json_mode: bool,
    stream: bool,
) -> dict:
    s = get_settings()
    body: dict[str, Any] = {
        "model": model or s.llm_model,
        "messages": messages,
        "temperature": s.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if json_mode and s.llm_json_mode:
        body["response_format"] = {"type": "json_object"}
    if s.llm_disable_thinking:
        # Reasoning-модели тратят max_tokens на «размышления» и только потом отдают
        # ответ: для мгновенного перевода субтитров это лишняя задержка, а при
        # небольшом max_tokens ответ вообще может прийти пустым.
        body["thinking"] = {"type": "disabled"}
    return body


def _drop_thinking(body: dict) -> bool:
    """Убирает необязательный параметр thinking, если провайдер его не понимает."""
    if body.pop("thinking", None) is not None:
        log.warning("Провайдер LLM не принял параметр 'thinking' — повторяю запрос без него")
        return True
    return False


async def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 1500,
    json_mode: bool = False,
    retries: int = 2,
) -> str:
    """Обычный (нестриминговый) вызов чата."""
    if not available():
        raise LLMError("LLM is not configured: set LLM_API_KEY in backend/.env")
    body = _payload(messages, model, temperature, max_tokens, json_mode, stream=False)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await _get_client().post(_endpoint(), json=body, headers=_headers())
            if resp.status_code >= 400:
                if resp.status_code == 400 and _drop_thinking(body):
                    continue
                raise LLMError(f"{resp.status_code}: {resp.text[:400]}")
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(0.8 * (attempt + 1))
    raise LLMError(f"LLM is unavailable: {last_error}")


async def _stream_body(body: dict) -> AsyncGenerator[str, None]:
    """Одна попытка стримингового запроса с готовым телом."""
    async with _get_client().stream("POST", _endpoint(), json=body, headers=_headers()) as resp:
        if resp.status_code >= 400:
            text = (await resp.aread()).decode("utf-8", errors="replace")
            raise LLMError(f"{resp.status_code}: {text[:400]}")
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            choices = parsed.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                yield piece


async def chat_stream(
    messages: list[dict],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 1500,
) -> AsyncGenerator[str, None]:
    """Стриминговый вызов: отдаёт текстовые дельты."""
    if not available():
        raise LLMError("LLM is not configured: set LLM_API_KEY in backend/.env")
    body = _payload(messages, model, temperature, max_tokens, json_mode=False, stream=True)
    try:
        async for piece in _stream_body(body):
            yield piece
    except LLMError as exc:
        # некоторые шлюзы не знают параметр thinking — повторяем без него
        if "400" not in str(exc) or not _drop_thinking(body):
            raise
        async for piece in _stream_body(body):
            yield piece


async def chat_vision(prompt: str, image_bytes: bytes, mime: str = "image/png", max_tokens: int = 2000) -> str:
    """Распознавание текста/содержимого изображения через vision-модель."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    return await chat(messages, max_tokens=max_tokens, temperature=0.0, retries=1)


# ------------------------------------------------------------------ JSON
def parse_json(raw: str) -> Any:
    """Максимально терпимый парсер JSON из ответа модели."""
    if not raw:
        raise LLMError("Empty model response")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # ищем первый сбалансированный массив/объект
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"Could not parse JSON: {raw[:300]}")


async def chat_json(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
) -> Any:
    raw = await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    return parse_json(raw)


async def warmup() -> None:
    """Проверка доступности LLM при старте (не блокирует приложение)."""
    if not available():
        log.warning("LLM не настроен — работаем в деградированном режиме (эвристики + Wikipedia)")
        return
    try:
        await chat([{"role": "user", "content": "ping"}], max_tokens=5, retries=0)
        log.info("LLM доступен: %s", get_settings().llm_model)
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM недоступен (%s) — часть функций будет работать в деградированном режиме", exc)


def models_hint() -> Iterable[str]:
    s = get_settings()
    return (s.llm_model, s.fast_model)
