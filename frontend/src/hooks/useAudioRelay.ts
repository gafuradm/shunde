/**
 * Аудио-релей живых лекций.
 *
 * Преподаватель записывает микрофон через MediaRecorder и отправляет бинарные
 * WebM-чанки в WebSocket; студенты складывают их в MediaSource и слушают поток.
 * Если браузер не умеет нужный MIME (например, Safari), релей отключается —
 * субтитры и перевод продолжают работать.
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

const MIME = "audio/webm;codecs=opus";

export interface AudioRelayApi {
  /** Нужно навесить на <audio /> в разметке. */
  audioRef: RefObject<HTMLAudioElement>;
  supported: boolean;
  playing: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
  push: (data: ArrayBuffer) => void;
}

function relaySupported(): boolean {
  if (typeof window === "undefined" || typeof window.MediaSource === "undefined") return false;
  try {
    return window.MediaSource.isTypeSupported(MIME);
  } catch {
    return false;
  }
}

export function useAudioRelay(): AudioRelayApi {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const msRef = useRef<MediaSource | null>(null);
  const sbRef = useRef<SourceBuffer | null>(null);
  const queueRef = useRef<ArrayBuffer[]>([]);
  const urlRef = useRef<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const supported = relaySupported();

  const flush = useCallback(() => {
    const sb = sbRef.current;
    const ms = msRef.current;
    if (!sb || !ms || ms.readyState !== "open" || sb.updating) return;
    const chunk = queueRef.current.shift();
    if (!chunk) return;
    try {
      sb.appendBuffer(chunk);
    } catch (err) {
      queueRef.current = [];
      setError(String(err));
    }
  }, []);

  const start = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    setError(null);
    if (!supported) {
      setError("unsupported");
      return;
    }
    if (!msRef.current) {
      const ms = new MediaSource();
      msRef.current = ms;
      urlRef.current = URL.createObjectURL(ms);
      audio.src = urlRef.current;
      ms.addEventListener("sourceopen", () => {
        try {
          const sb = ms.addSourceBuffer(MIME);
          try {
            sb.mode = "segments";
          } catch {
            /* режим не переключается — оставляем по умолчанию */
          }
          sbRef.current = sb;
          sb.addEventListener("updateend", flush);
          flush();
        } catch (err) {
          setError(String(err));
        }
      });
    }
    audio
      .play()
      .then(() => setPlaying(true))
      .catch(() => setPlaying(false));
  }, [flush, supported]);

  const stop = useCallback(() => {
    setPlaying(false);
    audioRef.current?.pause();
  }, []);

  const reset = useCallback(() => {
    queueRef.current = [];
    const ms = msRef.current;
    const sb = sbRef.current;
    try {
      if (sb && ms && ms.readyState === "open" && !sb.updating) ms.removeSourceBuffer(sb);
    } catch {
      /* noop */
    }
    sbRef.current = null;
    msRef.current = null;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    const audio = audioRef.current;
    if (audio) {
      audio.removeAttribute("src");
      audio.load();
    }
    setPlaying(false);
  }, []);

  const push = useCallback(
    (data: ArrayBuffer) => {
      const queue = queueRef.current;
      if (!sbRef.current) {
        // Ждём «sourceopen»: первый чанк — инициализирующий сегмент WebM.
        queue.push(data);
        if (queue.length > 200) queue.splice(1, queue.length - 100);
        return;
      }
      queue.push(data);
      if (queue.length > 200) queue.splice(0, queue.length - 100);
      flush();
    },
    [flush],
  );

  useEffect(
    () => () => {
      const ms = msRef.current;
      try {
        if (ms && ms.readyState === "open") ms.endOfStream();
      } catch {
        /* noop */
      }
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
      msRef.current = null;
      sbRef.current = null;
    },
    [],
  );

  return { audioRef, supported, playing, error, start, stop, reset, push };
}
