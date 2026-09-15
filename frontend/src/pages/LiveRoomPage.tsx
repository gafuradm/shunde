/**
 * Live-комната: преподаватель говорит — студенты читают субтитры и мгновенный
 * перевод. Поддерживает аудио-релей, личный перевод выделенного фрагмента и TTS.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useAudioRelay } from "../hooks/useAudioRelay";
import { useLiveRoom, type LiveRole } from "../hooks/useLiveRoom";
import { speechLangFor, useSpeechRecognition } from "../hooks/useSpeechRecognition";
import {
  endLecture,
  getLecture,
  lectureExportUrl,
  roomUrl,
  setLectureConfig,
  startLecture,
} from "../lib/api";
import { useI18n } from "../lib/i18n";
import { getTeacherToken } from "../lib/storage";
import type { Lecture } from "../lib/types";

const TARGET_LANGS = [
  { value: "en", label: "English" },
  { value: "ru", label: "Russian" },
  { value: "zh-CN", label: "中文" },
];

const SOURCE_LANGS = [
  { value: "zh-CN", label: "中文（普通话）" },
  { value: "zh-classical", label: "文言文" },
  { value: "en", label: "English" },
  { value: "ru", label: "Russian" },
  { value: "ja", label: "日本語" },
];

function speak(text: string, lang: string): void {
  if (!text || typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = speechLangFor(lang);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

export default function LiveRoomPage() {
  const { code = "" } = useParams<{ code: string }>();
  const [search] = useSearchParams();
  const { t } = useI18n();
  const role: LiveRole = search.get("role") === "host" ? "host" : "viewer";
  const token = getTeacherToken();

  const [lecture, setLecture] = useState<Lecture | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [viewerTarget, setViewerTarget] = useState("en");
  const [selected, setSelected] = useState("");
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [audioPublishing, setAudioPublishing] = useState(false);

  const relay = useAudioRelay();
  const room = useLiveRoom({
    code,
    role,
    token: role === "host" ? token : undefined,
    name: role === "host" ? "live.host" : "live.viewer",
    onAudio: relay.push,
  });

  const feedRef = useRef<HTMLDivElement | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const lastPartialRef = useRef({ text: "", at: 0 });

  const speech = useSpeechRecognition(
    room.state?.source_lang || lecture?.source_lang || "zh-CN",
    {
      onFinal: (text) => {
        lastPartialRef.current = { text: "", at: 0 };
        room.sendTranscript(text, true);
      },
      onPartial: (text) => {
        const now = Date.now();
        const last = lastPartialRef.current;
        if (text === last.text || now - last.at < 500) return;
        lastPartialRef.current = { text, at: now };
        room.sendTranscript(text, false);
      },
      onError: (reason) => setSpeechError(reason),
    },
  );

  useEffect(() => {
    let alive = true;
    setLoadError(null);
    getLecture(code)
      .then((data) => {
        if (!alive) return;
        setLecture(data);
        setViewerTarget(data.target_lang || "en");
      })
      .catch((err: any) => {
        if (!alive) return;
        setLoadError(err?.status === 404 ? "notFound" : "generic");
      });
    return () => {
      alive = false;
    };
  }, [code]);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [room.lines.length, room.partial, room.preview]);

  useEffect(
    () => () => {
      try {
        mediaRef.current?.stop();
      } catch {
        /* noop */
      }
      mediaRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    },
    [],
  );

  const livePreview = useMemo(() => {
    const seqs = Object.keys(room.preview)
      .map(Number)
      .sort((a, b) => a - b);
    const last = seqs[seqs.length - 1];
    return Number.isFinite(last) ? room.preview[last] : "";
  }, [room.preview]);

  const status = room.state?.status || lecture?.status || "idle";
  const statusKey = status === "live" ? "live" : status === "ended" ? "ended" : "idle";
  const statusClass =
    status === "live"
      ? "bg-emerald-100 text-emerald-700"
      : status === "ended"
        ? "bg-ink-200 text-ink-600"
        : "bg-amber-100 text-amber-700";
  const sourceLang = room.state?.source_lang || lecture?.source_lang || "zh-CN";
  const targetLang = room.state?.target_lang || lecture?.target_lang || "en";
  const fatal = loadError || (room.error && room.error !== "closed" ? room.error : null);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(roomUrl(code));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard недоступен */
    }
  };

  const refresh = async () => {
    try {
      setLecture(await getLecture(code));
    } catch {
      /* оставляем прежние данные */
    }
  };

  const onStart = async () => {
    setBusy(true);
    setLoadError(null);
    try {
      await startLecture(code);
      await refresh();
    } catch (err: any) {
      setLoadError(err?.status === 401 ? "token" : "generic");
    } finally {
      setBusy(false);
    }
  };

  const onEnd = async () => {
    setBusy(true);
    try {
      await endLecture(code);
      await refresh();
    } catch (err: any) {
      setLoadError(err?.status === 401 ? "token" : "generic");
    } finally {
      setBusy(false);
    }
  };

  const onLangChange = async (key: "source_lang" | "target_lang", value: string) => {
    setLecture((prev) => {
      if (!prev) return prev;
      return key === "source_lang" ? { ...prev, source_lang: value } : { ...prev, target_lang: value };
    });
    try {
      const payload = key === "source_lang" ? { source_lang: value } : { target_lang: value };
      setLecture(await setLectureConfig(code, payload));
    } catch (err: any) {
      setLoadError(err?.status === 401 ? "token" : "generic");
    }
  };

  const toggleAudio = async () => {
    if (mediaRef.current) {
      try {
        mediaRef.current.stop();
      } catch {
        /* noop */
      }
      mediaRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      room.setAudioState(false);
      setAudioPublishing(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime =
        typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "";
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 32000 })
        : new MediaRecorder(stream);
      recorder.ondataavailable = (event: BlobEvent) => {
        if (!event.data || event.data.size === 0) return;
        event.data
          .arrayBuffer()
          .then((buffer) => room.sendAudio(buffer))
          .catch(() => undefined);
      };
      recorder.start(1000);
      mediaRef.current = recorder;
      setAudioPublishing(true);
      room.setAudioState(true);
    } catch {
      setSpeechError("mic");
    }
  };

  const startListening = () => {
    relay.start();
    room.requestAudio();
  };

  const onFeedMouseUp = () => {
    const selection = window.getSelection();
    const text = selection ? selection.toString().trim() : "";
    if (text) setSelected(text.slice(0, 600));
  };

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-6">
      <header className="card flex flex-wrap items-start gap-3 px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold text-ink-900">
              {lecture?.title || t("live.connecting")}
            </h1>
            <span className={`badge ${statusClass}`}>{t(`lectures.status.${statusKey}`)}</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-500">
            <span className="font-mono text-sm tracking-[0.2em] text-ink-800">{code}</span>
            <span className="chip">{role === "host" ? t("live.host") : t("live.viewer")}</span>
            <span className="chip">
              {t("live.viewers")}: {room.participants.viewers}
            </span>
            <span className={`chip ${room.connected ? "text-emerald-600" : "text-rose-600"}`}>
              {room.connected ? t("live.connected") : t("live.disconnected")}
            </span>
            <span className="chip">
              {sourceLang} → {targetLang}
            </span>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button type="button" className="btn-ghost" onClick={copyLink}>
            {copied ? t("common.copied") : t("lectures.copyLink")}
          </button>
          <a className="btn-ghost" href={lectureExportUrl(code, "srt")}>
            {t("live.exportSrt")}
          </a>
          <a className="btn-ghost" href={lectureExportUrl(code, "markdown")}>
            {t("live.exportMd")}
          </a>
          <Link className="btn-quiet" to="/lectures">
            {t("common.back")}
          </Link>
        </div>
      </header>

      {fatal && (
        <div className="card border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {fatal === "notFound"
            ? t("errors.notFound")
            : fatal === "token"
              ? t("errors.token")
              : t("errors.generic")}
          {fatal === "token" && <span className="ml-1 text-rose-500">{t("common.teacherTokenHint")}</span>}
        </div>
      )}

      {!room.connected && room.error === "closed" && (
        <div className="card flex items-center gap-3 border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {t("live.disconnected")}
          <button type="button" className="btn-ghost" onClick={room.reconnect}>
            {t("live.reconnect")}
          </button>
        </div>
      )}

      {role === "host" && (
        <section className="card px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary" disabled={busy || status === "live"} onClick={onStart}>
              {t("lectures.start")}
            </button>
            <button type="button" className="btn-ghost" disabled={busy} onClick={onEnd}>
              {t("lectures.end")}
            </button>
            <span className="mx-1 hidden h-6 w-px bg-ink-200 sm:block" />
            {speech.supported ? (
              <button
                type="button"
                className={speech.listening ? "btn-primary" : "btn-ghost"}
                onClick={speech.toggle}
              >
                🎙 {speech.listening ? t("live.micStop") : t("live.micStart")}
              </button>
            ) : (
              <span className="chip text-amber-700">{t("live.speechUnsupported")}</span>
            )}
            <button
              type="button"
              className={audioPublishing ? "btn-primary" : "btn-ghost"}
              onClick={toggleAudio}
            >
              {audioPublishing ? t("live.audioRelayOff") : t("live.audioRelayOn")}
            </button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-600">
            <label className="flex items-center gap-2">
              {t("lectures.field.source")}
              <select
                className="input w-auto py-1"
                value={sourceLang}
                onChange={(event) => onLangChange("source_lang", event.target.value)}
              >
                {SOURCE_LANGS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              {t("lectures.field.target")}
              <select
                className="input w-auto py-1"
                value={targetLang}
                onChange={(event) => onLangChange("target_lang", event.target.value)}
              >
                {TARGET_LANGS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {speech.listening && (
              <span className="chip text-emerald-600">
                {t("live.listen")}
                {speech.interim ? `: ${speech.interim.slice(0, 80)}` : "…"}
              </span>
            )}
            {speechError && <span className="chip text-rose-600">{speechError}</span>}
          </div>
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="card flex min-h-[420px] flex-col">
          <div className="flex items-center justify-between border-b border-ink-100 px-4 py-2">
            <h2 className="text-sm font-semibold text-ink-700">{t("live.subtitles")}</h2>
            {speech.listening && <span className="chip text-seal-600">{t("live.listen")}</span>}
          </div>
          <div
            ref={feedRef}
            onMouseUp={onFeedMouseUp}
            className="scroll-thin flex-1 space-y-2 overflow-y-auto px-4 py-3"
          >
            {room.lines.length === 0 && !room.partial && (
              <p className="text-sm text-ink-400">{t("live.emptyRoom")}</p>
            )}
            {room.lines.map((line) => {
              const translation = line.translation;
              return (
                <article key={line.id || line.seq} className="rounded-lg bg-ink-50/70 px-3 py-2">
                  <div className="flex items-start gap-2">
                    <p className="flex-1 font-han text-[15px] leading-relaxed text-ink-900">{line.text}</p>
                    <button
                      type="button"
                      className="btn-quiet px-1 text-xs"
                      title={t("live.speakBtn")}
                      onClick={() => speak(line.text, sourceLang)}
                    >
                      🔊
                    </button>
                  </div>
                  <div className="mt-1 flex items-start gap-2">
                    <p className="flex-1 text-sm leading-relaxed text-seal-700">
                      {translation || <span className="text-ink-400">{t("live.translating")}</span>}
                    </p>
                    {translation && (
                      <button
                        type="button"
                        className="btn-quiet px-1 text-xs"
                        title={t("live.speakBtn")}
                        onClick={() => speak(translation, line.target_lang || targetLang)}
                      >
                        🔊
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
            {(room.partial || livePreview) && (
              <article className="rounded-lg border border-dashed border-seal-300 bg-seal-50/60 px-3 py-2">
                {room.partial && (
                  <p className="font-han text-[15px] italic leading-relaxed text-ink-700">{room.partial}</p>
                )}
                {livePreview && <p className="mt-1 text-sm italic text-seal-600">{livePreview}</p>}
              </article>
            )}
          </div>
          {role === "viewer" && (
            <div className="flex flex-wrap items-center gap-2 border-t border-ink-100 px-4 py-2">
              <button
                type="button"
                className="btn-ghost"
                disabled={!selected}
                onClick={() => room.requestTranslation(selected, viewerTarget, "manual")}
              >
                {t("live.translateSelection")}
              </button>
              <label className="flex items-center gap-2 text-xs text-ink-600">
                {t("live.targetLang")}
                <select
                  className="input w-auto py-1"
                  value={viewerTarget}
                  onChange={(event) => setViewerTarget(event.target.value)}
                >
                  {TARGET_LANGS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <span className="text-xs text-ink-400">{t("live.personalHint")}</span>
            </div>
          )}
        </section>

        <aside className="flex flex-col gap-4">
          {role === "viewer" && (
            <section className="card px-4 py-3">
              <h2 className="text-sm font-semibold text-ink-700">{t("live.audioRelay")}</h2>
              <audio ref={relay.audioRef} className="mt-2 w-full" controls playsInline />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" className="btn-ghost" onClick={startListening}>
                  {t("live.audioRelayOn")}
                </button>
                {room.audioActive ? (
                  <span className="chip text-emerald-600">{t("live.audioLive")}</span>
                ) : (
                  <span className="chip">{t("live.audioWaiting")}</span>
                )}
              </div>
              {!relay.supported && <p className="mt-1 text-xs text-amber-700">{t("live.audioBlocked")}</p>}
            </section>
          )}

          <section className="card px-4 py-3">
            <h2 className="text-sm font-semibold text-ink-700">{t("live.myTranslation")}</h2>
            {room.personal?.text ? (
              <p className="mt-1 text-sm leading-relaxed text-ink-800">{room.personal.text}</p>
            ) : (
              <p className="mt-1 text-xs text-ink-400">{t("live.personalHint")}</p>
            )}
            {room.personalPending && <p className="mt-1 text-xs text-ink-400">{t("live.translating")}</p>}
          </section>

          {lecture?.document_id && (
            <section className="card px-4 py-3">
              <h2 className="text-sm font-semibold text-ink-700">{t("live.reader")}</h2>
              <Link className="btn-ghost mt-2" to={`/documents/${lecture.document_id}`}>
                {t("common.open")}
              </Link>
            </section>
          )}

          <section className="card px-4 py-3 text-xs text-ink-500">
            <h2 className="text-sm font-semibold text-ink-700">{t("live.participants")}</h2>
            <p className="mt-1">
              {t("live.viewers")}: {room.participants.viewers} · {t("live.host")}: {room.participants.hosts}
            </p>
            {status !== "live" && <p className="mt-2 text-amber-700">{t("live.notLive")}</p>}
          </section>
        </aside>
      </div>
    </div>
  );
}
