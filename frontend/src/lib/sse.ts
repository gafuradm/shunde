/**
 * Чтение SSE поверх fetch (POST). EventSource не умеет POST и не отдаёт
 * заголовки, поэтому разбираем поток вручную.
 */
import { API_BASE } from "./api";
import type { ExplainRequest, Source } from "./types";

export interface ExplainEvents {
  onStatus?: (data: { stage: string; message?: string; [k: string]: unknown }) => void;
  onSources?: (data: { sources: Source[] }) => void;
  onMeta?: (data: Record<string, unknown>) => void;
  onDelta?: (chunk: string) => void;
  onDone?: (data: { answer?: string; degraded?: boolean; engine?: string }) => void;
  onError?: (message: string) => void;
}

/** Стрим объяснения. Возвращает функцию отмены. */
export function streamExplain(
  payload: ExplainRequest,
  events: ExplainEvents,
  signal?: AbortSignal,
): { abort: () => void } {
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort, { once: true });

  (async () => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/explain/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (err) {
      if (!controller.signal.aborted) events.onError?.(String(err));
      return;
    }
    if (!res.ok || !res.body) {
      events.onError?.(`HTTP ${res.status}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Кадры SSE разделяются пустой строкой.
        let idx = buffer.indexOf("\n\n");
        while (idx !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          idx = buffer.indexOf("\n\n");
          handleFrame(frame);
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) events.onError?.(String(err));
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
  })();

  function handleFrame(frame: string) {
    let event = "message";
    const dataLines: string[] = [];
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.trimEnd();
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    const raw = dataLines.join("\n");
    let data: any = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        data = { text: raw };
      }
    }

    switch (event) {
      case "meta":
        events.onMeta?.(data);
        break;
      case "status":
        events.onStatus?.(data);
        break;
      case "sources":
        events.onSources?.(data);
        break;
      case "delta":
        events.onDelta?.(String(data.text ?? data.delta ?? ""));
        break;
      case "done":
        events.onDone?.(data);
        break;
      case "error":
        events.onError?.(String(data.message ?? "Explanation failed"));
        break;
      default:
        break;
    }
  }

  return { abort: () => controller.abort() };
}
