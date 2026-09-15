/** Типы ответов API (см. backend/app/schemas.py). */

export type AnnotationKind =
  | "concept"
  | "term"
  | "fact"
  | "unclear"
  | "allusion"
  | "grammar"
  | "emphasis";

export type ExplainMode = "explain" | "simplify" | "translate" | "example";
export type Level = "simple" | "standard" | "advanced";

export interface HealthFeatures {
  llm: boolean;
  llm_model: string | null;
  llm_base_url: string | null;
  vision_ocr: boolean;
  server_asr: boolean;
  web_search: Record<string, boolean>;
  free_translate_fallback: boolean;
  teacher_token_required: boolean;
}

export interface Health {
  status: string;
  app: string;
  features: HealthFeatures;
}

export interface DocumentBrief {
  id: string;
  title: string;
  filename: string;
  ext?: string | null;
  language?: string | null;
  script?: string | null;
  classical: number;
  char_count: number;
  status: string;
  error?: string | null;
  annotation_count: number;
  created_at: string;
}

export interface DocumentFull extends DocumentBrief {
  text: string;
  progress: number;
  stage?: string | null;
  engine?: string | null;
  glossary: Record<string, string>;
}

export interface Annotation {
  id: string;
  document_id: string;
  start_char: number;
  end_char: number;
  quote: string;
  kind: string;
  title?: string | null;
  rationale?: string | null;
  difficulty: number;
  created_by: string;
  en: string;
  created_at: string;
}

export interface AnnotationCreate {
  start_char: number;
  end_char: number;
  quote?: string;
  kind: AnnotationKind;
  title?: string;
  rationale?: string;
  difficulty?: number;
}

export interface AnnotationPatch {
  kind?: AnnotationKind;
  title?: string;
  rationale?: string;
  difficulty?: number;
}

export interface AiAnnotateRequest {
  mode?: "quick" | "deep";
  kinds?: AnnotationKind[];
  max_per_segment?: number;
  replace_existing?: boolean;
  only_segment?: number;
  note_lang?: string;
}

export interface ExplainRequest {
  document_id?: string;
  start_char?: number;
  end_char?: number;
  quote?: string;
  context?: string;
  ui_lang?: string;
  level?: Level;
  mode?: ExplainMode;
  force?: boolean;
  extra_query?: string;
}

export interface Source {
  title: string;
  url: string;
  snippet?: string;
  provider?: string;
}

export interface ExplainResult {
  answer: string;
  sources: Source[];
  degraded: boolean;
  engine: string;
}

export interface Lecture {
  id: string;
  code: string;
  title: string;
  document_id?: string | null;
  target_lang: string;
  source_lang: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface LectureLine {
  id: string;
  seq: number;
  source_text: string;
  translated?: string | null;
  source_lang?: string | null;
  target_lang?: string | null;
  is_final: boolean;
  created_at: string;
}

/** Состояние комнаты (ws hello.state / config). */
export interface RoomState {
  code: string;
  title: string;
  status: string;
  source_lang: string;
  target_lang: string;
  document_id?: string | null;
  partial: string;
  audio: boolean;
  viewers: number;
  hosts: number;
  total: number;
}

export interface RoomLine {
  id: string;
  seq: number;
  text: string;
  translation: string | null;
  target_lang: string;
  final: boolean;
  created_at: string;
}
