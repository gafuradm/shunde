import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import ExplainPanel, { type ExplainTarget } from "../components/ExplainPanel";
import { kindLabel, useI18n } from "../lib/i18n";
import {
  aiAnnotate,
  ApiError,
  clearAnnotations,
  createAnnotation,
  deleteAnnotation,
  documentExportUrl,
  getDocument,
  listAnnotations,
} from "../lib/api";
import { getTeacherToken, loadNotes, saveNote } from "../lib/storage";
import type { Annotation, AnnotationKind, DocumentFull } from "../lib/types";

const KINDS: AnnotationKind[] = [
  "concept",
  "term",
  "fact",
  "unclear",
  "allusion",
  "grammar",
  "emphasis",
];

interface Segment {
  start: number;
  end: number;
  ann?: Annotation;
}

/** Непересекающиеся интервалы: первая (самая широкая) аннотация выигрывает. */
function buildSegments(text: string, annotations: Annotation[]): Segment[] {
  const usable = annotations
    .filter((a) => a.end_char > a.start_char && a.start_char >= 0 && a.end_char <= text.length)
    .sort((a, b) => a.start_char - b.start_char || b.end_char - a.end_char);

  const chosen: Annotation[] = [];
  let cursor = 0;
  for (const ann of usable) {
    if (ann.start_char >= cursor) {
      chosen.push(ann);
      cursor = ann.end_char;
    }
  }

  const segments: Segment[] = [];
  let pos = 0;
  for (const ann of chosen) {
    if (ann.start_char > pos) segments.push({ start: pos, end: ann.start_char });
    segments.push({ start: ann.start_char, end: ann.end_char, ann });
    pos = ann.end_char;
  }
  if (pos < text.length) segments.push({ start: pos, end: text.length });
  return segments.filter((seg) => seg.end > seg.start);
}

/** Смещение в тексте документа по точке DOM-выделения. */
function offsetFromNode(node: Node | null, offset: number): number {
  if (!node) return -1;
  const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement);
  const holder = element?.closest<HTMLElement>("[data-start]");
  if (!holder) return -1;
  const base = Number(holder.dataset.start || 0);
  return base + offset;
}

export default function DocumentReaderPage() {
  const { docId = "" } = useParams();
  const { t } = useI18n();
  const teacherToken = getTeacherToken();

  const [doc, setDoc] = useState<DocumentFull | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [pinned, setPinned] = useState<ExplainTarget | null>(null);
  const [hoverPoint, setHoverPoint] = useState<{ x: number; y: number } | null>(null);
  const [selection, setSelection] = useState<{ start: number; end: number; text: string; x: number; y: number } | null>(
    null,
  );
  const [panelOpen, setPanelOpen] = useState(true);
  const hoverTimer = useRef<number | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [document, anns] = await Promise.all([getDocument(docId), listAnnotations(docId)]);
      setDoc(document);
      setAnnotations(anns);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    setLoading(true);
    load();
    setNote(loadNotes()[docId] || "");
  }, [docId, load]);

  // Документ ещё размечается — подтягиваем прогресс.
  useEffect(() => {
    if (!doc || doc.status !== "annotating") return;
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [doc, load]);

  const segments = useMemo(
    () => (doc ? buildSegments(doc.text, annotations) : []),
    [doc, annotations],
  );

  const hoverExplain = (ann: Annotation, event: React.MouseEvent) => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
    const point = { x: event.clientX, y: event.clientY };
    hoverTimer.current = window.setTimeout(() => {
      setPinned({ quote: ann.quote, start: ann.start_char, end: ann.end_char, annotation: ann });
      setHoverPoint(point);
    }, 220);
  };

  const cancelHover = () => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
  };

  const onMouseUp = () => {
    if (!doc || !textRef.current) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setSelection(null);
      return;
    }
    const range = sel.getRangeAt(0);
    if (!textRef.current.contains(range.commonAncestorContainer)) {
      setSelection(null);
      return;
    }
    let start = offsetFromNode(range.startContainer, range.startOffset);
    let end = offsetFromNode(range.endContainer, range.endOffset);
    if (start < 0 || end < 0) return;
    if (start > end) [start, end] = [end, start];
    const quote = doc.text.slice(start, end);
    if (!quote.trim()) {
      setSelection(null);
      return;
    }
    const rect = range.getBoundingClientRect();
    setSelection({ start, end, text: quote, x: rect.left + rect.width / 2, y: rect.top });
  };

  const addAnnotation = async (kind: AnnotationKind) => {
    if (!selection || !doc) return;
    setBusy("manual");
    try {
      const created = await createAnnotation(doc.id, {
        start_char: selection.start,
        end_char: selection.end,
        quote: selection.text,
        kind,
      });
      setAnnotations((prev) => [...prev, created].sort((a, b) => a.start_char - b.start_char));
      window.getSelection()?.removeAllRanges();
      setSelection(null);
      setPinned({
        quote: created.quote,
        start: created.start_char,
        end: created.end_char,
        annotation: created,
      });
      setHoverPoint({ x: Math.max(8, window.innerWidth - 420), y: 140 });
      setError("");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    } finally {
      setBusy("");
    }
  };

  const runAi = async (mode: "quick" | "deep") => {
    if (!doc) return;
    setBusy(mode);
    try {
      await aiAnnotate(doc.id, { mode, note_lang: "ru", replace_existing: true });
      setDoc({ ...doc, status: "annotating", progress: 0 });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    } finally {
      setBusy("");
    }
  };

  const clearAi = async () => {
    if (!doc) return;
    setBusy("clear");
    try {
      await clearAnnotations(doc.id, true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    } finally {
      setBusy("");
    }
  };

  const removeAnnotation = async (ann: Annotation) => {
    try {
      await deleteAnnotation(ann.id);
      setAnnotations((prev) => prev.filter((item) => item.id !== ann.id));
      setPinned((prev) => (prev?.annotation?.id === ann.id ? null : prev));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const glossaryEntries = useMemo(
    () => Object.entries(doc?.glossary || {}),
    [doc?.glossary],
  );

  if (loading) return <div className="card p-6 text-sm text-ink-600">{t("common.loading")}</div>;
  if (error && !doc)
    return <div className="card border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">{error}</div>;
  if (!doc) return null;

  // Панель объяснения не должна вылезать за пределы экрана (важно для телефонов).
  const panelWidth = Math.min(400, Math.max(260, window.innerWidth - 24));
  const panelX = hoverPoint
    ? Math.max(8, Math.min(hoverPoint.x + 16, window.innerWidth - panelWidth - 8))
    : 8;
  const panelY = hoverPoint
    ? Math.max(8, Math.min(hoverPoint.y + 16, Math.max(80, window.innerHeight - 460)))
    : 8;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/documents" className="btn-quiet">
          ← {t("reader.backToList")}
        </Link>
        <h1 className="font-han text-lg font-bold">{doc.title}</h1>
        <span className="chip">{doc.language || "—"}</span>
        {doc.classical >= 0.6 && (
          <span className="badge bg-violet-100 text-violet-700">{t("docs.classical")}</span>
        )}
        <span className={`badge ${doc.status === "annotating" ? "bg-amber-100 text-amber-700" : "bg-ink-100 text-ink-600"}`}>
          {doc.status === "annotating" ? `${t("annotate.running")} ${Math.round((doc.progress || 0) * 100)}%` : doc.status}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setPanelOpen((v) => !v)}
          >
            {panelOpen ? t("reader.hideComments") : t("reader.showComments")}
          </button>
          <a className="btn-ghost" href={documentExportUrl(doc.id)} target="_blank" rel="noreferrer noopener">
            {t("reader.exportMd")}
          </a>
        </div>
      </div>

      {teacherToken && (
        <div className="card flex flex-wrap items-center gap-2 p-2.5 text-xs">
          <span className="font-semibold text-ink-600">{t("reader.annotateAi")}:</span>
          <button type="button" className="btn-primary px-2.5 py-1" disabled={Boolean(busy)} onClick={() => runAi("quick")}>
            {t("reader.annotateQuick")}
          </button>
          <button type="button" className="btn-ghost px-2.5 py-1" disabled={Boolean(busy)} onClick={() => runAi("deep")}>
            {t("reader.annotateDeep")}
          </button>
          <button type="button" className="btn-quiet px-2.5 py-1 text-rose-600" disabled={Boolean(busy)} onClick={clearAi}>
            {t("reader.clearAi")}
          </button>
          <span className="mx-1 h-4 w-px bg-ink-200" />
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={manualMode} onChange={(e) => setManualMode(e.target.checked)} />
            {t("reader.manualMode")}
          </label>
          <span className="text-ink-600">{t("reader.manualHint")}</span>
        </div>
      )}

      {error && doc && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      <div className={`grid gap-4 ${panelOpen ? "lg:grid-cols-[minmax(0,1fr)_360px]" : ""}`}>
        <div className="card min-w-0 p-5">
          <div
            ref={textRef}
            className="doc-text select-text"
            onMouseUp={onMouseUp}
            onMouseLeave={() => {
              cancelHover();
              setSelection(null);
            }}
          >
            {segments.map((seg) =>
              seg.ann ? (
                <span
                  key={`${seg.start}-${seg.ann.id}`}
                  data-start={seg.start}
                  className={`mk mk-${seg.ann.kind} ${
                    pinned?.annotation?.id === seg.ann.id ? "mk-active" : ""
                  }`}
                  onMouseEnter={(event) => hoverExplain(seg.ann!, event)}
                  onMouseLeave={cancelHover}
                  onClick={(event) => {
                    setPinned({
                      quote: seg.ann!.quote,
                      start: seg.ann!.start_char,
                      end: seg.ann!.end_char,
                      annotation: seg.ann,
                    });
                    setHoverPoint({ x: event.clientX, y: event.clientY });
                  }}
                >
                  {doc.text.slice(seg.start, seg.end)}
                </span>
              ) : (
                <span key={seg.start} data-start={seg.start}>
                  {doc.text.slice(seg.start, seg.end)}
                </span>
              ),
            )}
          </div>
        </div>

        {panelOpen && (
          <aside className="min-w-0 space-y-4 lg:order-none">
            <div className="card p-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">{t("reader.legend")}</h2>
                <span className="text-xs text-ink-600">
                  {annotations.length} / {t("docs.col.annotations")}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {KINDS.map((kind) => (
                  <span key={kind} className={`chip mk-${kind}`}>
                    {kindLabel(kind, t)}
                  </span>
                ))}
              </div>
              <ul className="mt-2 max-h-[38vh] space-y-1 overflow-y-auto scroll-thin">
                {annotations.length === 0 && (
                  <li className="text-xs text-ink-600">{t("reader.noAnnotations")}</li>
                )}
                {annotations.map((ann) => (
                  <li key={ann.id} className="flex items-start gap-2 rounded-lg px-2 py-1 hover:bg-ink-50">
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => {
                        setPinned({
                          quote: ann.quote,
                          start: ann.start_char,
                          end: ann.end_char,
                          annotation: ann,
                        });
                        setHoverPoint({ x: Math.max(8, window.innerWidth - 480), y: 160 });
                      }}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className={`badge bg-ink-100 text-ink-700 mk-${ann.kind}`}>
                          {kindLabel(ann.kind, t)}
                        </span>
                        <span className="font-han truncate text-xs text-ink-800">{ann.quote}</span>
                      </div>
                      {ann.title && <div className="mt-1 text-xs text-ink-600">{ann.title}</div>}
                    </button>
                    {teacherToken && (
                      <button
                        type="button"
                        className="btn-quiet px-1.5 py-0.5 text-xs text-rose-600"
                        onClick={() => removeAnnotation(ann)}
                        title={t("common.delete")}
                      >
                        ✕
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            {glossaryEntries.length > 0 && (
              <div className="card p-3">
                <h2 className="text-sm font-semibold">{t("reader.glossary")}</h2>
                <dl className="mt-2 max-h-[30vh] space-y-1 overflow-y-auto scroll-thin text-xs">
                  {glossaryEntries.map(([term, meaning]) => (
                    <div key={term}>
                      <dt className="font-han font-semibold text-ink-800">{term}</dt>
                      <dd className="text-ink-600">{meaning}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}

            <div className="card p-3">
              <h2 className="text-sm font-semibold">{t("reader.notes")}</h2>
              <p className="mt-0.5 text-[11px] text-ink-600">{t("reader.notesHint")}</p>
              <textarea
                className="input mt-2 h-32 resize-y"
                value={note}
                onChange={(event) => {
                  setNote(event.target.value);
                  saveNote(doc.id, event.target.value);
                }}
              />
            </div>
          </aside>
        )}
      </div>

      {manualMode && selection && (
        <div
          className="fixed z-40 -translate-x-1/2 -translate-y-full animate-fade-in"
          style={{
            left: Math.max(96, Math.min(selection.x, window.innerWidth - 96)),
            top: Math.max(selection.y - 8, 70),
          }}
        >
          <div className="card flex flex-wrap items-center gap-1 p-1.5 shadow-lg">
            <span className="px-1 font-han text-xs text-ink-600">{selection.text.slice(0, 24)}</span>
            {KINDS.map((kind) => (
              <button
                key={kind}
                type="button"
                className={`chip mk-${kind}`}
                disabled={busy === "manual"}
                onClick={() => addAnnotation(kind)}
              >
                {kindLabel(kind, t)}
              </button>
            ))}
          </div>
        </div>
      )}

      {pinned && (
        <div className="fixed z-50" style={{ left: panelX, top: panelY, width: panelWidth }}>
          <ExplainPanel
            documentId={doc.id}
            target={pinned}
            onClose={() => setPinned(null)}
            className="shadow-2xl"
          />
        </div>
      )}
    </div>
  );
}
