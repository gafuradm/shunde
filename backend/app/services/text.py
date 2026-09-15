"""Лингвистические утилиты: определение языка/скрипта/вэньяня, сегментация, поиск цитат."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3005]")
KANA_RE = re.compile(r"[\u3040-\u30FF]")
HANGUL_RE = re.compile(r"[\uAC00-\uD7AF]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-z]")
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

# Классические (вэньянь) служебные слова и частицы
CLASSICAL_CHARS = set("之乎者也矣焉哉乃盖是以所夫其而与於于则皆亦且矣耳邪耶兮")
# Часто встречающиеся в современном китайском служебные слова/морфемы
MODERN_WORDS = ("的", "了", "这", "那", "们", "是", "很", "我", "你", "什么", "怎么", "没有", "可以")

# Канонические для вэньяня формулы
CLASSICAL_PATTERNS = (
    "者", "也", "曰", "所謂", "所谓", "何以", "何为", "是以", "是故", "故曰",
    "之谓", "之所以", "不亦", "乎哉", "焉", "矣", "無乃", "无乃", "盖",
)

SENTENCE_END = "。！？；!?;\n"


@dataclass
class LanguageInfo:
    language: str
    script: str
    classical: float

    @property
    def is_chinese(self) -> bool:
        return self.language.startswith("zh")


def detect_language(text: str) -> LanguageInfo:
    """Определяет язык (zh / zh-classical / en / ru / ja / ko / other), скрипт и «классичность»."""
    sample = text[:20000]
    total = max(1, len(sample))
    cjk = len(CJK_RE.findall(sample))
    kana = len(KANA_RE.findall(sample))
    hangul = len(HANGUL_RE.findall(sample))
    cyr = len(CYRILLIC_RE.findall(sample))
    lat = len(LATIN_RE.findall(sample))
    arab = len(ARABIC_RE.findall(sample))

    counts = {"han": cjk - kana, "kana": kana, "hangul": hangul, "cyrl": cyr, "latn": lat, "arab": arab}
    letters = sum(max(0, v) for v in counts.values()) or 1
    ratios = {k: v / letters for k, v in counts.items()}
    script = max(counts, key=lambda k: counts[k])

    if ratios["kana"] > 0.15 and counts["kana"] >= 10:
        lang, script_out = "ja", "jpan"
    elif ratios["hangul"] > 0.3:
        lang, script_out = "ko", "hang"
    elif ratios["cyrl"] > 0.3:
        lang, script_out = "ru", "cyrl"
    # китайский имеет приоритет над латиницей: учебные материалы часто содержат
    # английские глоссы/переводы при основном китайском тексте
    elif ratios["han"] > 0.20 or (counts["han"] >= 30 and ratios["han"] > 0.08):
        lang, script_out = "zh", _han_script(sample)
    elif ratios["latn"] > 0.3:
        lang, script_out = "en", "latn"
    elif ratios["arab"] > 0.3:
        lang, script_out = "ar", "arab"
    else:
        lang, script_out = "other", "other"

    # классичность считаем всегда, когда иероглифов достаточно много —
    # даже если формально победила латиница (смешанные двуязычные тексты)
    classical = classical_score(sample) if counts["han"] >= 20 else 0.0
    if classical >= 0.62 and lang in ("zh", "en", "other"):
        lang, script_out = "zh-classical", _han_script(sample)
    return LanguageInfo(language=lang, script=script_out, classical=round(classical, 3))


def _han_script(text: str) -> str:
    """Упрощённые vs традиционные иероглифы (грубая эвристика по частотным парам)."""
    trad = sum(text.count(ch) for ch in "車門馬鳥魚龍義學國書與為東華廣發會體實寶語說讀師")
    simp = sum(text.count(ch) for ch in "车门马鸟鱼龙义学国书与为东华广发会体实宝语说读师")
    if trad > simp * 1.5 and trad > 5:
        return "hant"
    return "hans"


def classical_score(text: str) -> float:
    """0..1 — насколько текст похож на вэньянь (классический китайский)."""
    cjk = len(CJK_RE.findall(text))
    if cjk < 20:
        return 0.0
    classical_hits = sum(text.count(ch) for ch in CLASSICAL_CHARS)
    pattern_hits = sum(text.count(p) for p in CLASSICAL_PATTERNS) * 2
    modern_hits = sum(text.count(w) for w in MODERN_WORDS)
    classical_hits += pattern_hits

    ratio = classical_hits / max(1, classical_hits + modern_hits * 1.6)
    # вэньянь — короткие фразы; средняя длина «предложения» обычно < 40 иероглифов
    sentences = [s for s in re.split(r"[。！？；\n]", text) if s.strip()]
    avg_len = (sum(len(s) for s in sentences) / len(sentences)) if sentences else 0
    brevity = 1.0 if avg_len <= 40 else max(0.55, 40 / max(avg_len, 1))
    score = ratio * brevity
    return max(0.0, min(1.0, score))


def has_modern_particles(text: str) -> bool:
    return sum(text.count(w) for w in ("的", "了", "这", "那", "们")) > max(2, len(text) // 120)


# ---------------------------------------------------------------- сегментация
@dataclass
class Segment:
    index: int
    start: int  # смещение в code points
    end: int
    text: str


def split_segments(text: str, max_len: int = 900, min_len: int = 40) -> list[Segment]:
    """Режет текст на смысловые сегменты (абзацы/предложения) с сохранением смещений."""
    if not text:
        return []
    out: list[Segment] = []
    buf_start: int | None = None
    buf_len = 0

    def flush(end: int) -> None:
        nonlocal buf_start, buf_len
        if buf_start is not None and end > buf_start:
            chunk = text[buf_start:end]
            if chunk.strip():
                out.append(Segment(len(out), buf_start, end, chunk))
        buf_start, buf_len = None, 0

    i = 0
    n = len(text)
    while i < n:
        if buf_start is None:
            buf_start, buf_len = i, 0
        ch = text[i]
        buf_len += 1
        hard_break = ch == "\n"
        soft_break = ch in "。！？!?" or (ch == "；" and buf_len > max_len * 0.5)
        if (hard_break and buf_len >= min_len) or (soft_break and buf_len >= max_len * 0.6) or buf_len >= max_len:
            flush(i + 1)
        i += 1
    flush(n)

    if not out and text.strip():
        return [Segment(0, 0, len(text), text)]
    return out


def reindex(segments: list[Segment]) -> list[Segment]:
    return [Segment(i, s.start, s.end, s.text) for i, s in enumerate(segments)]


def context_window(text: str, start: int, end: int, radius: int = 700) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


# ---------------------------------------------------------------- поиск цитат
def normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s


def find_quote(text: str, quote: str, lo: int = 0, hi: int | None = None) -> tuple[int, int] | None:
    """Находит точную позицию цитаты в тексте (точное совпадение → без пробелов → нечёткое)."""
    quote = (quote or "").strip().strip("\"'“”「」『』")
    if len(quote) < 2:
        return None
    hi = len(text) if hi is None else min(hi, len(text))
    window = text[lo:hi]

    pos = window.find(quote)
    if pos >= 0:
        return lo + pos, lo + pos + len(quote)

    # игнорируем различия в пробелах/переводах строк
    flat_quote = normalize_for_match(quote)
    if not flat_quote:
        return None
    flat_window = normalize_for_match(window)
    fpos = flat_window.find(flat_quote)
    if fpos >= 0:
        # переводим «плоский» индекс обратно в исходный, пропуская пробелы
        idx = -1
        count = -1
        for i, ch in enumerate(window):
            if not re.match(r"\s", ch):
                count += 1
            if count == fpos:
                idx = i
                break
        if idx >= 0:
            end_idx = idx
            seen = 0
            for j in range(idx, len(window)):
                if not re.match(r"\s", window[j]):
                    seen += 1
                end_idx = j
                if seen >= len(flat_quote):
                    break
            return lo + idx, lo + min(end_idx + 1, len(window))

    # нечёткий поиск: скользящее окно + difflib
    if len(quote) > 60:
        quote = quote[:60]
    sm = difflib.SequenceMatcher()
    sm.set_seq2(quote)
    best_ratio, best_span = 0.0, None
    win = len(quote)
    step = max(1, win // 4)
    for start in range(0, max(1, len(window) - win), step):
        candidate = window[start:start + win + 4]
        sm.set_seq1(candidate)
        ratio = sm.ratio()
        if ratio > best_ratio:
            best_ratio, best_span = ratio, (start, start + len(candidate))
    if best_span and best_ratio >= 0.72:
        return lo + best_span[0], lo + best_span[1]
    return None


def clip_quote(text: str, start: int, end: int, max_len: int = 160) -> str:
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    q = text[start:end]
    return q if len(q) <= max_len else q[:max_len]


def excerpt(text: str, limit: int = 1200) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"
