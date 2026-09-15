import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useI18n } from "../lib/i18n";
import {
  aiAnnotate,
  ApiError,
  deleteDocument,
  documentExportUrl,
  getDocument,
  listDocuments,
  uploadDocument,
} from "../lib/api";
import { getTeacherToken } from "../lib/storage";
import type { DocumentBrief } from "../lib/types";

const STATUS_STYLE: Record<string, string> = {
  uploaded: "bg-ink-100 text-ink-600",
  extracted: "bg-sky-100 text-sky-700",
  annotating: "bg-amber-100 text-amber-700",
  annotated: "bg-emerald-100 text-emerald-700",
  error: "bg-rose-100 text-rose-700",
};

function DocActions({
  doc,
  teacherToken,
  busy,
  onAnnotate,
  onDelete,
  className = "justify-end",
}: {
  doc: DocumentBrief;
  teacherToken: string | null;
  busy: string;
  onAnnotate: (docId: string, mode: "quick" | "deep") => void;
  onDelete: (doc: DocumentBrief) => void;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      <Link to={`/documents/${doc.id}`} className="btn-ghost px-2 py-1 text-xs">
        {t("common.open")}
      </Link>
      {teacherToken && (
        <>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            disabled={busy === `annotate:${doc.id}`}
            onClick={() => onAnnotate(doc.id, "quick")}
          >
            {t("reader.annotateQuick")}
          </button>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            disabled={busy === `annotate:${doc.id}`}
            onClick={() => onAnnotate(doc.id, "deep")}
          >
            {t("reader.annotateDeep")}
          </button>
          <a
            className="btn-ghost px-2 py-1 text-xs"
            href={documentExportUrl(doc.id)}
            target="_blank"
            rel="noreferrer noopener"
          >
            MD
          </a>
          <button
            type="button"
            className="btn-quiet px-2 py-1 text-xs text-rose-600"
            onClick={() => onDelete(doc)}
          >
            {t("common.delete")}
          </button>
        </>
      )}
    </div>
  );
}

export default function DocumentsPage() {
  const { t } = useI18n();
  const teacherToken = getTeacherToken();
  const [docs, setDocs] = useState<DocumentBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [autoAnnotate, setAutoAnnotate] = useState(true);
  const [mode, setMode] = useState<"quick" | "deep">("quick");
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [refresh]);

  const watchAnnotation = useCallback(
    (docId: string) => {
      if (pollRef.current) window.clearInterval(pollRef.current);
      let ticks = 0;
      pollRef.current = window.setInterval(async () => {
        ticks += 1;
        try {
          const doc = await getDocument(docId);
          if (doc.status !== "annotating" || ticks > 90) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            setMessage(doc.status === "annotating" ? "" : t("annotate.done"));
            setBusy("");
            refresh();
          } else {
            setMessage(`${t("annotate.running")} ${Math.round((doc.progress || 0) * 100)}%`);
          }
        } catch {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setBusy("");
        }
      }, 1500);
    },
    [refresh, t],
  );

  const onUpload = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy("upload");
    setError("");
    setMessage(t("docs.uploading"));
    try {
      const doc = await uploadDocument(file, { title, annotate: autoAnnotate, mode });
      setFile(null);
      setTitle("");
      const input = document.getElementById("doc-file") as HTMLInputElement | null;
      if (input) input.value = "";
      if (autoAnnotate) {
        setBusy("annotate");
        setMessage(t("annotate.started"));
        watchAnnotation(doc.id);
      } else {
        setMessage("");
      }
      await refresh();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401 ? t("errors.token") : err instanceof Error ? err.message : String(err),
      );
    } finally {
      if (!autoAnnotate) setBusy("");
    }
  };

  const onAnnotate = async (docId: string, annotateMode: "quick" | "deep") => {
    setBusy(`annotate:${docId}`);
    setError("");
    setMessage(t("annotate.started"));
    try {
      await aiAnnotate(docId, { mode: annotateMode, note_lang: "ru", replace_existing: true });
      watchAnnotation(docId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
      setBusy("");
    }
  };

  const onDelete = async (doc: DocumentBrief) => {
    if (!window.confirm(t("docs.confirmDelete"))) return;
    try {
      await deleteDocument(doc.id);
      setMessage(t("docs.deleted"));
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return docs;
    return docs.filter(
      (doc) =>
        doc.title.toLowerCase().includes(needle) || doc.filename.toLowerCase().includes(needle),
    );
  }, [docs, filter]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("docs.title")}</h1>
        <input
          className="input ml-auto max-w-xs"
          placeholder={t("docs.filter")}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button type="button" className="btn-ghost" onClick={refresh}>
          ↻
        </button>
      </div>

      {!teacherToken && (
        <div className="card border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {t("common.teacherTokenHint")} — {t("common.teacherToken")}.{" "}
          <Link to="/" className="underline">
            {t("nav.home")}
          </Link>
        </div>
      )}

      <form onSubmit={onUpload} className="card p-4">
        <h2 className="text-sm font-semibold text-ink-900">{t("docs.upload")}</h2>
        <p className="mt-1 text-xs text-ink-600">{t("docs.uploadHint")}</p>
        <div className="mt-3 grid gap-3 md:grid-cols-[1.2fr_1fr_auto]">
          <input
            id="doc-file"
            type="file"
            className="input file:mr-2 file:rounded file:border-0 file:bg-ink-100 file:px-2 file:py-1 file:text-xs"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <input
            className="input"
            placeholder={`${t("docs.titleField")}`}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <button type="submit" className="btn-primary justify-center" disabled={!file || Boolean(busy)}>
            {busy === "upload" ? t("docs.uploading") : t("docs.upload")}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-600">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={autoAnnotate}
              onChange={(event) => setAutoAnnotate(event.target.checked)}
            />
            {t("docs.autoAnnotate")}
          </label>
          <div className="flex overflow-hidden rounded-lg border border-ink-200">
            {(["quick", "deep"] as const).map((item) => (
              <button
                key={item}
                type="button"
                className={`px-2.5 py-2.5 text-xs font-semibold lg:py-1 ${
                  mode === item ? "bg-ink-800 text-white" : "bg-white hover:bg-ink-100"
                }`}
                onClick={() => setMode(item)}
              >
                {item === "quick" ? t("reader.annotateQuick") : t("reader.annotateDeep")}
              </button>
            ))}
          </div>
        </div>
      </form>

      {message && (
        <div className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-800">{message}</div>
      )}
      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

      <div className="card overflow-hidden">
        {loading ? (
          <div className="p-6 text-sm text-ink-600">{t("common.loading")}</div>
        ) : filtered.length === 0 ? (
          <div className="p-6 text-sm text-ink-600">{t("docs.empty")}</div>
        ) : (
          <>
            {/* Планшеты и десктоп: таблица. На телефонах — карточки ниже. */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead className="bg-ink-50 text-left text-xs uppercase tracking-wide text-ink-600">
                  <tr>
                    <th className="px-3 py-2">{t("docs.col.title")}</th>
                    <th className="px-3 py-2">{t("docs.col.lang")}</th>
                    <th className="px-3 py-2">{t("docs.col.chars")}</th>
                    <th className="px-3 py-2">{t("docs.col.annotations")}</th>
                    <th className="px-3 py-2">{t("docs.col.status")}</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((doc) => (
                    <tr key={doc.id} className="border-t border-ink-100 align-top">
                      <td className="px-3 py-2">
                        <Link to={`/documents/${doc.id}`} className="font-medium text-ink-900 hover:text-seal-600">
                          {doc.title}
                        </Link>
                        <div className="text-xs text-ink-600">{doc.filename}</div>
                      </td>
                      <td className="px-3 py-2">
                        <span className="chip">{doc.language || "—"}</span>
                        {doc.classical >= 0.6 && (
                          <span className="ml-1 badge bg-violet-100 text-violet-700">
                            {t("docs.classical")}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-ink-600">{doc.char_count}</td>
                      <td className="px-3 py-2 text-ink-600">{doc.annotation_count}</td>
                      <td className="px-3 py-2">
                        <span className={`badge ${STATUS_STYLE[doc.status] || "bg-ink-100 text-ink-600"}`}>
                          {doc.status}
                        </span>
                        {doc.error && <div className="mt-1 text-xs text-rose-600">{doc.error}</div>}
                      </td>
                      <td className="px-3 py-2">
                        <DocActions
                          doc={doc}
                          teacherToken={teacherToken}
                          busy={busy}
                          onAnnotate={onAnnotate}
                          onDelete={onDelete}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <ul className="divide-y divide-ink-100 md:hidden">
              {filtered.map((doc) => (
                <li key={doc.id} className="space-y-2 p-3">
                  <div className="flex items-start gap-2">
                    <Link
                      to={`/documents/${doc.id}`}
                      className="min-w-0 flex-1 font-medium text-ink-900 hover:text-seal-600"
                    >
                      <span className="block truncate">{doc.title}</span>
                      <span className="block truncate text-xs font-normal text-ink-600">{doc.filename}</span>
                    </Link>
                    <span className={`badge ${STATUS_STYLE[doc.status] || "bg-ink-100 text-ink-600"}`}>
                      {doc.status}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-ink-600">
                    <span className="chip">{doc.language || "—"}</span>
                    {doc.classical >= 0.6 && (
                      <span className="badge bg-violet-100 text-violet-700">{t("docs.classical")}</span>
                    )}
                    <span>
                      {t("docs.col.chars")}: {doc.char_count}
                    </span>
                    <span>
                      {t("docs.col.annotations")}: {doc.annotation_count}
                    </span>
                  </div>
                  {doc.error && <div className="text-xs text-rose-600">{doc.error}</div>}
                  <DocActions
                    doc={doc}
                    teacherToken={teacherToken}
                    busy={busy}
                    onAnnotate={onAnnotate}
                    onDelete={onDelete}
                    className="justify-start"
                  />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
