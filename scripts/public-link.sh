#!/usr/bin/env bash
# ============================================================================
#  Публичный HTTPS-адрес для Shunde Tutor за ~15 секунд, без регистрации.
#
#  Что делает:
#    1. собирает фронтенд (frontend/dist), если его ещё нет;
#    2. поднимает бэкенд на 127.0.0.1:$PORT (если он ещё не отвечает);
#    3. открывает наружу туннель и печатает публичный https-адрес.
#
#  Туннель выбирается автоматически (TUNNEL=auto, по умолчанию):
#    - cloudflared, если доступен   -> https://<имя>.trycloudflare.com
#    - иначе / при блокировке       -> ssh localhost.run (без аккаунта)
#                                     https://<хеш>.lhr.life
#  Принудительный выбор: TUNNEL=cloudflared|localhost|ngrok
#
#  Ссылка живёт, пока запущен этот скрипт (Ctrl+C — закрыть).
#  Установка (необязательно): brew install cloudflared   |   brew install ngrok
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
TUNNEL="${TUNNEL:-auto}"
BACKEND_DIR="$ROOT_DIR/backend"
LOG_FILE="/tmp/shunde-public.log"
CF_LOG="/tmp/shunde-cloudflared.log"
LR_LOG="/tmp/shunde-localhost-run.log"
URL_FILE="/tmp/shunde_public_url.txt"

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

announce() {
  echo "$1" >"$URL_FILE"
  echo
  echo "===================================================================="
  echo "  Публичный адрес: $1"
  echo "  (сохранён в $URL_FILE)"
  echo "===================================================================="
}

# Cloudflare Quick Tunnel. 1 = туннель непригоден (блокировка/прокси).
try_cloudflared() {
  command -v cloudflared >/dev/null 2>&1 || return 1
  : >"$CF_LOG"
  nohup cloudflared tunnel --url "http://127.0.0.1:${PORT}" >"$CF_LOG" 2>&1 &
  local pid=$!
  local url=""
  for _ in $(seq 1 25); do
    if grep -q 'Unable to establish connection with Cloudflare edge' "$CF_LOG" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      return 1
    fi
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1 || true)"
    [ -n "$url" ] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if [ -z "$url" ]; then
    kill "$pid" 2>/dev/null || true
    return 1
  fi
  # Адрес выдан, но edge может быть заблокирован и отдавать 530 — проверяем.
  if ! probe_url "$url"; then
    echo "==> Cloudflare выдал $url, но снаружи он недоступен (блокировка edge)" >&2
    kill "$pid" 2>/dev/null || true
    return 1
  fi
  announce "$url"
  wait "$pid"
  return 0
}

# Проверяет, что публичный адрес реально отвечает 200 на /api/health.
probe_url() {
  local url="$1" code=""
  for _ in $(seq 1 10); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$url/api/health" || true)"
    [ "$code" = "200" ] && return 0
    sleep 2
  done
  return 1
}

# localhost.run — обычный SSH reverse-tunnel, аккаунт не нужен.
try_localhost_run() {
  : >"$LR_LOG"
  nohup ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -R 80:localhost:"${PORT}" nokey@localhost.run >"$LR_LOG" 2>&1 &
  local pid=$!
  local url=""
  for _ in $(seq 1 30); do
    url="$(tr -d '\000' <"$LR_LOG" | grep -oE 'https://[a-z0-9.-]+\.(lhr\.life|localhost\.run|serveo\.net)' | head -1 || true)"
    [ -n "$url" ] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if [ -z "$url" ]; then
    echo "Не удалось получить адрес. Лог: $LR_LOG" >&2
    kill "$pid" 2>/dev/null || true
    return 1
  fi
  if ! probe_url "$url"; then
    echo "==> Адрес $url выдан, но пока не отвечает; смотрите лог $LR_LOG" >&2
  fi
  announce "$url"
  wait "$pid"
  return 0
}

case "$TUNNEL" in
  cloudflared)
    try_cloudflared || { echo "cloudflared не подключился к edge" >&2; exit 1; }
    ;;
  ngrok)
    command -v ngrok >/dev/null 2>&1 || { echo "ngrok не найден: brew install ngrok" >&2; exit 1; }
    exec ngrok http "$PORT"
    ;;
  localhost)
    try_localhost_run
    ;;
  auto | *)
    if try_cloudflared; then
      exit 0
    fi
    echo "==> Cloudflare недоступен из этой сети (блокировка провайдером/прокси) — переключаюсь на localhost.run"
    try_localhost_run
    ;;
esac
