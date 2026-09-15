"""Извлечение текста из документов «любого» формата.

Поддерживается: pdf, docx (и docm), pptx, xlsx/xlsm, csv/tsv, txt/md/код/tex,
html/htm, rtf, odt/ods/odp, epub, srt/vtt, json/xml/yaml, zip-архивы с поддерживаемыми
файлами, изображения (OCR через pytesseract, опционально; иначе — через vision-LLM).
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from html import unescape

from bs4 import BeautifulSoup

from .text import CJK_RE

MAX_ARCHIVE_MB = 60


@dataclass
class ExtractResult:
    text: str
    extractor: str = "unknown"
    encoding: str | None = None
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


class ExtractError(RuntimeError):
    pass


PLAIN_TEXT_EXTS = {
    ".txt", ".text", ".md", ".markdown", ".rst", ".log", ".me", ".tex", ".bib",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".sql", ".sh", ".bat", ".r",
    ".xml", ".xsl", ".svg", ".wiki", ".po", ".properties", ".srt", ".vtt", ".ass",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic"}


# ------------------------------------------------------------------ helpers
def decode_bytes(data: bytes) -> tuple[str, str, list[str]]:
    """Подбирает кодировку: utf-8 → BOM → gb18030/big5 → cp1251 → latin-1."""
    warnings: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace"), "utf-8-sig", warnings
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16", warnings
    try:
        return data.decode("utf-8"), "utf-8", warnings
    except UnicodeDecodeError:
        pass

    sample = data[:400000]
    candidates: list[tuple[str, str]] = []
    for enc in ("gb18030", "big5", "shift_jis", "euc_kr", "cp1251", "cp1252", "koi8-r"):
        try:
            candidates.append((enc, sample.decode(enc)))
        except (UnicodeDecodeError, LookupError):
            continue

    def quality(txt: str) -> float:
        if not txt:
            return 0.0
        good = len(re.findall(r"[\w\s\u3000-\u303F\uFF00-\uFFEF]", txt))
        cjk = len(CJK_RE.findall(txt))
        weird = txt.count("\ufffd") * 5
        return (good + cjk * 0.5 - weird) / max(1, len(txt))

    if candidates:
        best = max(candidates, key=lambda c: quality(c[1]))
        warnings.append(f"encoding detected as {best[0]} (the file is not UTF-8)")
        return best[1], best[0], warnings
    return data.decode("latin-1", errors="replace"), "latin-1", warnings


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"]):
        block.append("\n")
    text = soup.get_text()
    text = unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f]", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _strip_subtitles(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*\d+\s*", line):
            continue
        if "-->" in line:
            continue
        if line.strip().upper().startswith(("WEBVTT", "NOTE ")):
            continue
        lines.append(re.sub(r"<[^>]+>", "", line))
    return "\n".join(lines)


def _strip_tex(text: str) -> str:
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    text = re.sub(r"\\(begin|end)\{[^}]*\}", "\n", text)
    text = re.sub(r"\\(cite|label|ref|includegraphics|usepackage|documentclass)\{[^}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\n{3,}", "\n\n", text)


# ------------------------------------------------------------------ extractors
def _pdf(data: bytes) -> ExtractResult:
    res = ExtractResult(text="", extractor="pypdf")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ExtractError("pypdf is not installed (pip install -r requirements.txt)") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages: list[str] = []
        for i, page in enumerate(reader.pages):
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001
                res.warnings.append(f"page {i + 1}: {exc}")
                pages.append("")
        res.meta["pages"] = len(pages)
        res.text = _clean("\n\n".join(pages))
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"Could not read the PDF: {exc}") from exc

    if len(res.text) < 200:
        res.warnings.append(
            "Very little text was extracted from the PDF — it is probably a scan. Enable OCR "
            "(pytesseract) or LLM_VISION=true to recognise images."
        )
    return res


def _docx(data: bytes) -> ExtractResult:
    from docx import Document  # python-docx

    doc = Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    meta = {}
    try:
        core = doc.core_properties
        meta = {"author": core.author or "", "title": core.title or ""}
    except Exception:  # noqa: BLE001
        pass
    return ExtractResult(text=_clean("\n".join(parts)), extractor="python-docx", meta=meta)


def _pptx(data: bytes) -> ExtractResult:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    chunks: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        chunks.append(f"\n## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        chunks.append(line)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    if any(cells):
                        chunks.append(" | ".join(cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            chunks.append("Notes: " + slide.notes_slide.notes_text_frame.text.strip())
    return ExtractResult(text=_clean("\n".join(chunks)), extractor="python-pptx", meta={"slides": len(prs.slides)})


def _xlsx(data: bytes) -> ExtractResult:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    chunks: list[str] = []
    for ws in wb.worksheets:
        chunks.append(f"\n## Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                chunks.append(" | ".join(cells).rstrip(" |"))
    return ExtractResult(text=_clean("\n".join(chunks)), extractor="openpyxl", meta={"sheets": len(wb.worksheets)})


def _rtf(data: bytes) -> ExtractResult:
    from striprtf.striprtf import rtf_to_text

    raw, enc, warns = decode_bytes(data)
    return ExtractResult(text=_clean(rtf_to_text(raw)), extractor="striprtf", encoding=enc, warnings=warns)


def _odf(data: bytes) -> ExtractResult:
    """odt/ods/odp — это zip с content.xml."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("content.xml").decode("utf-8", errors="replace")
    soup = BeautifulSoup(xml, "html.parser")
    for tag in soup.find_all(["text:p", "text:h", "table:table-cell", "text:list-item"]):
        tag.append("\n")
    for tag in soup.find_all("table:table-row"):
        tag.append("\n")
    return ExtractResult(text=_clean(soup.get_text("")), extractor="odf(zip+xml)")


def _epub(data: bytes) -> ExtractResult:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        opf_name = next((n for n in names if n.lower().endswith(".opf")), None)
        order: list[str] = []
        base = ""
        if opf_name:
            base = os.path.dirname(opf_name)
            opf = zf.read(opf_name).decode("utf-8", errors="replace")
            manifest = dict(re.findall(r'<item[^>]+id="([^"]+)"[^>]+href="([^"]+)"', opf))
            manifest.update({i: h for h, i in re.findall(r'<item[^>]+href="([^"]+)"[^>]+id="([^"]+)"', opf)})
            for idref in re.findall(r'<itemref[^>]+idref="([^"]+)"', opf):
                href = manifest.get(idref)
                if href:
                    order.append(os.path.normpath(os.path.join(base, href)))
        if not order:
            order = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
        chunks: list[str] = []
        for name in order:
            try:
                chunks.append(_html_to_text(zf.read(name).decode("utf-8", errors="replace")))
            except KeyError:
                continue
    return ExtractResult(text=_clean("\n\n".join(chunks)), extractor="epub")


def _zip_archive(data: bytes) -> ExtractResult:
    if len(data) > MAX_ARCHIVE_MB * 1024 * 1024:
        raise ExtractError(f"The archive is larger than {MAX_ARCHIVE_MB} MB — unzip it manually")
    chunks: list[str] = []
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()][:60]
        for info in infos:
            ext = os.path.splitext(info.filename)[1].lower()
            if info.file_size > 20 * 1024 * 1024:
                warnings.append(f"{info.filename}: skipped (>20 MB)")
                continue
            if ext not in PLAIN_TEXT_EXTS and ext not in {".html", ".htm", ".docx", ".pdf", ".rtf"}:
                continue
            payload = zf.read(info)
            try:
                if ext in {".html", ".htm"}:
                    sub = ExtractResult(text=_html_to_text(decode_bytes(payload)[0]), extractor="html")
                elif ext == ".docx":
                    sub = _docx(payload)
                elif ext == ".pdf":
                    sub = _pdf(payload)
                elif ext == ".rtf":
                    sub = _rtf(payload)
                else:
                    sub = ExtractResult(text=_clean(decode_bytes(payload)[0]), extractor="plain")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{info.filename}: {exc}")
                continue
            if sub.text.strip():
                chunks.append(f"\n\n### File: {info.filename}\n{sub.text}")
    if not chunks:
        raise ExtractError("No supported text files were found inside the archive")
    return ExtractResult(text=_clean("\n".join(chunks)), extractor="zip", warnings=warnings)


def _ocr_image(data: bytes, ext: str) -> ExtractResult:
    res = ExtractResult(text="", extractor="image")
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        res.warnings.append(
            "OCR is unavailable (pytesseract/Pillow missing). Install them or enable LLM_VISION=true."
        )
        return res
    try:
        img = Image.open(io.BytesIO(data))
        langs = "chi_sim+chi_tra+eng"
        try:
            text = pytesseract.image_to_string(img, lang=langs)
            res.extractor = "pytesseract(chi+eng)"
        except Exception:  # noqa: BLE001
            text = pytesseract.image_to_string(img)
            res.extractor = "pytesseract(default)"
        res.text = _clean(text)
    except Exception as exc:  # noqa: BLE001
        res.warnings.append(f"OCR failed: {exc}")
    if not res.text.strip():
        res.warnings.append("No text was recognised in the image")
    return res


def _plain(data: bytes, ext: str) -> ExtractResult:
    res = ExtractResult(text="", extractor="plain")
    text, enc, warns = decode_bytes(data)
    res.encoding, res.warnings = enc, warns
    if ext in {".html", ".htm", ".xml", ".xhtml"}:
        text = _html_to_text(text)
        res.extractor = "html"
    elif ext in {".srt", ".vtt", ".ass"}:
        text = _strip_subtitles(text)
        res.extractor = "subtitles"
    elif ext == ".tex":
        text = _strip_tex(text)
        res.extractor = "latex"
    elif ext == ".csv":
        text = "\n".join(" | ".join(c.strip() for c in line.split(",")) for line in text.splitlines())
        res.extractor = "csv"
    elif ext == ".tsv":
        text = "\n".join(" | ".join(c.strip() for c in line.split("\t")) for line in text.splitlines())
        res.extractor = "tsv"
    res.text = _clean(text)
    return res


# ------------------------------------------------------------------ API
def extract_text(data: bytes, filename: str) -> ExtractResult:
    """Определяет формат и извлекает текст. Бросает ExtractError, если совсем не получилось."""
    ext = os.path.splitext(filename or "")[1].lower()
    if not data:
        raise ExtractError("Empty file")

    try:
        if ext == ".pdf":
            return _pdf(data)
        if ext in {".docx", ".docm"}:
            return _docx(data)
        if ext in {".pptx", ".pptm"}:
            return _pptx(data)
        if ext in {".xlsx", ".xlsm"}:
            return _xlsx(data)
        if ext == ".rtf":
            return _rtf(data)
        if ext in {".odt", ".ods", ".odp"}:
            return _odf(data)
        if ext == ".epub":
            return _epub(data)
        if ext == ".zip":
            return _zip_archive(data)
        if ext in IMAGE_EXTS:
            return _ocr_image(data, ext)
        if ext in PLAIN_TEXT_EXTS:
            return _plain(data, ext)
        if ext in {".doc", ".xls", ".ppt"}:
            res = _plain(data, ext)
            res.extractor = f"legacy-{ext[1:]}"
            res.warnings.append(
                f"Legacy binary format {ext}: the text was extracted as-is. "
                f"For a good result save the file as {ext}x."
            )
            if len(res.text) < 30:
                raise ExtractError(f"Format {ext} is not supported — convert the file to {ext}x or PDF")
            return res

        # неизвестное расширение — пробуем как текст, затем как zip (docx/odt без расширения)
        if data[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    names = set(zf.namelist())
                if "word/document.xml" in names:
                    return _docx(data)
                if "content.xml" in names:
                    return _odf(data)
                if "mimetype" in names:
                    return _epub(data)
            except zipfile.BadZipFile:
                pass
        res = _plain(data, ext)
        if res.text and len(re.findall(r"[\ufffd\x00-\x08]", res.text)) < len(res.text) * 0.02:
            res.warnings.append(f"Unknown extension “{ext or '—'}”, the text was read as plain text")
            return res
        raise ExtractError(f"Format “{ext or 'no extension'}” is not supported")
    except ExtractError:
        raise
    except ImportError as exc:
        raise ExtractError(f"Missing library for {ext or 'this format'}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"Text extraction error ({ext}): {exc}") from exc
