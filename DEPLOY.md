# Деплой Shunde Tutor

Приложение собрано так, что **и API, и SPA живут на одном порту**: FastAPI отдаёт
`/api/*`, `/ws/*` и статику из `frontend/dist`. Поэтому для хостинга достаточно
одного контейнера — никаких отдельных фронтенд-хостингов не нужно.

- [`Dockerfile`](Dockerfile:1) — двухэтапная сборка (Node собирает SPA → Python-рантайм).
- [`.dockerignore`](.dockerignore:1) — исключает `.env`, `node_modules`, `backend/data`.
- [`render.yaml`](render.yaml:1) — готовый Blueprint для Render (тариф `free`).

---

## Вариант 1. Мгновенно и без регистрации — один скрипт

```bash
./scripts/public-link.sh
```

Скрипт собирает фронтенд, поднимает бэкенд на 8000, открывает туннель и **проверяет,
что адрес реально отвечает** (`/api/health` → 200), прежде чем его напечатать.
Адрес сохраняется в `/tmp/shunde_public_url.txt`.

Провайдер туннеля выбирается автоматически:

| Провайдер | Адрес | Комментарий |
|---|---|---|
| cloudflared Quick Tunnel | `https://<имя>.trycloudflare.com` | если edge доступен из вашей сети |
| ssh localhost.run | `https://<хеш>.lhr.life` | фолбэк без аккаунта; адрес меняется при переподключении |

Принудительный выбор: `TUNNEL=cloudflared ./scripts/public-link.sh`,
`TUNNEL=localhost …` или `TUNNEL=ngrok …`.

> **Наблюдение из этой сети:** edge Cloudflare заблокирован локальным прокси/VPN
> (Clash даёт `TLS handshake with edge error: EOF`, адрес отвечает `530`).
> Скрипт это распознаёт и сам переключается на localhost.run — он работает.

Полная проверка опубликованного адреса (HTTP, статика, документы, WebSocket,
сквозной live-перевод):

```bash
backend/.venv/bin/python scripts/smoke_deploy.py https://<ваш-адрес>
```

---

## Вариант 2. Постоянный бесплатный хостинг — Render

Бесплатный тариф Render тянет WebSocket и SSE (это критично для живых лекций
и для стрима объяснений), умеет собирать [`Dockerfile`](Dockerfile:1) и не требует
банковской карты.

**Этот проект уже задеплоен так:** репозиторий `gafuradm/shunde` (ветка `main`) →
сервис `shunde-tutor` → **https://shunde-tutor.onrender.com**
(`region: singapore`, `plan: free`, `healthCheckPath: /api/health`, `autoDeploy: yes`).

1. Создайте пустой репозиторий на GitHub и залейте туда проект
   (папка должна быть git-репозиторием, `.env` уже в [`.gitignore`](.gitignore:1)).
2. На [render.com](https://render.com) → **New → Blueprint** → выберите репозиторий.
   Render прочитает [`render.yaml`](render.yaml:1) и создаст сервис `shunde-tutor`.
3. Впишите секреты, помеченные `sync: false`: как минимум `LLM_API_KEY`
   (остальное — по желанию). `TEACHER_TOKEN` Render сгенерирует сам — посмотрите
   его в **Environment** и введите на странице курса как токен преподавателя.
4. Дождитесь сборки: `https://<имя-сервиса>.onrender.com`.

### Вариант 2б. Через Render API (без кликов в дашборде)

Нужен API-ключ: **Account Settings → API Keys**. Храните его только в переменной
окружения, в репозиторий не коммитьте.

```bash
export RENDER_API_KEY=rnd_xxxxxxxx
OWNER=$(curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/owners?limit=1" | python3 -c 'import json,sys;print(json.load(sys.stdin)[0]["owner"]["id"])')

# 1. создать сервис (переменные окружения здесь Render ИГНОРИРУЕТ)
curl -s -X POST "https://api.render.com/v1/services" \
  -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  -d "{\"type\":\"web_service\",\"name\":\"shunde-tutor\",\"ownerId\":\"$OWNER\",
       \"repo\":\"https://github.com/<user>/shunde\",\"branch\":\"main\",\"autoDeploy\":\"yes\",
       \"serviceDetails\":{\"env\":\"docker\",\"plan\":\"free\",\"region\":\"singapore\",
       \"healthCheckPath\":\"/api/health\",\"dockerfilePath\":\"./Dockerfile\"}}"

# 2. задать переменные окружения (полный список — см. render.yaml)
curl -s -X PUT "https://api.render.com/v1/services/$SID/env-vars" \
  -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  -d '[{"key":"LLM_API_KEY","value":"sk-..."},{"key":"TEACHER_TOKEN","value":"..."},
       {"key":"DATA_DIR","value":"/data"},{"key":"CORS_ORIGINS","value":"*"},
       {"key":"LLM_BASE_URL","value":"https://api.deepseek.com/v1"},
       {"key":"LLM_MODEL","value":"deepseek-v4-pro"},{"key":"LLM_MODEL_FAST","value":"deepseek-flash"},
       {"key":"LLM_DISABLE_THINKING","value":"true"}]'

# 3. перезапустить, чтобы переменные применились
curl -s -X POST "https://api.render.com/v1/services/$SID/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  -d '{"clearCache":"do_not_clear"}'
```

Что важно знать про этот путь (проверено на практике):

| Грабли | Как обойти |
|---|---|
| `envVars` внутри `serviceDetails` при `POST /services` **молча игнорируются** | после создания вызвать `PUT /services/{id}/env-vars` |
| Смена переменных окружения **не** запускает пересборку | явный `POST /services/{id}/deploys` |
| Первый деплой стартует сам сразу после создания сервиса — и без переменных | не пугайтесь `features.llm: false` в `/api/health` до второго деплоя |
| Проверить, что `Dockerfile` подхватился | `GET /services/{id}` → `serviceDetails.envSpecificDetails.dockerfilePath` |

Статус деплоя: `GET /services/{id}/deploys?limit=1` → `build_in_progress` →
`update_in_progress` → `live`.

Всё это завёрнуто в [`scripts/render-deploy.sh`](scripts/render-deploy.sh:1):

```bash
export RENDER_API_KEY=rnd_xxxxxxxx
./scripts/render-deploy.sh status     # сервис + последний деплой
./scripts/render-deploy.sh env        # переменные (значения замаскированы)
./scripts/render-deploy.sh sync-env   # перенести backend/.env в Render
./scripts/render-deploy.sh deploy     # собрать и дождаться live
```

> **Важно про автодеплой.** У сервиса, созданного через API, стоит
> `autoDeploy: yes / autoDeployTrigger: commit`, но GitHub-вебхук при этом
> **не подключён** (Render не получает событие push). Поэтому после `git push`
> нужно явно выполнить `./scripts/render-deploy.sh deploy` — либо в дашборде
> Render один раз подключить GitHub-приложение к репозиторию, и тогда пуши
> начнут собираться сами.

Особенности бесплатного тарифа:

| Что | Как ведёт себя |
|---|---|
| Простой | Сервис засыпает через ~15 мин без запросов, первый запрос после сна — 30–60 с. |
| Файловая система | **Эфемерная**: `backend/data` (SQLite + загрузки) обнуляется при каждом деплое и рестарте. |
| RAM | 512 МБ — для разметки и живых лекций достаточно. |
| Хранение данных | Чтобы документы не пропадали, нужен платный диск (`disk:` в `render.yaml`) или внешняя БД (Postgres + правка [`backend/app/db.py`](backend/app/db.py:1)). |

---

## Вариант 3. Другие платформы (Docker-образ переносим как есть)

- **Hugging Face Spaces** (Docker, бесплатно, без карты): создайте Space → SDK
  `Docker`, залейте проект, в `README.md` Space укажите `app_port: 8000`,
  секреты — в *Settings → Variables and secrets*.
- **Fly.io** (`flyctl launch --dockerfile Dockerfile`, затем `flyctl secrets set`):
  нужен `[http_service] internal_port = 8000`; карта требуется для верификации.
- **Koyeb**, **Railway**, **Zeabur** — тот же Dockerfile, порт берётся из `$PORT`.

Порт нигде не зашит: [`Dockerfile`](Dockerfile:1) запускает
`uvicorn --host 0.0.0.0 --port ${PORT:-8000}`, а `--proxy-headers` нужен, чтобы
FastAPI видел схему `https` за прокси хостинга.

---

## Обязательные переменные окружения

Значения — те же, что в [`backend/.env.example`](backend/.env.example:1); локальный
[`backend/.env`](backend/.env:1) на хостинг **не** попадает (он в `.dockerignore`).

| Переменная | Обязательно | Значение для деплоя |
|---|---|---|
| `LLM_API_KEY` | да | ключ DeepSeek (без него нет разметки и перевода) |
| `TEACHER_TOKEN` | да | секрет преподавателя; на Render — `generateValue: true` |
| `DATA_DIR` | нет | `/data` (путь внутри контейнера) |
| `CORS_ORIGINS` | нет | `*` либо точный домен, например `https://shunde-tutor.onrender.com` |
| `LLM_BASE_URL` | нет | `https://api.deepseek.com/v1` |
| `LLM_MODEL` / `LLM_MODEL_FAST` | нет | `deepseek-v4-pro` / `deepseek-flash` |
| `LLM_DISABLE_THINKING` | нет | `true` — мгновенный перевод речи без «размышлений» |
| `TAVILY_API_KEY`, `SERPER_API_KEY`, `BING_SEARCH_KEY` | нет | платные поисковики; без них работают бесплатные источники |
| `ASR_API_KEY` | нет | серверное распознавание речи (иначе — Web Speech в браузере) |

Проверка после деплоя:

```bash
curl -s https://<ваш-домен>/api/health
```

В ответе `features.llm` должно быть `true`, `features.web_search` — список
доступных источников.

---

## Локальная проверка «как в контейнере»

Docker локально не требуется — можно смоделировать окружение хостинга:

```bash
cd frontend && npm run build && cd ..
DATA_DIR=/tmp/shunde-deploy PORT=8000 \
  backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```
