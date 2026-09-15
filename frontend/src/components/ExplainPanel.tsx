import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { kindLabel, useI18n } from "../lib/i18n";
import { streamExplain } from "../lib/sse";
import { getExplainLevel, setExplainLevel } from "../lib/storage";
import type { Annotation, Level, Source } from "../lib/types";
import MiniMarkdown from "./MiniMarkdown";

const MODES = ["explain", "simplify", "translate", "example"] as const;
const LEVELS: Level[] = ["simple", "standard", "advanced"];

export interface ExplainTarget {
  quote: string;
  start?: number;
  end?: number;
  context?: string;
  annotation?: Annotation;
}

interface Props {
  documentId?: string;
  target: ExplainTarget;
  onClose?: () => void;
  className?: string;
}

export default function ExplainPanel({ documentId, target, onClose, className = "" }: Props) {
  const { t, lang } = useI18n();
  const [mode, setMode] = useState<(typeof MODES)[number]>("explain");
  const [level, setLevel] = useState<Level>((getExplainLevel() as Level) || "standard");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [status, setStatus] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [degraded, setDegraded] = useState(false);
  const abortRef = useRef<{ abort: () => void } | null>(null);

  const run = useCallback(() => {
    abortRef.current?.abort();
    setAnswer("");
    setSources([]);
    setError("");
    setDegraded(false);
    setLoading(true);
    setStatus(t("explain.loading"));

    abortRef.current = streamExplain(
      {
        document_id: documentId,
        start_char: target.start,
        end_char: target.end,
        quote: target.quote,
        context: target.context,
        ui_lang: lang,
        mode,
        level,
        force: true,
      },
      {
        onStatus: (data) => {
          const stage = String(data.stage || "");
          if (data.message) setStatus(String(data.message));
          else if (stage) setStatus(stage);
        },
        onSources: (data) => setSources(data.sources || []),
        onDelta: (chunk) => setAnswer((prev) => prev + chunk),
        onDone: (data) => {
          if (data.answer) setAnswer(data.answer);
          setDegraded(Boolean(data.degraded));
          setLoading(false);
          setStatus("");
        },
        onError: (message) => {
          setError(message);
          setLoading(false);
          setStatus("");
        },
      },
    );
  }, [documentId, target.start, target.end, target.quote, target.context, mode, level, lang, t]);

  useEffect(() => {
    run();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);

  const speak = () => {
    const plain = (answer || target.quote).replace(/[#*`>\[\]()]/g, "").slice(0, 1200);
    if (!plain || typeof window.speechSynthesis === "undefined") return;
    const utter = new SpeechSynthesisUtterance(plain);
    utter.lang = mode === "translate" ? "en-US" : "zh-CN";
    utter.rate = 0.95;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  };

  const kindText = useMemo(
    () => (target.annotation ? kindLabel(target.annotation.kind, t) : ""),
    [target.annotation, t],
  );

  return (
    <div className={`card overflow-hidden ${className}`}>
      <div className="flex items-start gap-2 border-b border-ink-100 bg-ink-50/60 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-600">
              {t("explain.title")}
            </span>
            {kindText && <span className="badge bg-white text-ink-600">{kindText}</span>}
            {degraded && (
              <span className="badge bg-amber-100 text-amber-700" title={t("explain.degraded")}>
                sources
              </span>
            )}
          </div>
          <div className="mt-0.5 truncate font-han text-sm text-ink-800" title={target.quote}>
            {target.quote}
          </div>
        </div>
        {onClose && (
          <button type="button" className="btn-quiet px-2 py-1" onClick={onClose} title={t("common.close")}>
            ✕
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1 border-b border-ink-100 px-3 py-2">
        {MODES.map((item) => (
          <button
            key={item}
            type="button"
            className={`chip ${mode === item ? "border-seal-400 bg-seal-500/10 text-seal-600" : ""}`}
            onClick={() => setMode(item)}
          >
            {t(`explain.mode.${item}`)}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-ink-200" />
        {LEVELS.map((item) => (
          <button
            key={item}
            type="button"
            className={`chip ${level === item ? "border-seal-400 bg-seal-500/10 text-seal-600" : ""}`}
            onClick={() => {
              setLevel(item);
              setExplainLevel(item);
            }}
          >
            {t(`explain.level.${item}`)}
          </button>
        ))}
        <button type="button" className="btn-quiet ml-auto px-2 py-1" onClick={speak} title="TTS">
          🔊
        </button>
        <button type="button" className="btn-quiet px-2 py-1" onClick={run} title={t("common.retry")}>
          ↻
        </button>
      </div>

      <div className="max-h-[min(58vh,520px)] overflow-y-auto px-3 py-2 scroll-thin">
        {error ? (
          <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {t("common.error")}: {error}
          </div>
        ) : (
          <>
            {loading && !answer && (
              <div className="flex items-center gap-2 text-sm text-ink-600">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink-200 border-t-seal-500" />
                {status || t("explain.loading")}
              </div>
            )}
            {answer && <MiniMarkdown text={answer} />}
            {loading && answer && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-seal-500 align-middle" />
            )}
          </>
        )}

        {sources.length > 0 && (
          <div className="mt-3 border-t border-ink-100 pt-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-600">
              {t("explain.sources")}
            </div>
            <ul className="mt-1 space-y-1">
              {sources.map((source, index) => (
                <li key={`${source.url}-${index}`} className="text-xs">
                  <a
                    className="text-seal-600 underline decoration-dotted"
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {source.title || source.url}
                  </a>
                  {source.provider && <span className="ml-1 text-ink-600">· {source.provider}</span>}
                  {source.snippet && (
                    <div className="mt-0.5 line-clamp-2 text-ink-600">{source.snippet}</div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {target.annotation?.rationale && (
        <div className="border-t border-ink-100 bg-ink-50/60 px-3 py-2 text-xs text-ink-600">
          {target.annotation.rationale}
        </div>
      )}
    </div>
  );
}
