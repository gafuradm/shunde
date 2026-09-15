import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useI18n } from "../lib/i18n";
import {
  ApiError,
  createLecture,
  deleteLecture,
  endLecture,
  lectureExportUrl,
  listDocuments,
  listLectures,
  roomUrl,
  startLecture,
} from "../lib/api";
import { getTeacherToken } from "../lib/storage";
import type { DocumentBrief, Lecture } from "../lib/types";

const SOURCES = ["zh-CN", "zh-classical", "en", "ru", "ja"];
const TARGETS = ["en", "ru", "zh-CN"];

const STATUS_STYLE: Record<string, string> = {
  idle: "bg-ink-100 text-ink-600",
  live: "bg-rose-100 text-rose-700",
  ended: "bg-emerald-100 text-emerald-700",
};

function LectureActions({
  lecture,
  teacherToken,
  copied,
  onCopy,
  onStart,
  onEnd,
  onDelete,
  className = "justify-end",
}: {
  lecture: Lecture;
  teacherToken: string | null;
  copied: string;
  onCopy: (code: string) => void;
  onStart: (code: string) => void;
  onEnd: (code: string) => void;
  onDelete: (code: string) => void;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => onCopy(lecture.code)}>
        {copied === lecture.code ? t("common.copied") : t("lectures.copyLink")}
      </button>
      <Link className="btn-ghost px-2 py-1 text-xs" to={`/live/${lecture.code}`}>
        {t("lectures.openStudent")}
      </Link>
      {teacherToken && (
        <>
          <Link className="btn-ghost px-2 py-1 text-xs" to={`/live/${lecture.code}?role=host`}>
            {t("lectures.openHost")}
          </Link>
          {lecture.status !== "live" ? (
            <button
              type="button"
              className="btn-primary px-2 py-1 text-xs"
              onClick={() => onStart(lecture.code)}
            >
              {t("lectures.start")}
            </button>
          ) : (
            <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => onEnd(lecture.code)}>
              {t("lectures.end")}
            </button>
          )}
          <a
            className="btn-ghost px-2 py-1 text-xs"
            href={lectureExportUrl(lecture.code, "srt")}
            target="_blank"
            rel="noreferrer noopener"
          >
            {t("live.exportSrt")}
          </a>
          <a
            className="btn-ghost px-2 py-1 text-xs"
            href={lectureExportUrl(lecture.code, "markdown")}
            target="_blank"
            rel="noreferrer noopener"
          >
            {t("live.exportMd")}
          </a>
          <button
            type="button"
            className="btn-quiet px-2 py-1 text-xs text-rose-600"
            onClick={() => onDelete(lecture.code)}
          >
            {t("lectures.delete")}
          </button>
        </>
      )}
    </div>
  );
}

export default function LecturesPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const teacherToken = getTeacherToken();
  const [lectures, setLectures] = useState<Lecture[]>([]);
  const [documents, setDocuments] = useState<DocumentBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const [title, setTitle] = useState("");
  const [sourceLang, setSourceLang] = useState("zh-CN");
  const [targetLang, setTargetLang] = useState("en");
  const [documentId, setDocumentId] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [list, docs] = await Promise.all([listLectures(), listDocuments()]);
      setLectures(list);
      setDocuments(docs);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const lecture = await createLecture({
        title: title || undefined,
        document_id: documentId || undefined,
        source_lang: sourceLang,
        target_lang: targetLang,
      });
      setTitle("");
      setDocumentId("");
      await refresh();
      navigate(`/live/${lecture.code}?role=host`);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const onStart = async (code: string) => {
    try {
      await startLecture(code);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const onEnd = async (code: string) => {
    try {
      await endLecture(code);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const onDelete = async (code: string) => {
    if (!window.confirm(`${t("lectures.delete")}: ${code}?`)) return;
    try {
      await deleteLecture(code);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? t("errors.token") : String(err));
    }
  };

  const copyLink = async (code: string) => {
    try {
      await navigator.clipboard.writeText(roomUrl(code));
      setCopied(code);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      setCopied("");
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("lectures.title")}</h1>
        <span className="text-xs text-ink-600">{t("lectures.createHint")}</span>
        <button type="button" className="btn-ghost ml-auto" onClick={refresh}>
          ↻
        </button>
      </div>

      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

      {teacherToken ? (
        <form onSubmit={onCreate} className="card p-4">
          <h2 className="text-sm font-semibold text-ink-900">{t("lectures.create")}</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <input
              className="input"
              placeholder={t("lectures.field.title")}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
            <select className="input" value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
              {SOURCES.map((item) => (
                <option key={item} value={item}>
                  {t("lectures.field.source")}: {item}
                </option>
              ))}
            </select>
            <select className="input" value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
              {TARGETS.map((item) => (
                <option key={item} value={item}>
                  {t("lectures.field.target")}: {item}
                </option>
              ))}
            </select>
            <select className="input" value={documentId} onChange={(e) => setDocumentId(e.target.value)}>
              <option value="">{t("lectures.field.document")}</option>
              {documents.map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {doc.title}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" className="btn-primary mt-3">
            {t("lectures.create")}
          </button>
        </form>
      ) : (
        <div className="card border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {t("common.teacherTokenHint")} — {t("common.teacherToken")}.
        </div>
      )}

      <div className="card overflow-hidden">
        {loading ? (
          <div className="p-6 text-sm text-ink-600">{t("common.loading")}</div>
        ) : lectures.length === 0 ? (
          <div className="p-6 text-sm text-ink-600">{t("lectures.empty")}</div>
        ) : (
          <>
            {/* Планшеты и десктоп: таблица. На телефонах — карточки ниже. */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead className="bg-ink-50 text-left text-xs uppercase tracking-wide text-ink-600">
                  <tr>
                    <th className="px-3 py-2">{t("lectures.field.title")}</th>
                    <th className="px-3 py-2">{t("lectures.code")}</th>
                    <th className="px-3 py-2">{t("lectures.field.source")} → {t("lectures.field.target")}</th>
                    <th className="px-3 py-2">{t("lectures.status")}</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {lectures.map((lecture) => (
                    <tr key={lecture.id} className="border-t border-ink-100 align-top">
                      <td className="px-3 py-2 font-medium">{lecture.title}</td>
                      <td className="px-3 py-2">
                        <span className="font-han text-base tracking-[0.2em]">{lecture.code}</span>
                      </td>
                      <td className="px-3 py-2 text-ink-600">
                        {lecture.source_lang} → {lecture.target_lang}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`badge ${STATUS_STYLE[lecture.status] || "bg-ink-100 text-ink-600"}`}>
                          {t(`lectures.status.${lecture.status}`, lecture.status)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <LectureActions
                          lecture={lecture}
                          teacherToken={teacherToken}
                          copied={copied}
                          onCopy={copyLink}
                          onStart={onStart}
                          onEnd={onEnd}
                          onDelete={onDelete}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <ul className="divide-y divide-ink-100 md:hidden">
              {lectures.map((lecture) => (
                <li key={lecture.id} className="space-y-2 p-3">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-ink-900">{lecture.title}</div>
                      <div className="font-han text-sm tracking-[0.2em] text-ink-600">{lecture.code}</div>
                    </div>
                    <span className={`badge ${STATUS_STYLE[lecture.status] || "bg-ink-100 text-ink-600"}`}>
                      {t(`lectures.status.${lecture.status}`, lecture.status)}
                    </span>
                  </div>
                  <div className="text-xs text-ink-600">
                    {lecture.source_lang} → {lecture.target_lang}
                  </div>
                  <LectureActions
                    lecture={lecture}
                    teacherToken={teacherToken}
                    copied={copied}
                    onCopy={copyLink}
                    onStart={onStart}
                    onEnd={onEnd}
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
