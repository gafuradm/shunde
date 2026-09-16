#!/usr/bin/env bash
# Управление сервисом Shunde Tutor на Render через API (без дашборда).
#
# Нужен ключ: Render → Account Settings → API Keys. Положите его в переменную
# окружения RENDER_API_KEY (в репозиторий ключ не коммитится).
#
#   export RENDER_API_KEY=rnd_xxxxxxxx
#   ./scripts/render-deploy.sh status      # сервис + последний деплой
#   ./scripts/render-deploy.sh env         # переменные окружения (значения замаскированы)
#   ./scripts/render-deploy.sh sync-env    # перенести backend/.env в Render
#   ./scripts/render-deploy.sh deploy      # запустить сборку и дождаться статуса live
#
# Переменные: RENDER_SERVICE_ID (по умолчанию — сервис shunde-tutor), RENDER_API_KEY.

set -euo pipefail
cd "$(dirname "$0")/.."

SID="${RENDER_SERVICE_ID:-srv-daku4o0ae00c73esjrqg}"
API="https://api.render.com/v1"
: "${RENDER_API_KEY:?нужен RENDER_API_KEY (Render → Account Settings → API Keys)}"

api() { # api METHOD PATH [JSON]
  local method="$1" path="$2" data="${3:-}"
  if [ -n "$data" ]; then
    curl -sS -X "$method" "$API$path" \
      -H "Authorization: Bearer $RENDER_API_KEY" \
      -H "Content-Type: application/json" -H "Accept: application/json" \
      --data-binary "$data"
  else
    curl -sS -X "$method" "$API$path" \
      -H "Authorization: Bearer $RENDER_API_KEY" -H "Accept: application/json"
  fi
}

show_service() {
  api GET "/services/$SID" | python3 -c '
import json, sys
d = json.load(sys.stdin)
det = d.get("serviceDetails") or {}
print("  сервис      : {0} ({1})".format(d.get("name"), d.get("id")))
print("  репозиторий : {0} @ {1}".format(d.get("repo"), d.get("branch")))
print("  тип/план    : {0} / {1} / {2}".format(det.get("env"), det.get("plan"), det.get("region")))
print("  dockerfile  : {0}".format((det.get("envSpecificDetails") or {}).get("dockerfilePath")))
print("  health      : {0}".format(det.get("healthCheckPath")))
print("  автодеплой  : {0} (trigger: {1})".format(d.get("autoDeploy"), d.get("autoDeployTrigger")))
print("  адрес       : {0}".format(det.get("url")))
print("  дашборд     : {0}".format(d.get("dashboardUrl")))
'
}

show_deploy() {
  api GET "/services/$SID/deploys?limit=1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not d:
    print("  деплоев ещё нет"); raise SystemExit
dep = d[0]["deploy"]
c = dep.get("commit") or {}
print("  статус      : {0}".format(dep.get("status")))
print("  id          : {0} (trigger: {1})".format(dep.get("id"), dep.get("trigger")))
print("  commit      : {0} {1}".format(str(c.get("id"))[:7], c.get("message")))
print("  начат/готов : {0} / {1}".format(dep.get("startedAt"), dep.get("finishedAt")))
'
}

case "${1:-status}" in
  status)
    show_service; show_deploy;;

  env)
    api GET "/services/$SID/env-vars?limit=100" | python3 -c '
import json, sys
rows = []
for item in json.load(sys.stdin):
    ev = item.get("envVar", item)
    v = str(ev.get("value") or "")
    rows.append((ev.get("key"), "(пусто)" if not v else (v if len(v) <= 24 else v[:8] + "…" + v[-4:])))
for k, v in sorted(rows):
    print("  {0:30s} = {1}".format(k, v))
print("  всего: {0}".format(len(rows)))
'
    ;;

  sync-env)
    payload=$(python3 - <<'PY'
import json, pathlib
cfg = {}
p = pathlib.Path("backend/.env")
if p.exists():
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
envs = [
    ("APP_NAME", "Shunde Tutor"),
    ("DATA_DIR", "/data"),
    ("MAX_UPLOAD_MB", cfg.get("MAX_UPLOAD_MB", "40")),
    ("CORS_ORIGINS", cfg.get("CORS_ORIGINS", "*")),
    ("ALLOW_GOOGLE_FREE_TRANSLATE", cfg.get("ALLOW_GOOGLE_FREE_TRANSLATE", "true")),
]
envs.append(("TEACHER_TOKEN", cfg.get("TEACHER_TOKEN", "")))
for k in ("LLM_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "BING_SEARCH_KEY", "ASR_API_KEY"):
    envs.append((k, cfg.get(k, "")))
envs += [
    ("LLM_BASE_URL", cfg.get("LLM_BASE_URL", "https://api.deepseek.com/v1")),
    ("LLM_MODEL", cfg.get("LLM_MODEL", "deepseek-v4-pro")),
    ("LLM_MODEL_FAST", cfg.get("LLM_MODEL_FAST", "deepseek-flash")),
    ("LLM_TEMPERATURE", cfg.get("LLM_TEMPERATURE", "0.2")),
    ("LLM_TIMEOUT", cfg.get("LLM_TIMEOUT", "120")),
    ("LLM_JSON_MODE", cfg.get("LLM_JSON_MODE", "true")),
    ("LLM_VISION", cfg.get("LLM_VISION", "false")),
    ("LLM_DISABLE_THINKING", cfg.get("LLM_DISABLE_THINKING", "true")),
    ("SEARCH_PAGES", cfg.get("SEARCH_PAGES", "2")),
    ("SEARCH_DNS_GUARD", cfg.get("SEARCH_DNS_GUARD", "false")),
    ("ASR_BASE_URL", cfg.get("ASR_BASE_URL", "https://api.openai.com/v1")),
    ("ASR_MODEL", cfg.get("ASR_MODEL", "whisper-1")),
]
print(json.dumps([{"key": k, "value": v} for k, v in envs]))
PY
)
    api PUT "/services/$SID/env-vars" "$payload" > /dev/null
    echo "  переменные обновлены из backend/.env"
    echo "  теперь: ./scripts/render-deploy.sh deploy"
    ;;

  deploy)
    resp=$(api POST "/services/$SID/deploys" '{"clearCache":"do_not_clear"}')
    did=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
    if [ -z "$did" ]; then
      echo "  не удалось запустить деплой: $resp"; exit 1
    fi
    echo "  деплой запущен: $did"
    for _ in $(seq 1 60); do
      st=$(api GET "/services/$SID/deploys?limit=1" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["deploy"]["status"])')
      printf '  %s\n' "$st"
      case "$st" in
        live) echo "  готово"; break;;
        build_failed|update_failed|canceled) echo "  ОШИБКА: $st"; exit 1;;
      esac
      sleep 10
    done
    ;;

  *)
    echo "Использование: $0 {status|env|sync-env|deploy}" >&2; exit 2;;
esac
