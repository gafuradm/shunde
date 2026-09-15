/** Тонкий клиент REST API бэкенда. */
import { getTeacherToken } from "./storage";
import type {
  AiAnnotateRequest,
  Annotation,
  AnnotationCreate,
  AnnotationPatch,
  DocumentBrief,
  DocumentFull,
  ExplainRequest,
  ExplainResult,
  Health,
  Lecture,
  LectureLine,
} from "./types";

export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export function wsUrl(path: string): string {
  const base =
    import.meta.env.VITE_WS_BASE ||
    `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return `${base.replace(/\/$/, "")}${path}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

function teacherHeaders(): Record<string, string> {
  const token = getTeacherToken();
  return token ? { "X-Teacher-Token": token } : {};
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  withTeacher = false,
): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...(withTeacher ? teacherHeaders() : {}),
    ...((init.headers as Record<string, string>) || {}),
  };
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/* ------------------------------------------------------------ служебное */
export const getHealth = () => request<Health>("/api/health");

/* ----------------------------------------------------------- документы */
export const listDocuments = () => request<DocumentBrief[]>("/api/documents");

export const getDocument = (id: string) => request<DocumentFull>(`/api/documents/${id}`);

export const getDocumentStatus = (id: string) =>
  request<Record<string, unknown>>(`/api/documents/${id}/status`);

export function uploadDocument(
  file: File,
  opts: { title?: string; annotate?: boolean; mode?: "quick" | "deep" } = {},
): Promise<DocumentFull> {
  const form = new FormData();
  form.append("file", file);
  if (opts.title) form.append("title", opts.title);
  form.append("annotate", String(Boolean(opts.annotate)));
  form.append("mode", opts.mode || "quick");
  return request<DocumentFull>("/api/documents", { method: "POST", body: form }, true);
}

export const deleteDocument = (id: string) =>
  request<{ deleted: string }>(`/api/documents/${id}`, { method: "DELETE" }, true);

export const getDocumentText = (id: string) =>
  fetch(`${API_BASE}/api/documents/${id}/text`).then((r) => r.text());

export function documentExportUrl(id: string): string {
  return `${API_BASE}/api/documents/${id}/export`;
}

/* ---------------------------------------------------------- аннотации */
export const listAnnotations = (docId: string) =>
  request<Annotation[]>(`/api/documents/${docId}/annotations`);

export const createAnnotation = (docId: string, payload: AnnotationCreate) =>
  request<Annotation>(`/api/documents/${docId}/annotations`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const patchAnnotation = (annId: string, payload: AnnotationPatch) =>
  request<Annotation>(`/api/annotations/${annId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteAnnotation = (annId: string) =>
  request<{ deleted: string }>(`/api/annotations/${annId}`, { method: "DELETE" }, true);

export const aiAnnotate = (docId: string, payload: AiAnnotateRequest = {}) =>
  request<{ status: string; engine: string; message?: string }>(
    `/api/documents/${docId}/annotations/ai`,
    { method: "POST", body: JSON.stringify(payload) },
    true,
  );

export const clearAnnotations = (docId: string, onlyAi = false) =>
  request<{ deleted: number }>(
    `/api/documents/${docId}/annotations?only_ai=${onlyAi ? "true" : "false"}`,
    { method: "DELETE" },
    true,
  );

/* --------------------------------------------------------- объяснения */
export const explainOnce = (payload: ExplainRequest) =>
  request<ExplainResult>("/api/explain", { method: "POST", body: JSON.stringify(payload) });

export const explainHistory = (docId?: string, limit = 50) =>
  request<Array<Record<string, unknown>>>(
    `/api/explain/history?limit=${limit}${docId ? `&document_id=${encodeURIComponent(docId)}` : ""}`,
  );

/* ------------------------------------------------------------ лекции */
export const listLectures = () => request<Lecture[]>("/api/lectures");

export const getLecture = (code: string) => request<Lecture>(`/api/lectures/${code}`);

export const createLecture = (payload: {
  title?: string;
  document_id?: string;
  source_lang?: string;
  target_lang?: string;
}) =>
  request<Lecture>("/api/lectures", { method: "POST", body: JSON.stringify(payload) }, true);

export const startLecture = (code: string) =>
  request<Lecture>(`/api/lectures/${code}/start`, { method: "POST" }, true);

export const endLecture = (code: string) =>
  request<Lecture>(`/api/lectures/${code}/end`, { method: "POST" }, true);

export const deleteLecture = (code: string) =>
  request<{ deleted: string }>(`/api/lectures/${code}`, { method: "DELETE" }, true);

export const lectureLines = (code: string, limit = 500) =>
  request<LectureLine[]>(`/api/lectures/${code}/lines?limit=${limit}`);

export const setLectureConfig = (
  code: string,
  payload: { source_lang?: string; target_lang?: string },
) =>
  request<Lecture>(`/api/lectures/${code}/config`, {
    method: "POST",
    body: JSON.stringify(payload),
  }, true);

export function lectureExportUrl(code: string, kind: "srt" | "markdown"): string {
  return `${API_BASE}/api/lectures/${code}/export/${kind}`;
}

/** Ссылка на комнату, которой делятся со студентами. */
export function roomUrl(code: string): string {
  return `${window.location.origin}/live/${code}`;
}
