/**
 * Обёртка над Web Speech API: непрерывное распознавание речи с промежуточными
 * результатами. Используется в live-комнате, когда преподаватель говорит в микрофон.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/** Соответствие кода языка лекции языку распознавания. */
const SPEECH_LANGS: Record<string, string> = {
  zh: "zh-CN",
  "zh-cn": "zh-CN",
  "zh-hans": "zh-CN",
  "zh-classical": "zh-CN",
  "zh-tw": "zh-TW",
  "zh-hant": "zh-TW",
  en: "en-US",
  "en-us": "en-US",
  "en-gb": "en-GB",
  ru: "ru-RU",
  ja: "ja-JP",
  ko: "ko-KR",
};

export function speechLangFor(source?: string | null): string {
  const key = (source || "zh-CN").trim().toLowerCase();
  return SPEECH_LANGS[key] || SPEECH_LANGS[key.split("-")[0]] || "zh-CN";
}

type RecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: any) => void) | null;
  onresult: ((event: any) => void) | null;
};

function recognitionCtor(): (new () => RecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export interface SpeechRecognitionHandlers {
  /** Готовая фраза — отправляем её в комнату как финальную строку. */
  onFinal: (text: string) => void;
  /** Промежуточный текст — для «живых» субтитров. */
  onPartial?: (text: string) => void;
  onError?: (code: string) => void;
}

export interface SpeechRecognitionApi {
  supported: boolean;
  listening: boolean;
  interim: string;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

export function useSpeechRecognition(
  lang: string,
  handlers: SpeechRecognitionHandlers,
): SpeechRecognitionApi {
  const supported = recognitionCtor() !== null;
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const recRef = useRef<RecognitionLike | null>(null);
  const wantRef = useRef(false);
  const handlersRef = useRef(handlers);
  const langRef = useRef(lang);
  handlersRef.current = handlers;
  langRef.current = lang;

  const ensure = useCallback((): RecognitionLike | null => {
    if (recRef.current) return recRef.current;
    const Ctor = recognitionCtor();
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.lang = speechLangFor(langRef.current);
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onstart = () => setListening(true);
    rec.onresult = (event: any) => {
      let interimText = "";
      let finalText = "";
      const results = event?.results ?? [];
      for (let i = event?.resultIndex ?? 0; i < results.length; i += 1) {
        const result = results[i];
        const chunk: string = result?.[0]?.transcript ?? "";
        if (result?.isFinal) finalText += chunk;
        else interimText += chunk;
      }
      const tail = interimText.trim();
      setInterim(tail);
      if (tail) handlersRef.current.onPartial?.(tail);
      const ready = finalText.trim();
      if (ready) {
        setInterim("");
        handlersRef.current.onFinal(ready);
      }
    };
    rec.onerror = (event: any) => {
      const code = String(event?.error || "unknown");
      if (code === "not-allowed" || code === "service-not-allowed") {
        wantRef.current = false;
        setListening(false);
      }
      if (code !== "no-speech" && code !== "aborted") handlersRef.current.onError?.(code);
    };
    rec.onend = () => {
      if (wantRef.current) {
        // Chrome останавливает распознавание после паузы — перезапускаем.
        try {
          rec.start();
        } catch {
          /* уже запущено */
        }
      } else {
        setListening(false);
        setInterim("");
      }
    };
    recRef.current = rec;
    return rec;
  }, []);

  const start = useCallback(() => {
    const rec = ensure();
    if (!rec) return;
    wantRef.current = true;
    rec.lang = speechLangFor(langRef.current);
    try {
      rec.start();
      setListening(true);
    } catch {
      /* уже слушаем */
    }
  }, [ensure]);

  const stop = useCallback(() => {
    wantRef.current = false;
    setListening(false);
    setInterim("");
    try {
      recRef.current?.stop();
    } catch {
      /* noop */
    }
  }, []);

  const toggle = useCallback(() => {
    if (wantRef.current) stop();
    else start();
  }, [start, stop]);

  useEffect(() => {
    if (recRef.current) recRef.current.lang = speechLangFor(lang);
  }, [lang]);

  useEffect(
    () => () => {
      wantRef.current = false;
      try {
        recRef.current?.abort();
      } catch {
        /* noop */
      }
      recRef.current = null;
    },
    [],
  );

  return { supported, listening, interim, start, stop, toggle };
}
