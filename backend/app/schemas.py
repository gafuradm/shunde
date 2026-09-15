"""Pydantic-модели запросов/ответов API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnnotationKind = Literal["concept", "term", "fact", "unclear", "allusion", "grammar", "emphasis"]
ExplainMode = Literal["explain", "simplify", "translate", "example"]
Level = Literal["simple", "standard", "advanced"]


# ---------- документы ----------
class DocumentBrief(BaseModel):
    id: str
    title: str
    filename: str
    ext: str | None = None
    language: str | None = None
    script: str | None = None
    classical: float = 0.0
    char_count: int = 0
    status: str = "uploaded"
    error: str | None = None
    annotation_count: int = 0
    created_at: str


class DocumentFull(DocumentBrief):
    text: str = ""
    progress: float = 0.0
    stage: str | None = None
    engine: str | None = None
    glossary: dict[str, str] = {}


class AnnotationOut(BaseModel):
    id: str
    document_id: str
    start_char: int
    end_char: int
    quote: str
    kind: str
    title: str | None = None
    rationale: str | None = None
    difficulty: int = 3
    created_by: str = "ai"
    en: str = ""
    created_at: str


class AnnotationCreate(BaseModel):
    start_char: int
    end_char: int
    quote: str | None = None
    kind: AnnotationKind = "concept"
    title: str | None = None
    rationale: str | None = None
    difficulty: int = Field(3, ge=1, le=5)


class AnnotationPatch(BaseModel):
    kind: AnnotationKind | None = None
    title: str | None = None
    rationale: str | None = None
    difficulty: int | None = Field(None, ge=1, le=5)


class AiAnnotateRequest(BaseModel):
    mode: Literal["quick", "deep"] = "quick"
    kinds: list[AnnotationKind] | None = None
    max_per_segment: int | None = None
    replace_existing: bool = False
    only_segment: int | None = None
    # язык пояснений ИИ (title/rationale): по умолчанию — язык документа
    note_lang: str | None = None


# ---------- объяснения ----------
class ExplainRequest(BaseModel):
    document_id: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    quote: str | None = None
    context: str | None = None
    ui_lang: str = "en"
    level: Level = "standard"
    mode: ExplainMode = "explain"
    force: bool = False
    extra_query: str | None = None


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    provider: str = ""


# ---------- лекции ----------
class LectureCreate(BaseModel):
    title: str | None = None
    document_id: str | None = None
    target_lang: str = "en"
    source_lang: str = "zh-CN"


class LectureOut(BaseModel):
    id: str
    code: str
    title: str
    document_id: str | None = None
    target_lang: str = "en"
    source_lang: str = "zh-CN"
    status: str = "idle"
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None


class LectureLineOut(BaseModel):
    id: str
    seq: int
    source_text: str
    translated: str | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    is_final: bool = True
    created_at: str
