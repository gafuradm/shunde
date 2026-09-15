"""Веб-поиск: Wikipedia (без ключа) + Tavily / Serper / Bing / DuckDuckGo.

Используется в пайплайне объяснений: студент наводит курсор — мы ищем источники
и синтезируем объяснение. Работает и без API-ключей (Wikipedia + DuckDuckGo HTML).
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from ..config import get_settings
from .extract import _html_to_text

log = logging.getLogger("shunde.search")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_client: httpx.AsyncClient | None = None


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    text: str = ""
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet[:600], "provider": self.provider}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal",
    "169.254.169.254", "100.100.100.200",
}


def _safe_url(url: str) -> bool:
    """Защита от SSRF: только http(s) и никаких локальных адресов.

    Проверка по DNS делается только при SEARCH_DNS_GUARD=true: во многих
    корпоративных/домашних окружениях (VPN в режиме fake-IP) DNS возвращает
    адреса из диапазона 198.18.0.0/15, и жёсткая проверка ломает весь поиск.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ip = ipaddress.ip_address(host)  # литеральный IP в URL
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)
    except ValueError:
        pass  # это доменное имя — проверяем дополнительно только по флагу
    if not get_settings().search_dns_guard:
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
    except Exception:  # noqa: BLE001
        return True
    return True


# ---------------------------------------------------------------- Wikipedia
# Служебные слова, которые добавляются к запросу для поисковиков и мешают
# полнотекстовому поиску Wikipedia (он ищет по буквальному совпадению).
_WIKI_NOISE = (
    "意思", "解释", "释义", "含义", "出处", "典故", "文言文", "翻译", "成语",
    "meaning", "definition", "explanation", "translation",
)


def _clean_wiki_query(query: str) -> str:
    cleaned = query
    for word in _WIKI_NOISE:
        cleaned = re.sub(re.escape(word), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s，。、；：？！?\"'「」“”]+", " ", cleaned).strip()
    # если осмысленных символов осталось мало — возвращаем исходный запрос
    return cleaned if len(cleaned) >= 2 else query.strip()


async def wikipedia(query: str, langs: Iterable[str] = ("zh", "en"), limit: int = 3) -> list[SearchHit]:
    hits: list[SearchHit] = []
    wiki_query = _clean_wiki_query(query)
    for lang in langs:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query", "format": "json", "list": "search",
            "srsearch": wiki_query, "srlimit": str(limit), "srprop": "snippet",
        }
        try:
            resp = await _get_client().get(api, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("wikipedia(%s) ошибка: %s", lang, exc)
            continue
        titles = [item["title"] for item in data.get("query", {}).get("search", [])]
        if not titles:
            continue
        try:
            resp = await _get_client().get(
                api,
                params={
                    "action": "query", "format": "json", "prop": "extracts", "explaintext": "1",
                    "exintro": "1", "redirects": "1", "titles": "|".join(titles),
                },
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
        except Exception as exc:  # noqa: BLE001
            log.debug("wikipedia extracts(%s) ошибка: %s", lang, exc)
            pages = {}
        for page in pages.values():
            title = page.get("title", "")
            extract = (page.get("extract") or "").strip()
            if not title:
                continue
            url = f"https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            hits.append(
                SearchHit(
                    title=f"{title} — Wikipedia ({lang})",
                    url=url,
                    snippet=re.sub(r"\s+", " ", extract)[:600],
                    provider=f"wikipedia-{lang}",
                    text=extract[:2500],
                    score=0.6 if lang != "zh" else 0.7,
                )
            )
    return hits


# ---------------------------------------------------------------- провайдеры с ключом
async def tavily(query: str, limit: int = 6) -> list[SearchHit]:
    key = get_settings().tavily_api_key.strip()
    if not key:
        return []
    try:
        resp = await _get_client().post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": limit,
                "search_depth": "advanced",
                "include_answer": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("tavily ошибка: %s", exc)
        return []
    return [
        SearchHit(
            title=r.get("title", "")[:200],
            url=r.get("url", ""),
            snippet=(r.get("content") or "")[:800],
            provider="tavily",
            text=(r.get("raw_content") or "")[:3000] or (r.get("content") or "")[:3000],
            score=0.9,
        )
        for r in data.get("results", [])
        if r.get("url")
    ]


async def serper(query: str, limit: int = 6) -> list[SearchHit]:
    key = get_settings().serper_api_key.strip()
    if not key:
        return []
    try:
        resp = await _get_client().post(
            "https://google.serper.dev/search",
            json={"q": query, "num": limit},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("serper ошибка: %s", exc)
        return []
    hits = [
        SearchHit(
            title=r.get("title", "")[:200], url=r.get("link", ""),
            snippet=(r.get("snippet") or "")[:800], provider="serper", score=0.85,
        )
        for r in data.get("organic", [])
        if r.get("link")
    ]
    if data.get("answerBox"):
        box = data["answerBox"]
        hits.insert(0, SearchHit(
            title=box.get("title") or "Answer box",
            url=box.get("link") or "",
            snippet=(box.get("snippet") or box.get("answer") or "")[:800],
            provider="serper-answer",
            score=0.95,
        ))
    return hits


async def bing(query: str, limit: int = 6) -> list[SearchHit]:
    s = get_settings()
    if not s.bing_search_key.strip():
        return []
    try:
        resp = await _get_client().get(
            s.bing_search_endpoint,
            params={"q": query, "count": limit, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": s.bing_search_key.strip()},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("bing ошибка: %s", exc)
        return []
    return [
        SearchHit(
            title=r.get("name", "")[:200], url=r.get("url", ""),
            snippet=(r.get("snippet") or "")[:800], provider="bing", score=0.8,
        )
        for r in data.get("webPages", {}).get("value", [])
        if r.get("url")
    ]


# ---------------------------------------------------------------- DuckDuckGo (без ключа)
def _clean_ddg_href(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:  # noqa: BLE001
            pass
    return href


def _parse_ddg(html: str, limit: int) -> list[SearchHit]:
    """Разбирает выдачу lite- и html-версий DuckDuckGo."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[SearchHit] = []
    for link in soup.select("a.result-link, a.result__a")[: limit * 3]:
        title = link.get_text(" ", strip=True)
        href = _clean_ddg_href(link.get("href", ""))
        if not title or not href.startswith("http"):
            continue
        row = link.find_parent("tr") or link.find_parent("div", class_="result") or link.parent
        snippet_el = row.select_one(".result-snippet, .result__snippet") if row else None
        out.append(
            SearchHit(
                title=title[:200],
                url=href,
                snippet=(snippet_el.get_text(" ", strip=True) if snippet_el else "")[:800],
                provider="duckduckgo",
                score=0.55,
            )
        )
        if len(out) >= limit:
            break
    return out


def _decode_bing_url(href: str) -> str:
    """Bing прячет реальный адрес в редирект bing.com/ck/a?...&u=a1<base64url>."""
    if "bing.com/ck/a" not in href:
        return href
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", href)
    if not m:
        return href
    raw = m.group(1).replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(raw).decode("utf-8", "replace")
        return decoded if decoded.startswith("http") else href
    except Exception:  # noqa: BLE001
        return href


async def bing_html(query: str, limit: int = 6) -> list[SearchHit]:
    """Бесплатная HTML-выдача Bing (без API-ключа)."""
    try:
        resp = await _get_client().get(
            "https://www.bing.com/search",
            params={"q": query, "count": limit, "setlang": "zh-hans"},
        )
        if resp.status_code >= 400:
            return []
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")
        out: list[SearchHit] = []
        for li in soup.select("li.b_algo")[: limit * 2]:
            a = li.select_one("h2 a") or li.select_one("a[href^='http']")
            if not a:
                continue
            url = _decode_bing_url(a.get("href", ""))
            if not url.startswith("http"):
                continue
            p = li.select_one(".b_caption p") or li.select_one("p")
            out.append(
                SearchHit(
                    title=a.get_text(" ", strip=True)[:200],
                    url=url,
                    snippet=(p.get_text(" ", strip=True) if p else "")[:800],
                    provider="bing-html",
                    score=0.45,
                )
            )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("bing_html ошибка: %s", exc)
        return []


async def duckduckgo(query: str, lang: str = "cn-zh", limit: int = 6) -> list[SearchHit]:
    """DuckDuckGo без API-ключа: сначала lite-эндпоинт, затем обычный html.

    html.duckduckgo.com часто отвечает 202 с анти-бот заглушкой — в этом случае
    пробуем другой эндпоинт, а при полном провале остаётся Mojeek/Wikipedia.
    """
    attempts = (
        ("https://lite.duckduckgo.com/lite/", {"q": query}),
        ("https://html.duckduckgo.com/html/", {"q": query, "kl": lang}),
    )
    for url, data in attempts:
        try:
            resp = await _get_client().post(
                url,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://duckduckgo.com/",
                },
            )
            if resp.status_code >= 400:
                log.debug("ddg %s -> HTTP %s", url, resp.status_code)
                continue
            hits = _parse_ddg(resp.text, limit)
            if hits:
                return hits
        except Exception as exc:  # noqa: BLE001
            log.debug("ddg(%s) ошибка: %s", url, exc)
    log.info("DuckDuckGo недоступен (анти-бот); используются другие провайдеры")
    return []


async def mojeek(query: str, limit: int = 6) -> list[SearchHit]:
    """Mojeek — независимый поисковый индекс, доступен без ключей."""
    try:
        resp = await _get_client().get("https://www.mojeek.com/search", params={"q": query})
        if resp.status_code >= 400:
            return []
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")
        out: list[SearchHit] = []
        for li in soup.select("ul.results-standard li")[: limit * 3]:
            a = li.select_one("a.title") or li.select_one("h2 a")
            if not a:
                continue
            url = a.get("href", "")
            if not url.startswith("http"):
                continue
            p = li.select_one("p.s")
            out.append(
                SearchHit(
                    title=a.get_text(" ", strip=True)[:200],
                    url=url,
                    snippet=(p.get_text(" ", strip=True) if p else "")[:800],
                    provider="mojeek",
                    score=0.5,
                )
            )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("mojeek ошибка: %s", exc)
        return []


# ---------------------------------------------------------------- агрегация
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Слова, которые мы сами добавляем к запросу: по ним нельзя судить о релевантности
_QUERY_STOPWORDS = {
    "意思", "解释", "释义", "含义", "出处", "典故", "文言文", "翻译", "成语", "释义",
    "meaning", "definition", "explanation", "translation", "wiki", "wikipedia",
    "what", "why", "how", "the", "and", "for", "with",
}
# Провайдеры-скраперы: при анти-боте или гео-редиректе они отдают страницы,
# вообще не связанные с запросом (отели, WhatsApp, установщики софта).
SCRAPED_PROVIDERS = {"bing-html", "duckduckgo", "mojeek"}


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _query_tokens(query: str) -> list[str]:
    tokens = [t for t in re.split(r"[^\w\u4e00-\u9fff]+", query or "") if len(t) >= 2]
    meaningful = [t for t in tokens if t.lower() not in _QUERY_STOPWORDS]
    return meaningful or tokens


def _mentions_query(hit: SearchHit, query: str) -> bool:
    """Есть ли в находке хоть один значимый токен запроса (CJK-слово или латинское слово)."""
    tokens = _query_tokens(query)
    if not tokens:
        return True
    hay = f"{hit.title} {hit.snippet} {hit.text[:400]}".lower()
    return any(t.lower() in hay for t in tokens)


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    out: list[SearchHit] = []
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (hit.url or hit.title).lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


async def web_search(query: str, lang_hint: str = "zh", limit: int = 8) -> list[SearchHit]:
    """Собирает результаты из всех доступных провайдеров."""
    if not query.strip():
        return []
    ddg_lang = "cn-zh" if lang_hint.startswith("zh") else "wt-wt"
    wiki_langs = ("zh", "en") if lang_hint.startswith("zh") else ("en", "zh")

    tasks = [
        wikipedia(query, wiki_langs, limit=3),
        duckduckgo(query, ddg_lang, limit=6),
        mojeek(query, limit=6),
        bing_html(query, limit=6),
        tavily(query, limit=6),
        serper(query, limit=6),
        bing(query, limit=6),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    hits: list[SearchHit] = []
    for res in results:
        if isinstance(res, list):
            hits.extend(res)
    # Скраперы при анти-боте/гео-редиректе возвращают страницы, не имеющие
    # отношения к запросу: такие находки выбрасываем сразу — иначе они попадут
    # студенту в блок «Источники» и собьют модель с толку.
    filtered = [h for h in hits if h.provider not in SCRAPED_PROVIDERS or _mentions_query(h, query)]
    if len(filtered) != len(hits):
        log.info("по запросу «%s» отброшено %s нерелевантных находок скраперов",
                 query[:60], len(hits) - len(filtered))
    return _dedupe(filtered)[:limit]


async def fetch_page(url: str, limit: int = 4000) -> str:
    """Скачивает страницу и возвращает чистый текст (с ограничением)."""
    if not url or not _safe_url(url):
        return ""
    try:
        resp = await _get_client().get(url, headers={"User-Agent": UA})
        if resp.status_code >= 400:
            return ""
        content_type = resp.headers.get("content-type", "")
        if "pdf" in content_type:
            return ""
        raw = resp.content[:600_000]
        if content_type and not any(t in content_type for t in ("html", "text", "xml", "json")):
            return ""
        text = _html_to_text(raw.decode(resp.encoding or "utf-8", errors="replace"))
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit]
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_page(%s) ошибка: %s", url, exc)
        return ""


def _relevance(hit: SearchHit, terms: list[str]) -> float:
    """Насколько находка действительно относится к терминам запроса.

    Полнотекстовый поиск Wikipedia по цитатам вэньяня любит возвращать
    нерелевантные статьи — этот счёт даёт возможность отодвинуть их в конец.
    """
    keys = [t.strip().lower() for t in terms if len(t.strip()) >= 2]
    if not keys:
        return 0.0
    hay = f"{hit.title} {hit.snippet} {hit.text}".lower()
    score = 0.0
    for key in keys:
        if key in hay:
            score += 2.0 if len(key) >= 3 else 1.0
            continue
        if len(key) >= 3:  # частичное совпадение по биграммам (длинные цитаты)
            grams = {key[i : i + 2] for i in range(len(key) - 1)}
            if grams:
                score += sum(1 for g in grams if g in hay) / len(grams)
    return score


def pick_relevant(text: str, terms: list[str], limit: int = 1800) -> str:
    """Оставляет только фрагменты страницы, релевантные терминам запроса."""
    if not text:
        return ""
    lowered_terms = [t.lower() for t in terms if len(t) >= 2]
    sentences = re.split(r"(?<=[。．.!?！？\n])\s*", text)
    chosen: list[str] = []
    size = 0
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        low = s.lower()
        if any(t in low for t in lowered_terms):
            chosen.append(s)
            size += len(s)
            if size >= limit:
                break
    if not chosen:
        return text[:limit]
    return "\n".join(chosen)[:limit]


async def gather_context(
    queries: list[str],
    lang_hint: str = "zh",
    terms: list[str] | None = None,
    max_hits: int = 8,
) -> tuple[list[SearchHit], str]:
    """Ищет по нескольким запросам, при необходимости выкачивает страницы и склеивает контекст."""
    hits: list[SearchHit] = []
    for q in [q for q in queries if q and q.strip()][:4]:
        try:
            hits.extend(await web_search(q, lang_hint=lang_hint, limit=5))
        except Exception as exc:  # noqa: BLE001
            log.warning("web_search(%s) ошибка: %s", q, exc)
    hits = _dedupe(hits)[: max_hits * 2]
    if terms:
        scored = sorted(((_relevance(h, terms), h) for h in hits), key=lambda p: p[0], reverse=True)
        good = [h for s, h in scored if s > 0]
        if not good:
            # Ничего по делу: лучше отвечать без источников, чем подсунуть мусор.
            # Пробуем спастись надёжным индексом — Wikipedia по ключевому термину.
            primary = terms[0] if terms else ""
            if primary:
                wiki_langs = ("zh", "en") if _has_cjk(primary) else ("en", "zh")
                log.info("релевантных находок нет — пробую Wikipedia напрямую: %s", primary[:60])
                good = await wikipedia(primary, wiki_langs, limit=3)
        hits = good[:max_hits]
    else:
        hits = hits[:max_hits]

    settings = get_settings()
    # у Wikipedia в text только короткое вступление — догружаем полную страницу,
    # если материала мало (вступление < 500 символов)
    thin = [h for h in hits if len(h.text) < 500]
    pages_to_fetch = thin[: max(0, settings.search_pages)]
    if pages_to_fetch:
        fetched = await asyncio.gather(
            *[fetch_page(h.url) for h in pages_to_fetch], return_exceptions=True
        )
        for hit, text in zip(pages_to_fetch, fetched):
            if isinstance(text, str) and text:
                picked = pick_relevant(text, terms or [], limit=2200)
                if len(picked) > len(hit.text):
                    hit.text = picked

    blocks: list[str] = []
    for i, hit in enumerate(hits, start=1):
        body = (hit.text or hit.snippet or "").strip()
        if not body:
            continue
        blocks.append(
            f"[{i}] {hit.title}\nURL: {hit.url}\nProvider: {hit.provider}\n{body[:2200]}"
        )
    return hits, "\n\n".join(blocks)[:12000]


def hit_to_source(hit: SearchHit) -> dict:
    return {
        "title": hit.title or hit.url,
        "url": hit.url,
        "snippet": re.sub(r"\s+", " ", hit.snippet)[:400],
        "provider": hit.provider,
    }
