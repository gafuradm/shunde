# 顺德 Shunde Tutor

Учебная платформа для вузовского курса китайского языка (включая 文言文 / вэньянь) и английского.

Состоит из двух частей:

**Часть 1 — работа с документами.**
Преподаватель загружает документ любого формата. ИИ (или преподаватель вручную) размечает важные
моменты: понятия, термины, факты, даты, цитаты и потенциально непонятные студентам места. Когда студент
наводит курсор на подсвеченный фрагмент, приложение стримит объяснение: ассистент сам ищет информацию
в интернете (Wikipedia, DuckDuckGo, Mojeek, Bing) и синтезирует понятное объяснение со ссылками на источники.

**Часть 2 — живые лекции.**
Преподаватель запускает лекцию и говорит в микрофон. Студенты подключаются со своих устройств по коду
комнаты или ссылке, слушают аудио и читают субтитры с **мгновенным переводом** (по умолчанию на английский).
Есть личный перевод произвольного выделенного фрагмента, режимы объяснения (упростить / пересказать /
перевести / пример), озвучка строк и экспорт субтитров в SRT и Markdown.

Приложение полностью работоспособно **без ключей LLM** (деградированный режим: эвристическая разметка,
поиск в Википедии и бесплатный перевод) — удобно для локальной демонстрации.

---

## Содержание

- [Возможности](#возможности)
- [Требования](#требования)
- [Быстрый старт (dev)](#быстрый-старт-dev)
- [Запуск в один порт (production-сборка)](#запуск-в-один-порт-production-сборка)
- [Публикация в интернете (деплой)](#публикация-в-интернете-деплой)
- [Конфигурация](#конфигурация)
- [Структура проекта](#структура-проекта)
- [Сценарии использования](#сценарии-использования)
- [API](#api)
- [Протокол live-комнаты (WebSocket)](#протокол-live-комнаты-websocket)
- [Деградированный режим](#деградированный-режим)
- [Диагностика](#диагностика)
- [Что уже проверено](#что-уже-проверено)

---

## Возможности

### Документы
- Загрузка файлов любых распространённых форматов: `pdf`, `docx`, `doc`, `pptx`, `xlsx`, `epub`, `odt`,
  `rtf`, `html`, `md`, `txt`, `csv`; сканы — через OCR (если включён `LLM_VISION`), аудио/видео транскрипты.
- Автоопределение языка документа, отдельная поддержка **вэньяня** (китайский классический) — язык
  `zh-classical` влияет на подсказки модели, глоссарий, разметку и перевод.
- ИИ-разметка в двух режимах: «Быстро» (быстрая модель) и «Глубоко» (основная модель) — фоновой задачей,
  с видимым прогрессом.
- Ручная разметка: выделил фрагмент → выбрал вид (`concept`, `term`, `fact`, `unclear`, `allusion`,
  `grammar`, `emphasis`).
- Объяснение по наведению курсора: SSE-поток (`POST /api/explain/stream`), режимы `explain / simplify /
  translate / example` и глубина `simple / standard / advanced`, со списком источников.
- Студенческие заметки хранятся локально в браузере (localStorage), не на сервере.
- Экспорт документа с разметкой в Markdown.

### Живые лекции
- Комната по коду из 6 символов и ссылке `/live/{CODE}`.
- Распознавание речи прямо в браузере (Web Speech API), выбор языка речи: 普通话 / 文言文 / English /
  Русский / 日本語. Опционально — серверный ASR (`POST /api/lectures/{code}/transcribe`).
- Мгновенный перевод: промежуточные превью по мере речи + финальный перевод каждой реплики.
- Аудио-релей: преподаватель публикует поток (MediaRecorder → WebSocket → MediaSource у студентов),
  включая поздних зрителей (заголовок потока досылается при подключении).
- Личный перевод: студент выделяет текст в субтитрах и переводит его на свой язык (кэшируется в комнате).
- Экспорт субтитров в **SRT** и **Markdown**, озвучка строк через `speechSynthesis`.
- Роли: ведущий (нужен токен преподавателя) и зритель. Интерфейс на трёх языках: RU / EN / 中文.

---

## Требования

- **Python 3.11+**
- **Node.js 18+** и npm
- (необязательно) ключ LLM API — DeepSeek, OpenAI, OpenRouter, Ollama, vLLM (любой OpenAI-совместимый)

---

## Быстрый старт (dev)

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # ключи можно не заполнять — заработает деградированный режим
uvicorn app.main:app --reload --port 8000
```

Проверка: `curl -s http://127.0.0.1:8000/api/health` — вернёт `{"status":"ok", ...}`,
документация API: <http://127.0.0.1:8000/docs>.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                   # http://127.0.0.1:5173, /api и /ws проксируются на порт 8000
```

> **Важно.** Если в окружении задан `NODE_ENV=production` или в npm включён `omit=dev`
> (`npm config get omit` → `dev`), обычный `npm install` не поставит devDependencies, и сборка упадёт
> с ошибками вида `TS7016: Could not find a declaration file for module 'react'`.
> В этом случае ставьте зависимости так:
>
> ```bash
> npm install --include=dev
> ```

### 3. Токен преподавателя

Откройте UI, введите токен (по умолчанию `teacher`, задаётся в `TEACHER_TOKEN`) — он сохранится в
`localStorage` под ключом `shunde.teacherToken` и будет отправляться в заголовке `X-Teacher-Token`.
Без него доступны только чтение документов и вход в лекцию как зритель.

---

## Запуск в один порт (production-сборка)

```bash
cd frontend && npm run build          # собирает frontend/dist
cd ../backend && uvicorn app.main:app --port 8000
```

Backend сам отдаёт собранный SPA: `frontend/dist/index.html` и ассеты, а все пути, кроме `/api/*` и
`/ws/*`, ведут на `index.html` (клиентский роутинг). Приложение целиком доступно на
<http://127.0.0.1:8000>.

> Наличие каталога `frontend/dist` проверяется **в момент импорта** [`backend/app/main.py`](backend/app/main.py:32).
> Если вы собрали фронтенд уже после старта сервера, перезапустите uvicorn (или при `--reload`
> коснитесь файла: `touch backend/app/main.py`) — иначе вместо SPA будет отдаваться JSON-заглушка
> с подсказкой «Соберите frontend…».

---

## Публикация в интернете (деплой)

Приложение живёт на **одном порту**: FastAPI отдаёт и `/api/*`, и `/ws/*`, и собранный
SPA, поэтому для хостинга достаточно одного контейнера.

```bash
./scripts/public-link.sh                                            # публичный https за ~15 с, без регистрации
backend/.venv/bin/python scripts/smoke_deploy.py https://<адрес>    # проверка снаружи
```

`public-link.sh` собирает фронтенд, поднимает бэкенд и открывает туннель
(cloudflared, при блокировке edge — ssh localhost.run), а затем проверяет,
что адрес отвечает. `smoke_deploy.py` прогоняет health, SPA-маршруты, статику,
загрузку документа, WebSocket-комнату и сквозной live-перевод.

Постоянный бесплатный хостинг — Render по [`render.yaml`](render.yaml:1)
(**New → Blueprint**, секреты с `sync: false`), либо любой Docker-хостинг
(HF Spaces, Fly.io, Koyeb, Railway). Полная инструкция и таблица переменных
окружения — в [`DEPLOY.md`](DEPLOY.md:1).

---

## Конфигурация

### `backend/.env` (шаблон — [`backend/.env.example`](backend/.env.example:1))

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_NAME` | `Shunde Tutor` | Название приложения |
| `DATA_DIR` | `data` | Каталог данных (БД и загрузки) |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Разрешённые origin'ы для dev |
| `MAX_UPLOAD_MB` | `40` | Максимальный размер загружаемого файла |
| `TEACHER_TOKEN` | `teacher` | Токен преподавателя (права ведущего лекции) |
| `LLM_API_KEY` | пусто | Ключ OpenAI-совместимого API; пусто → деградированный режим |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | Базовый URL LLM |
| `LLM_MODEL` | `deepseek-chat` | Основная модель («Глубоко»); для шлюза из примера — `deepseek-v4-pro` |
| `LLM_MODEL_FAST` | пусто | Быстрая модель («Быстро»); пусто → основная; для шлюза — `deepseek-flash` |
| `LLM_TEMPERATURE` / `LLM_TIMEOUT` | `0.2` / `120` | Параметры генерации |
| `LLM_JSON_MODE` | `false` | Провайдер поддерживает `response_format=json_object` |
| `LLM_DISABLE_THINKING` | `false` | Выключает «размышления» reasoning-моделей (`thinking: disabled`): ответ приходит сразу и не съедает лимит токенов |
| `LLM_VISION` | `false` | Модель умеет читать изображения (OCR сканов и слайдов) |
| `TAVILY_API_KEY` / `SERPER_API_KEY` / `BING_SEARCH_KEY` | пусто | Платные поисковики (дополняют бесплатные) |
| `SEARCH_PAGES` | `2` | Сколько страниц-источников выкачивать (0 — только сниппеты) |
| `SEARCH_DNS_GUARD` | `false` | Анти-SSRF проверка DNS; `false` нужен при VPN/Clash с fake-IP |
| `ALLOW_GOOGLE_FREE_TRANSLATE` | `true` | Бесплатный перевод без LLM |
| `ASR_API_KEY` / `ASR_BASE_URL` / `ASR_MODEL` | пусто / `https://api.openai.com/v1` / `whisper-1` | Серверное распознавание речи |

Все параметры описаны в коде: [`backend/app/config.py`](backend/app/config.py:12).

Если провайдер — шлюз с reasoning-моделями (`deepseek-v4-pro`, `deepseek-flash`), включите
`LLM_DISABLE_THINKING=true`. Иначе модель расходует лимит `max_tokens` на `reasoning_content` и может
вернуть пустой `content` — для мгновенного перевода это выглядело бы как «пустые субтитры». Для
провайдеров, не знающих этот параметр, клиент автоматически повторяет запрос без него
([`backend/app/services/llm.py`](backend/app/services/llm.py:1)). Все запросы идут с запасом по лимиту:
перевод речи — 1200 (превью) / 1500 (финал) токенов, объяснение — 1800.

### `frontend/.env` (шаблон — [`frontend/.env.example`](frontend/.env.example:1))

| Переменная | Назначение |
|---|---|
| `VITE_API_BASE` | Адрес API; пусто → относительные пути `/api` |
| `VITE_WS_BASE` | Адрес WebSocket; пусто → берётся из `window.location` |
| `VITE_API_TARGET` | Куда проксировать `/api` и `/ws` в dev (по умолчанию `http://127.0.0.1:8000`) |

---

## Структура проекта

```
shunde/
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   └── app/
│       ├── main.py            # FastAPI, CORS, /api/health, отдача SPA
│       ├── config.py          # настройки из backend/.env
│       ├── db.py              # sqlite3: init, query, insert, update
│       ├── schemas.py         # Pydantic-схемы
│       ├── deps.py            # require_teacher (X-Teacher-Token)
│       ├── routers/
│       │   ├── documents.py   # загрузка, текст, разметка, экспорт
│       │   ├── explain.py     # объяснения (SSE-стрим)
│       │   └── lectures.py    # CRUD лекций, экспорт, WebSocket /ws/lectures/{code}
│       └── services/
│           ├── extract.py     # извлечение текста: pdf/docx/pptx/xlsx/epub/rtf/html/ocr
│           ├── text.py        # нормализация, определение языка, разбиение на предложения
│           ├── llm.py         # OpenAI-совместимый клиент + JSON-режим + warmup
│           ├── annotate.py    # ИИ-разметка + эвристика + глоссарий
│           ├── search.py      # Wikipedia / DDG / Mojeek / Bing HTML (+ Tavily/Serper)
│           ├── explain.py     # сборка контекста, промпты, стриминг объяснения
│           ├── translate.py   # перевод через LLM или бесплатный fallback
│           └── hub.py         # комнаты live-лекций: участники, субтитры, перевод, аудио
└── frontend/
    ├── package.json / vite.config.ts / tailwind.config.js / tsconfig.json
    └── src/
        ├── App.tsx            # маршруты: /, /documents, /documents/:id, /lectures, /live/:code
        ├── index.css          # Tailwind + утилиты подсветки (.mk-*), кнопок, карточек
        ├── lib/               # api.ts (REST+wsUrl), sse.ts, storage.ts, types.ts, i18n.tsx (ru/en/zh)
        ├── hooks/             # useLiveRoom.ts, useSpeechRecognition.ts, useAudioRelay.ts
        ├── components/        # Layout.tsx, ExplainPanel.tsx, MiniMarkdown.tsx
        └── pages/             # HomePage, DocumentsPage, DocumentReaderPage, LecturesPage, LiveRoomPage
```

---

## Сценарии использования

### Разметка документа и объяснения
1. `/documents` → загрузить файл (нужен токен преподавателя).
2. У документа нажать **Быстро** или **Глубоко** — запустится ИИ-разметка (фоновая задача, прогресс в UI).
3. Открыть документ: подсвеченные фрагменты различаются по видам (легенда в панели).
4. Навести курсор на фрагмент → в панели справа стримится объяснение со ссылками на источники;
   можно переключить режим (объяснить / упростить / перевести / пример) и глубину, включить озвучку.

### Живая лекция
1. `/lectures` → выбрать язык речи и язык перевода (по умолчанию `zh-classical → en`) → **Create a lecture**.
2. **Open as teacher** (`/live/{CODE}?role=host`): «Start», затем «🎙 Start speaking» (Web Speech API).
   Для трансляции аудио — «Turn audio on» (MediaRecorder, по умолчанию для вещания микрофон).
3. Студенты открывают **Open as student** (`/live/{CODE}`) или вводят код на главной: видят субтитры
   с мгновенным переводом и (при включённом аудио) слышат преподавателя.
4. Выделение текста в субтитрах + **Translate selection** → перевод на выбранный студентом язык.
5. По окончании — **End**, экспорт **SRT** / **Markdown** из кабинета лекций.

---

## API

Все эндпоинты, кроме помеченных 🔓, требуют заголовок `X-Teacher-Token`.

### Документы — `/api/documents`
| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/documents` | 🔓 Загрузка файла (multipart: `file`, `title?`, `source_lang?`) |
| `GET` | `/api/documents` | 🔓 Список документов |
| `GET` | `/api/documents/{doc_id}` | 🔓 Документ с текстом |
| `GET` | `/api/documents/{doc_id}/status` | 🔓 Статус обработки/разметки |
| `GET` | `/api/documents/{doc_id}/text` | 🔓 Текст как `text/plain` |
| `DELETE` | `/api/documents/{doc_id}` | Удаление |
| `GET` | `/api/documents/{doc_id}/annotations` | 🔓 Разметка документа |
| `POST` | `/api/documents/{doc_id}/annotations` | Ручная разметка (`quote`, `kind`, …) |
| `POST` | `/api/documents/{doc_id}/annotations/ai` | Запуск ИИ-разметки (`mode: "quick"` \| `"deep"`) |
| `DELETE` | `/api/documents/{doc_id}/annotations?only_ai=` | Очистка разметки |
| `GET` | `/api/documents/{doc_id}/export` | 🔓 Экспорт с разметкой в Markdown |
| `PATCH` | `/api/annotations/{ann_id}` | Правка одной аннотации |
| `DELETE` | `/api/annotations/{ann_id}` | Удаление одной аннотации |

### Объяснения — `/api/explain`
| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/explain/stream` | 🔓 SSE-поток объяснения фрагмента (тело — `ExplainRequest`, см. ниже) |
| `POST` | `/api/explain` | 🔓 То же без стриминга (одним JSON-ответом) |
| `GET` | `/api/explain/history` | 🔓 История объяснений |

Тело запроса `ExplainRequest`:

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `quote` | `string` | — | **Обязательное.** Выделенный фрагмент текста |
| `document_id` | `string?` | `null` | Документ-источник (для контекста и кэша) |
| `start_char` / `end_char` | `int?` | `null` | Позиция фрагмента в документе |
| `context` | `string?` | `null` | Окружающий текст (до 400 символов попадает в кэш) |
| `ui_lang` | `string` | `"ru"` | Язык ответа студента (`ru` / `en` / `zh`) |
| `level` | `"simple" \| "standard" \| "advanced"` | `"standard"` | Глубина объяснения |
| `mode` | `"explain" \| "simplify" \| "translate" \| "example"` | `"explain"` | Режим: объяснить / упростить / перевести / пример |
| `force` | `bool` | `false` | Игнорировать кэш и пересчитать |
| `extra_query` | `string?` | `null` | Дополнительный поисковый запрос |

Пример:

```bash
curl -sN -X POST http://127.0.0.1:8000/api/explain/stream \
  -H 'Content-Type: application/json' \
  -d '{"document_id":"doc_xxx","quote":"学而时习之","mode":"explain","level":"standard","ui_lang":"ru"}'
```

SSE-события: `meta` → `status` (`planning` → `searching` → `writing`) → `sources` → `delta` (текст по частям) → `done`; при ошибке — `error`. В деградированном режиме (без LLM) ответ приходит одним `delta` с пометкой `degraded: true` и реальными источниками из веб-поиска.

### Лекции — `/api/lectures`
| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/lectures` | Создать лекцию (`title?`, `document_id?`, `source_lang?`, `target_lang?`) |
| `GET` | `/api/lectures` | 🔓 Список лекций |
| `GET` | `/api/lectures/{code}` | 🔓 Лекция + счётчики участников и строк |
| `POST` | `/api/lectures/{code}/start` \| `/end` | Старт / завершение |
| `DELETE` | `/api/lectures/{code}` | Удаление лекции и её строк |
| `GET` | `/api/lectures/{code}/lines` | 🔓 Расшифровка лекции |
| `POST` | `/api/lectures/{code}/config` | Смена `source_lang` / `target_lang` / `title` |
| `POST` | `/api/lectures/{code}/transcribe` | Серверный ASR (нужен `ASR_API_KEY`) |
| `GET` | `/api/lectures/{code}/export/srt` \| `/markdown` | 🔓 Экспорт субтитров |

### Прочее
| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/health` | 🔓 Статус и доступные возможности (LLM, поиск, ASR) |
| `GET` | `/docs` | 🔓 Swagger UI |

---

## Протокол live-комнаты (WebSocket)

Подключение: `ws://<host>/ws/lectures/{code}?role=host|viewer&token=…&name=…`

- `role=host` требует валидный `token` (псевдонимы роли: `host`, `teacher`, `presenter`, `tutor`).
- Коды закрытия: **4403** — неверный токен, **4404** — комната не найдена.

Сообщения сервер → клиент:

| `type` | Полезная нагрузка |
|---|---|
| `hello` | `client_id`, `role`, `state` |
| `history` | `lines[]` (расшифровка на момент подключения), `state` |
| `participants` | `viewers`, `hosts`, `total` |
| `partial` | `seq`, `text`, `lang` — промежуточная распознанная речь |
| `line` | `line{id, seq, text, translation, target_lang, final, created_at}` |
| `translation_preview` | `seq`, `line_id`, `target`, `text`, `pending` — черновой перевод «на лету» |
| `translation` | `seq`, `line_id`, `target`, `text`, `pending` — финальный перевод реплики |
| `personal_translation` | `line_id`, `target`, `text` — ответ на личный запрос перевода |
| `config` / `status` | `state` (смена языков, старт/конец лекции) |
| `audio_state` | `active` — идёт ли аудио-трансляция |
| `error` | `message` |

Сообщения клиент → сервер:

| `type` | Кто | Полезная нагрузка |
|---|---|---|
| `transcript` | ведущий | `text`, `final` (`false` — промежуточный), `target?` |
| `translate_request` | любой | `text`, `target`, `line_id?` — личный перевод |
| `set_config` | ведущий | `source_lang?`, `target_lang?` |
| `audio_state` | ведущий | `active` |
| `audio_request` | зритель | просьба дослать заголовок аудио-потока |
| `rename` | любой | `name` |
| `ping` | любой | ответ — `pong` |

Бинарные кадры WebSocket используются как аудио-релей: первые байты ведущего становятся заголовком
потока комнаты и досылаются каждому новому зрителю. Реализация — [`backend/app/services/hub.py`](backend/app/services/hub.py:1),
подключение — [`backend/app/routers/lectures.py`](backend/app/routers/lectures.py:277).

---

## Деградированный режим

Без `LLM_API_KEY` приложение остаётся полностью работоспособным:

- разметка — эвристика по правилам (даты, имена, идиомы, устойчивые выражения, цитаты) с учётом вэньяня;
- объяснения — выдержки из найденных источников (Wikipedia и др.) с честной пометкой «LLM не настроен»;
- перевод — бесплатный публичный endpoint Google Translate (`ALLOW_GOOGLE_FREE_TRANSLATE=true`).

Текущий режим видно в бейдже шапки (`DEGRADED`) и в футере: `LLM: off · search: wikipedia, duckduckgo,
mojeek, bing_html`. Чтобы включить ИИ, достаточно заполнить `LLM_API_KEY` (+ при необходимости
`LLM_BASE_URL`, `LLM_MODEL`) в [`backend/.env`](backend/.env.example:20) и перезапустить сервер.

В этом проекте ключ уже вписан (`LLM_MODEL=deepseek-v4-pro`, `LLM_MODEL_FAST=deepseek-flash`,
`LLM_JSON_MODE=true`, `LLM_DISABLE_THINKING=true`) — проверено по `/api/health` → `features.llm: true`.
В этом режиме бейдж `DEGRADED` не показывается, разметка выполняется «глубоким» движком LLM,
объяснения синтезирует модель (с опорой на найденные источники), а перевод речи делает модель,
а не публичный Google Translate.

---

## Диагностика

| Симптом | Причина и решение |
|---|---|
| `npm run build` падает с `TS7016 … module 'react'` | Не установлены devDependencies (`NODE_ENV=production` / `omit=dev`) → `npm install --include=dev` |
| На `/` JSON-заглушка вместо интерфейса | `frontend/dist` отсутствовал при старте uvicorn → `npm run build` и перезапуск (или `touch backend/app/main.py`) |
| Объяснения пустые, «LLM не настроен» | Не задан `LLM_API_KEY` — это ожидаемое поведение деградированного режима |
| Субтитры пустые, хотя речь распознаётся | Reasoning-модель израсходовала лимит на размышления → задайте `LLM_DISABLE_THINKING=true` (лимиты токенов уже подняты в коде) |
| В источниках объяснения мусор (отели, WhatsApp, пиццерии) | Скраперы Bing HTML / DuckDuckGo отдали гео-редирект или анти-бот-страницу; такие находки отбрасываются по токенам запроса, а при полном отсутствии релевантных используется Wikipedia ([`search.py`](backend/app/services/search.py:495), [`search.py`](backend/app/services/search.py:593)) |
| Поиск ничего не находит за VPN | Подмена DNS (fake-IP): оставьте `SEARCH_DNS_GUARD=false`, проверьте `/api/health` → `web_search.*` |
| Микрофон не работает | Web Speech API доступен только в Chrome/Edge и по `https` либо `localhost`/`127.0.0.1`; при запрете доступа — используйте серверный ASR |
| Аудио не слышно у студентов | Ведущий должен нажать «Turn audio on»; аудио-релей требует поддержки `MediaSource` с `audio/webm;codecs=opus` (Chrome/Edge/Firefox) |
| 401 на действиях преподавателя | Неверный токен: сверьте введённый токен с `TEACHER_TOKEN` |

---

## Что уже проверено

- Backend: загрузка документов, определение языка (включая `zh-classical`), ИИ-разметка (13 аннотаций
  на `论语 学而`), SSE-объяснение, экспорт в Markdown/SRT, WebSocket-перевод и личный перевод.
- Сборка фронтенда: `tsc -b && vite build` — `49 modules transformed`, ~240 КБ JS (76 КБ gzip), без ошибок.
- В браузере: главная (RU/EN/中文, бейдж режима), список документов, читалка с 13 подсвеченными
  фрагментами, hover-объяснение с источником из Википедии, кабинет лекций, live-комната в ролях
  зрителя и ведущего — субтитры с мгновенным английским переводом, личный перевод выделения, экспорт.
- **Сквозной сценарий (последний прогон):** загружен новый документ (`E2E: Мэн-цзы и Лунь юй`) →
  язык определён как `zh-classical` (`classical: 1.0`) → ИИ-разметка `mode: "quick"` (3 аннотации,
  движок `heuristic`) → `POST /api/explain/stream` отдал `meta → status → sources` с реальным
  источником из Википедии.
- **Живая лекция (последний прогон):** создана комната → `start` → имитация речи преподавателя
  по WebSocket (3 фразы вэньяня) → для каждой фразы получены `translation_preview` (перевод
  «на лету») и финальный `translation`; экспорт **SRT** и **Markdown** содержит все 3 пары
  «иероглифы → английский»; комната удалена после проверки.
- Исправлен найденный при проверке дефект UI: устаревшие промежуточные переводы (`translation_preview`)
  могли «зависать» в субтитрах, потому что превью и финал приходят с разными `seq`. Теперь
  [`prunePreview()`](frontend/src/hooks/useLiveRoom.ts:67) вычищает устаревшие превью на каждом событии.

**Прогоны после подключения LLM-ключа (текущее состояние):**

- ИИ-разметка `mode: "deep"` на `论语 学而`: движок `llm`, **32 аннотации** вместо 13 эвристических
  (`term` 11, `concept` 8, `grammar` 8, `fact` 4, `allusion` 1), у всех `created_by: ai`. Пояснение
  хранится в полях `title` (китайский заголовок) и `rationale` (русское объяснение), перевод — в `meta.en`.
- SSE-объяснение с LLM: `meta {"llm": true}` → 3 события `status` (planning/searching/writing, поисковые
  запросы сгенерировала модель) → `sources` → поток из 1069 и 1481 `delta` → `done` (ответ 2,9 и 4,2 тыс.
  символов, разбор вэньяня с пословным глоссарием, современным китайским и английским).
- Живая лекция с LLM: комната → `start` → 3 фразы вэньяня по WebSocket → 6 запросов к модели
  (3 `translation_preview` + 3 `translation`), переводы вида «The Master said, to learn and constantly
  practice it, is it not a joy?»; комната удалена, лекций в базе — 0.
- Исправлены два дефекта, найденные при подключении ключа: (1) на reasoning-моделях пустой LLM-стрим
  давал пустые субтитры — теперь запросы идут с `thinking: disabled` + запасом токенов, а пустой стрим
  переключается на бесплатный перевод; (2) в «Источники» попадал мусор от скраперов (`bad[:3]`
  в фолбэке) — теперь нерелевантные находки отбрасываются, а при их отсутствии берётся статья Wikipedia.

**Проверка состояния после всех правок:** `/api/health` → `status: ok`, `features.llm: true`,
`llm_model: deepseek-v4-pro`; `GET /` отдаёт SPA; в базе 2 демо-документа (`论语 学而` — 32 аннотации,
`Мэн-цзы и Лунь юй` — 3 аннотации), 0 лекций.

### Что осталось для продакшена

- `LLM_API_KEY` уже подключён (шлюз с моделями `deepseek-v4-pro` / `deepseek-flash`); осталось добавить
  `TAVILY_API_KEY` / `SERPER_API_KEY` (точный поиск вместо HTML-скраперов) и `ASR_API_KEY` (серверное
  распознавание речи вместо браузерного).
- Заменить `TEACHER_TOKEN` на нормальную авторизацию и вынести SQLite на PostgreSQL при росте нагрузки.
- Для распознавания речи в Chrome нужен `https` (или `localhost`) — это требование Web Speech API.
