"""ИИ-разметка документов: поиск важных понятий, фактов и потенциально непонятных мест.

Работает с английским, современным китайским и вэньянем (文言文): 虚词, 通假字,
词类活用, 典故. Если LLM не настроен — включается эвристический режим.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..config import get_settings
from ..db import db, new_id, now_iso
from . import llm
from .text import Segment, clip_quote, find_quote, split_segments

log = logging.getLogger("shunde.annotate")

KINDS = ["concept", "term", "fact", "unclear", "allusion", "grammar", "emphasis"]

SYSTEM_PROMPT = """You are a senior university teaching assistant who prepares annotated editions of academic and literary texts.
You mark up passages so students immediately see what deserves attention: key concepts, terminology, facts, allusions (典故),
classical-Chinese grammar (虚词/句式/通假字/词类活用), and places students typically misunderstand.
You are an expert in English, Modern Chinese (简体/繁體) and Classical Chinese (文言文 / Literary Chinese).
Reply with valid JSON only. No explanations, no markdown fences."""


def _user_prompt(
    segment_text: str,
    doc_title: str,
    language: str,
    classical: float,
    known: list[str],
    max_items: int,
    kinds: list[str],
    note_lang: str,
    mode: str,
) -> str:
    lang_note = {
        "zh-classical": "The passage is CLASSICAL CHINESE (文言文). Pay special attention to 虚词 (之, 其, 而, 以, 于, 者, 也, 乃, 则), 通假字, 词类活用, 典故 and words whose modern meaning differs.",
        "zh": "The passage is MODERN CHINESE. Mark 成语, technical/disciplinary terms, proper nouns, key figures and dates, and sentences with non-obvious logic.",
        "en": "The passage is ENGLISH academic prose. Mark technical terms, named entities, load-bearing facts and easily missed logical connectives.",
        "ru": "The passage is RUSSIAN academic prose. Mark terms, named entities, key facts and easily missed logical connectives.",
    }.get(language, "Mark terms, named entities, and key facts.")
    depth = (
        "Be exhaustive: up to {max_items} marks, include secondary details."
        if mode == "deep"
        else "Be selective: pick only what a student really needs, at most {max_items} marks."
    ).format(max_items=max_items)

    kinds_line = ", ".join(kinds)
    known_line = ""
    if known:
        known_line = (
            "\nAlready marked in this document (do NOT repeat them): "
            + "; ".join(known[:40])
        )

    return f"""Document: "{doc_title}"
{lang_note}
Classical-Chinese score: {classical:.2f} (0 = modern, 1 = pure 文言文)

PASSAGE:
\"\"\"
{segment_text}
\"\"\"

Task: mark the spans in this passage that need explanation for a university student.
{depth}

Allowed kinds: {kinds_line}
- concept  = key concept / theory / idea worth explaining
- term     = terminology, proper noun, named entity, 成语
- fact     = date, figure, statistic, event the student must remember
- unclear  = wording that commonly confuses students (ambiguous reference, unusual syntax, irony)
- allusion = 典故 / cultural or literary reference requiring background
- grammar  = grammatical structure (especially 文言句式, 虚词, 通假字, 词类活用)
- emphasis = thesis sentence / conclusion to remember

Hard rules:
1. "quote" MUST be copied character-for-character from the passage (2-30 characters for Chinese; up to 60 for Latin script). Never paraphrase, never invent.
2. Quotes must not overlap each other.
3. "title": short label in {note_lang}, max 8 words.
4. "rationale": 1-2 sentences in {note_lang} saying why it matters and what students typically get wrong.
5. "en": concise English equivalent/translation of the marked span (used as glossary).
6. "difficulty": integer 1..5 (5 = hardest for a student).
{known_line}

Return JSON exactly in this shape:
{{"items":[{{"quote":"...","kind":"concept","title":"...","rationale":"...","en":"...","difficulty":3}}]}}"""


# ------------------------------------------------------------------ утилиты
def _sanitize_items(items: Any, segment: Segment, text: str) -> list[dict]:
    """Проверяет ответ модели и превращает quotes в точные смещения."""
    if isinstance(items, dict):
        items = items.get("items") or items.get("annotations") or []
    if not isinstance(items, list):
        return []

    result: list[dict] = []
    taken: list[tuple[int, int]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("quote") or raw.get("text") or "").strip()
        if not quote:
            continue
        span = find_quote(text, quote, segment.start, segment.end)
        if not span:
            continue
        start, end = span
        if end - start < 2 or end - start > 160:
            continue
        if any(not (end <= a or start >= b) for a, b in taken):  # пересечение
            continue
        kind = str(raw.get("kind") or "concept").strip().lower()
        if kind not in KINDS:
            kind = "concept"
        try:
            difficulty = int(raw.get("difficulty") or 3)
        except (TypeError, ValueError):
            difficulty = 3
        difficulty = max(1, min(5, difficulty))
        taken.append((start, end))
        result.append(
            {
                "start_char": start,
                "end_char": end,
                "quote": clip_quote(text, start, end),
                "kind": kind,
                "title": (str(raw.get("title") or "").strip() or clip_quote(text, start, end, 24))[:200],
                "rationale": str(raw.get("rationale") or "").strip()[:600],
                "difficulty": difficulty,
                "en": str(raw.get("en") or "").strip()[:200],
            }
        )
    result.sort(key=lambda i: i["start_char"])
    return result


# ------------------------------------------------------------------ эвристики (без LLM)
QUOTED_RE = re.compile(r"[「『“\"']([^「」『』“”\"']{2,30})[」』”\"']")
TITLE_RE = re.compile(r"《([^》]{2,40})》")
YEAR_RE = re.compile(r"\b(?:\d{3,4}\s*(?:年|г\.?|BC|AD|CE|BCE))|\b\d{3,4}\b\s*(?:年)")
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|％|percent|процент|млн|万|億|亿|倍|р\.|руб|元|美元|km|km²)\b")
LATIN_TERM_RE = re.compile(r"\b[A-Z][a-zA-Z\-]{2,}(?:\s+[A-Z][a-zA-Z\-]{2,}){0,3}\b")
IDIOM_RE = re.compile(r"[\u4e00-\u9fff]{4}")
CLASSICAL_MARK_RE = re.compile(r"(者也|是以|何以|所谓|之谓|至于|所以|是故|不亦|无乃|何如|焉|矣|乎哉|若夫|且夫|今夫|虽然)")

COMMON_4 = {
    "我们", "他们", "这个", "那个", "因为", "所以", "但是", "如果", "可以", "没有",
    "什么", "怎么", "时候", "问题", "工作", "中国", "美国", "学生", "老师", "研究",
    "发展", "社会", "经济", "文化", "历史", "科学", "技术", "教育", "生活", "世界",
}

COMMON_IDIOMS = {
    "一鸣惊人", "亡羊补牢", "守株待兔", "刻舟求剑", "自相矛盾", "掩耳盗铃", "画蛇添足",
    "杯弓蛇影", "叶公好龙", "邯郸学步", "因材施教", "温故知新",
    "举一反三", "循序渐进", "持之以恒", "锲而不舍", "水到渠成", "迎刃而解", "相辅相成",
    "融会贯通", "见微知著", "错综复杂", "画龙点睛", "亡国灭种", "兴利除弊", "载舟覆舟",
    "兼听则明", "偏信则暗", "居安思危", "防微杜渐", "提纲挈领", "一分为二",
}


def heuristic_items(text: str, segment: Segment, max_items: int) -> list[dict]:
    """Разметка без LLM: цитаты, названия, даты, числа, термины, 成语 и 文言虚词."""
    items: list[dict] = []
    lo, hi = segment.start, segment.end
    taken: list[tuple[int, int]] = []

    def push(start: int, end: int, kind: str, title: str, rationale: str, diff: int = 3) -> None:
        if len(items) >= max_items:
            return
        if any(not (end <= a or start >= b) for a, b in taken):
            return
        taken.append((start, end))
        items.append(
            {
                "start_char": start, "end_char": end,
                "quote": clip_quote(text, start, end),
                "kind": kind, "title": title, "rationale": rationale,
                "difficulty": diff, "en": "",
            }
        )

    def scan(regex: re.Pattern[str], kind: str, title: str, rationale: str, diff: int = 3) -> None:
        for m in regex.finditer(text, lo, hi):
            push(m.start(), m.end(), kind, title, rationale, diff)

    scan(TITLE_RE, "term", "Title of a work", "Source title — check the author and the context.")
    scan(QUOTED_RE, "term", "Quotation", "Quoted passage: important for following the argument.")
    scan(CLASSICAL_MARK_RE, "grammar", "文言虚词/句式", "Classical construction: the function word changes the meaning of the phrase.", 4)
    scan(YEAR_RE, "fact", "Date/year", "Chronological detail: note which period it belongs to.")
    scan(NUMBER_RE, "fact", "Numerical fact", "Quantitative data: important for the argument.")

    if segment.text and re.search(r"[\u4e00-\u9fff]", segment.text):
        for m in IDIOM_RE.finditer(text, lo, hi):
            word = m.group(0)
            if word in COMMON_4:
                continue
            if word in COMMON_IDIOMS or _looks_like_idiom(word):
                push(m.start(), m.end(), "concept", "成语", "Chengyu: the meaning cannot be derived from the individual characters.", 4)
    else:
        for m in LATIN_TERM_RE.finditer(text, lo, hi):
            token = m.group(0)
            if len(token) < 5:
                continue
            push(m.start(), m.end(), "term", "Term/name", "Check the definition of the term or who this person is.")

    items.sort(key=lambda i: i["start_char"])
    return items[:max_items]


def _looks_like_idiom(word: str) -> bool:
    """Эвристика для 成语: 4 иероглифа, в которых есть «классические» элементы."""
    return any(ch in CLASSICAL_CHARS_LOCAL for ch in word)


CLASSICAL_CHARS_LOCAL = set("之乎者也矣焉哉乃盖是以所夫其而与於于则皆亦且")


# ------------------------------------------------------------------ основной пайплайн
async def annotate_document(doc_id: str, opts: dict[str, Any]) -> dict:
    """Полный проход по документу: сегменты → LLM/эвристики → записи в БД."""
    settings = get_settings()
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        return {"error": "document not found"}
    text: str = doc["text"] or ""
    if not text.strip():
        db.update("documents", doc_id, {"status": "failed", "error": "The document has no extracted text"})
        return {"error": "empty text"}

    mode = opts.get("mode", "quick")
    kinds = opts.get("kinds") or KINDS
    max_items = int(opts.get("max_per_segment") or (10 if mode == "deep" else 6))
    note_lang = opts.get("note_lang") or ("en" if doc["language"] in {"en", "ru"} else "zh")

    if opts.get("replace_existing"):
        db.execute("DELETE FROM annotations WHERE document_id = ? AND created_by = 'ai'", (doc_id,))

    segments = split_segments(text)
    if opts.get("only_segment") is not None:
        segments = [s for s in segments if s.index == int(opts["only_segment"])]

    known: list[str] = [a["quote"] for a in db.query(
        "SELECT quote FROM annotations WHERE document_id = ? LIMIT 60", (doc_id,)
    )]
    glossary: dict[str, str] = {}

    use_llm = llm.available()
    db.update("documents", doc_id, {
        "status": "annotating",
        "error": None,
        "meta": db.dumps({
            "progress": 0.02,
            "stage": "AI markup" if use_llm else "Heuristic markup (no LLM configured)",
            "mode": mode,
            "engine": "llm" if use_llm else "heuristic",
        }),
    })

    total = max(1, len(segments))
    created = 0
    semaphore = asyncio.Semaphore(3)
    results: list[tuple[Segment, list[dict]]] = []
    lock = asyncio.Lock()
    done = 0

    async def process(segment: Segment) -> None:
        nonlocal done
        async with semaphore:
            items: list[dict] = []
            if use_llm:
                try:
                    raw = await llm.chat_json(
                        [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": _user_prompt(
                                segment.text, doc["title"], doc["language"] or "other",
                                float(doc["classical"] or 0), known, max_items, kinds, note_lang, mode,
                            )},
                        ],
                        model=settings.fast_model if mode == "quick" else settings.llm_model,
                        max_tokens=1800 if mode == "deep" else 1200,
                    )
                    items = _sanitize_items(raw, segment, text)
                except Exception as exc:  # noqa: BLE001
                    log.warning("разметка сегмента %s не удалась: %s", segment.index, exc)
                    items = heuristic_items(text, segment, max_items=3)
            else:
                items = heuristic_items(text, segment, max_items=max_items)

            async with lock:
                results.append((segment, items))
                known.extend(i["quote"] for i in items)
                done += 1
                progress = 0.05 + 0.9 * (done / total)
                meta = db.loads(doc["meta"], {}) or {}
                meta.update({"progress": round(progress, 3), "stage": f"Segment {done}/{total}"})
                db.update("documents", doc_id, {"meta": db.dumps(meta)})

    await asyncio.gather(*(process(s) for s in segments))

    for _segment, items in results:
        for item in items:
            db.insert("annotations", {
                "id": new_id("ann_"),
                "document_id": doc_id,
                "start_char": item["start_char"],
                "end_char": item["end_char"],
                "quote": item["quote"],
                "kind": item["kind"],
                "title": item["title"],
                "rationale": item["rationale"],
                "difficulty": item["difficulty"],
                "created_by": "ai",
                "meta": db.dumps({"en": item.get("en", "")}),
                "created_at": now_iso(),
            })
            created += 1
            if item.get("en"):
                glossary[item["quote"]] = item["en"]

    meta = db.loads(doc["meta"], {}) or {}
    meta.pop("progress", None)
    meta["glossary"] = {**glossary, **(meta.get("glossary") or {})}
    meta["engine"] = "llm" if use_llm else "heuristic"
    meta["stage"] = "Done"
    meta["progress"] = 1.0
    db.update("documents", doc_id, {"status": "annotated", "meta": db.dumps(meta), "error": None})
    return {"created": created, "segments": len(segments), "engine": meta["engine"]}


def annotations_for(doc_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM annotations WHERE document_id = ? ORDER BY start_char", (doc_id,)
    )
    out = []
    for row in rows:
        meta = db.loads(row.get("meta"), {}) or {}
        out.append({
            "id": row["id"],
            "document_id": row["document_id"],
            "start_char": row["start_char"],
            "end_char": row["end_char"],
            "quote": row["quote"],
            "kind": row["kind"],
            "title": row["title"],
            "rationale": row["rationale"],
            "difficulty": row["difficulty"],
            "created_by": row["created_by"],
            "en": meta.get("en", ""),
            "created_at": row["created_at"],
        })
    return out


def glossary_for(doc_id: str | None) -> dict[str, str]:
    if not doc_id:
        return {}
    doc = db.one("SELECT meta FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        return {}
    meta = db.loads(doc["meta"], {}) or {}
    return meta.get("glossary") or {}
