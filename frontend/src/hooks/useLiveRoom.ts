/**
 * WebSocket-клиент live-комнаты: субтитры, мгновенный перевод, личный перевод,
 * аудио-релей и состояние участников.
 *
 * Протокол — см. backend/app/services/hub.py:
 *   hello / history / participants / partial / line / translation /
 *   translation_preview / personal_translation / config / status / audio_state / error
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { wsUrl } from "../lib/api";
import type { RoomLine, RoomState } from "../lib/types";

export type LiveRole = "host" | "viewer";

export interface LiveParticipants {
  viewers: number;
  hosts: number;
  total: number;
}

export interface LivePersonal {
  key: string;
  target: string;
  text: string;
}

export interface LiveRoomApi {
  connected: boolean;
  error: string | null;
  state: RoomState | null;
  lines: RoomLine[];
  partial: string;
  preview: Record<number, string>;
  participants: LiveParticipants;
  audioActive: boolean;
  personal: LivePersonal | null;
  personalPending: boolean;
  sendTranscript: (text: string, final: boolean) => void;
  requestTranslation: (text: string, target: string, key?: string) => void;
  setConfig: (cfg: { source_lang?: string; target_lang?: string }) => void;
  setAudioState: (active: boolean) => void;
  requestAudio: () => void;
  sendAudio: (chunk: ArrayBuffer | Blob) => void;
  reconnect: () => void;
}

function normalizeLine(raw: any): RoomLine {
  return {
    id: String(raw?.id ?? ""),
    seq: Number(raw?.seq ?? 0),
    text: String(raw?.text ?? ""),
    translation: raw?.translation ?? null,
    target_lang: String(raw?.target_lang ?? "en"),
    final: Boolean(raw?.final ?? true),
    created_at: String(raw?.created_at ?? ""),
  };
}

/**
 * Убирает устаревшие промежуточные переводы.
 *
 * Частичная фраза и её финальная версия получают РАЗНЫЕ seq (сервер увеличивает
 * счётчик на каждый пакет), поэтому запись превью, пришедшая с seq частичной фразы,
 * сама по себе не удаляется и «висит» в субтитрах. Чистим всё, что не новее maxSeq.
 */
function prunePreview(prev: Record<number, string>, maxSeq: number): Record<number, string> {
  const next: Record<number, string> = {};
  for (const [key, value] of Object.entries(prev)) {
    const seq = Number(key);
    if (Number.isFinite(seq) && seq > maxSeq) next[seq] = value;
  }
  return Object.keys(next).length === Object.keys(prev).length ? prev : next;
}

export function useLiveRoom(options: {
  code: string;
  role: LiveRole;
  token?: string;
  name?: string;
  onAudio?: (data: ArrayBuffer) => void;
}): LiveRoomApi {
  const { code, role, token, name } = options;
  const onAudioRef = useRef(options.onAudio);
  onAudioRef.current = options.onAudio;

  const wsRef = useRef<WebSocket | null>(null);
  const pingRef = useRef<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<RoomState | null>(null);
  const [lines, setLines] = useState<RoomLine[]>([]);
  const [partial, setPartial] = useState("");
  const [preview, setPreview] = useState<Record<number, string>>({});
  const [participants, setParticipants] = useState<LiveParticipants>({
    viewers: 0,
    hosts: 0,
    total: 0,
  });
  const [audioActive, setAudioActive] = useState(false);
  const [personal, setPersonal] = useState<LivePersonal | null>(null);
  const [personalPending, setPersonalPending] = useState(false);

  useEffect(() => {
    if (!code) return undefined;
    const params = new URLSearchParams({ role });
    if (token) params.set("token", token);
    if (name) params.set("name", name);
    const ws = new WebSocket(wsUrl(`/ws/lectures/${encodeURIComponent(code)}?${params.toString()}`));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    setError(null);

    const handle = (msg: any) => {
      switch (msg?.type) {
        case "hello":
          if (msg.state) setState(msg.state as RoomState);
          break;
        case "history":
          setLines(Array.isArray(msg.lines) ? msg.lines.map(normalizeLine) : []);
          if (msg.state) setState(msg.state as RoomState);
          break;
        case "participants":
          setParticipants({
            viewers: Number(msg.viewers ?? 0),
            hosts: Number(msg.hosts ?? 0),
            total: Number(msg.total ?? 0),
          });
          break;
        case "status":
        case "config":
          if (msg.state) setState(msg.state as RoomState);
          break;
        case "partial": {
          const seq = Number(msg.seq ?? 0);
          setPartial(String(msg.text || ""));
          setPreview((prev) => prunePreview(prev, seq - 1));
          break;
        }
        case "line": {
          const line = normalizeLine(msg.line);
          setPartial("");
          setLines((prev) => (prev.some((l) => l.id === line.id) ? prev : [...prev, line]));
          setPreview((prev) => prunePreview(prev, line.seq));
          break;
        }
        case "translation_preview": {
          const seq = Number(msg.seq ?? 0);
          setPreview((prev) => ({ ...prunePreview(prev, seq - 1), [seq]: String(msg.text || "") }));
          break;
        }
        case "translation": {
          const seq = Number(msg.seq ?? 0);
          const text = String(msg.text || "");
          const lineId = msg.line_id ? String(msg.line_id) : "";
          if (lineId) {
            setLines((prev) =>
              prev.map((l) =>
                l.id === lineId ? { ...l, translation: text || l.translation } : l,
              ),
            );
          }
          setPreview((prev) => prunePreview(prev, seq));
          break;
        }
        case "personal_translation":
          setPersonal({
            key: String(msg.line_id || "manual"),
            target: String(msg.target || ""),
            text: String(msg.text || ""),
          });
          setPersonalPending(false);
          break;
        case "audio_state":
          setAudioActive(Boolean(msg.active));
          break;
        case "error":
          setError(String(msg.message || "error"));
          break;
        default:
          break;
      }
    };

    ws.onopen = () => {
      setConnected(true);
      setError(null);
    };
    ws.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== "string") {
        onAudioRef.current?.(event.data as ArrayBuffer);
        return;
      }
      try {
        handle(JSON.parse(event.data));
      } catch {
        /* не JSON — игнорируем */
      }
    };
    ws.onclose = (event: CloseEvent) => {
      setConnected(false);
      if (event.code === 4403) setError("token");
      else if (event.code === 4404) setError("notFound");
      else if (event.code !== 1000) setError("closed");
    };
    ws.onerror = () => setError((prev) => prev ?? "network");

    pingRef.current = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 25000);

    return () => {
      if (pingRef.current) window.clearInterval(pingRef.current);
      pingRef.current = null;
      ws.onclose = null;
      try {
        ws.close(1000, "bye");
      } catch {
        /* noop */
      }
      wsRef.current = null;
      setConnected(false);
      setPartial("");
    };
  }, [code, role, token, name, attempt]);

  const send = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }, []);

  const sendTranscript = useCallback(
    (text: string, final: boolean) => send({ type: "transcript", text, final }),
    [send],
  );

  const requestTranslation = useCallback(
    (text: string, target: string, key?: string) => {
      setPersonalPending(true);
      send({ type: "translate_request", text, target, line_id: key ?? null });
    },
    [send],
  );

  const setConfig = useCallback(
    (cfg: { source_lang?: string; target_lang?: string }) =>
      send({ type: "set_config", ...cfg }),
    [send],
  );

  const setAudioState = useCallback(
    (active: boolean) => send({ type: "audio_state", active }),
    [send],
  );

  const requestAudio = useCallback(() => send({ type: "audio_request" }), [send]);

  const sendAudio = useCallback((chunk: ArrayBuffer | Blob) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(chunk);
  }, []);

  const reconnect = useCallback(() => setAttempt((n) => n + 1), []);

  return {
    connected,
    error,
    state,
    lines,
    partial,
    preview,
    participants,
    audioActive,
    personal,
    personalPending,
    sendTranscript,
    requestTranslation,
    setConfig,
    setAudioState,
    requestAudio,
    sendAudio,
    reconnect,
  };
}
