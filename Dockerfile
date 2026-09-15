# ============================================================================
#  Shunde Tutor — единый образ: FastAPI (API + WebSocket + SSE) и собранный SPA.
#
#  Приложение отдаёт всё с одного порта: статика фронтенда из frontend/dist,
#  API — под /api/*, живые лекции — под /ws/*.
#  Порт берётся из переменной окружения PORT (так требуют Render/Fly/HF/Koyeb).
# ============================================================================

# ---------------------------------------------------------------- этап 1: сборка фронтенда
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend

# Сначала только манифесты — слой с зависимостями кэшируется между сборками.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------- этап 2: рантайм
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=8000

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код бэкенда. Структура /app/backend + /app/frontend нужна, потому что
# main.py ищет статику как parents[2] / "frontend" / "dist".
COPY backend/ ./

# Собранный SPA из первого этапа.
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

# Каталог для SQLite и загруженных файлов (переживает только внутри одного контейнера —
# на бесплатных тарифах ФС эфемерная, см. DEPLOY.md).
RUN mkdir -p /data/uploads

EXPOSE 8000

# proxy-headers — чтобы схема/хост корректно определялись за HTTPS-прокси хостинга.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
