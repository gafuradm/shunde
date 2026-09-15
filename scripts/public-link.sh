#!/usr/bin/env bash
# ============================================================================
#  Публичный HTTPS-адрес для Shunde Tutor за ~15 секунд, без регистрации.
#
#  Что делает:
#    1. собирает фронтенд (frontend/dist);
#    2. поднимает бэкенд на 127.0.0.1:$PORT (если он ещё не отвечает);
#    3. открывает наружу туннель Cloudflare и печатает адрес вида
#       https://<случайное-имя>.trycloudflare.com
#
#  Ссылка живёт, пока запущен этот скрипт (Ctrl+C — закрыть).
#  Требуется установленный cloudflared: brew install cloudflared
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
BACKEND_DIR="$ROOT_DIR/backend"
LOG_FILE="/tmp/shunde-public.log"

command -v cloudflared >/dev/null 2>&1 || {
  echo "cloudflared не найден. Установите: brew install cloudflared" >&2
  exit 1
}

# --- 1. фронтенд ------------------------------------------------------------
if [ ! -f "$ROOT_DIR/frontend/dist/index.html" ]; then
  echo "==> Сборка фронтенда…"
  (cd "$ROOT_DIR/frontend" && npm run build)
fi

# --- 2. бэкенд --------------------------------------------------------------
if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "==> Бэкенд уже работает на порту ${PORT}"
else
  echo "==> Запуск бэкенда на порту ${PORT}…"
  PYTHON="$BACKEND_DIR/.venv/bin/uvicorn"
  [ -x "$PYTHON" ] || PYTHON="uvicorn"
  (cd "$BACKEND_DIR" && nohup "$PYTHON" app.main:app \
    --host 0.0.0.0 --port "$PORT" --proxy-headers >"$LOG_FILE" 2>&1 &)

  for _ in $(seq 1 30); do
    curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 \
    || { echo "Бэкенд не поднялся, смотрите $LOG_FILE" >&2; exit 1; }
  echo "==> Бэкенд отвечает (лог: $LOG_FILE)"
fi

# --- 3. туннель -------------------------------------------------------------
echo "==> Открываю публичный адрес… (Ctrl+C — остановить)"
exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
