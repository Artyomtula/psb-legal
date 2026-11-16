#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

DEFAULT_BASE_URL="https://api.deepseek.com"
DEFAULT_MODEL="deepseek-flash"
ENV_FILE=".env"
MIN_DOCKER_MEMORY_GB=6
export DOCKER_CLI_HINTS=false

title() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
say() { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

env_get() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//" || true
}

env_set() {
  local tmp
  tmp="$(mktemp)"
  if [ -f "$ENV_FILE" ]; then grep -vE "^$1=" "$ENV_FILE" > "$tmp" || true; fi
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
}

clean_path() {
  local p
  p="$(printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/' -e 's/\\\(.\)/\1/g')"
  case "$p" in "~/"*) p="$HOME/${p#\~/}" ;; esac
  printf '%s' "$p"
}

docker_ready() { docker info >/dev/null 2>&1; }

port_busy() { (exec 3<>"/dev/tcp/localhost/$1") 2>/dev/null; }

free_port() {
  local p="$1"
  while port_busy "$p"; do p=$((p + 1)); done
  printf '%s' "$p"
}

api_status() {
  printf 'Authorization: Bearer %s\n' "$2" | curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H @- "$1" || true
}

crashed() {
  local id state
  id="$(docker compose ps -a -q "$1" 2>/dev/null | head -n 1)"
  [ -n "$id" ] || return 1
  state="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$id" 2>/dev/null || true)"
  case "$state" in
    "") return 1 ;;
    exited*|dead*) return 0 ;;
  esac
  [ "${state##* }" != "0" ]
}

retry() {
  local what="$1" attempt
  shift
  for attempt in 1 2 3 4; do
    "$@" && return 0
    if [ "$attempt" -lt 4 ]; then
      warn "$what прервалась — чаще всего это сбой сети. Через 20 секунд пробую ещё раз (попытка $((attempt + 1)) из 4), готовые шаги не повторяются…"
      sleep 20
    fi
  done
  return 1
}

base_images() { sed -nE 's/^FROM +(--platform=[^ ]+ +)?([^ ]+:[^ ]+).*/\2/p' Dockerfile open-webui/Dockerfile | sort -u; }

wait_http() {
  local url="$1" what="$2" limit="$3" service="${4:-}" waited=0
  printf 'Жду %s' "$what"
  until curl -sf -o /dev/null --max-time 5 "$url"; do
    if [ -n "$service" ] && crashed "$service"; then echo; return 1; fi
    if [ "$waited" -ge "$limit" ]; then echo; return 1; fi
    sleep 5
    waited=$((waited + 5))
    printf '.'
  done
  say " готово"
}

title "ПСБ · Юридический ассистент — запуск"

command -v docker >/dev/null 2>&1 || fail "Docker не найден. Установите Docker Desktop (https://www.docker.com/products/docker-desktop/), запустите его и повторите."
docker compose version >/dev/null 2>&1 || fail "Нужен Docker Compose v2 — он входит в Docker Desktop. Обновите Docker Desktop."
if ! docker_ready; then
  if [ "$(uname -s)" = "Darwin" ]; then
    say "Запускаю Docker Desktop…"
    open -a Docker >/dev/null 2>&1 || true
  fi
  say "Жду, пока Docker будет готов (до 3 минут)…"
  for _ in $(seq 90); do docker_ready && break; sleep 2; done
  docker_ready || fail "Docker не отвечает. Запустите Docker Desktop вручную, дождитесь зелёного статуса и повторите."
fi
memory="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
if [ "${memory:-0}" -gt 0 ] && [ "$memory" -lt $((MIN_DOCKER_MEMORY_GB * 1000 * 1000 * 1000)) ]; then
  warn "Docker выделено меньше ${MIN_DOCKER_MEMORY_GB} ГБ памяти — моделям поиска может не хватить. Увеличьте в Docker Desktop: Settings → Resources → Memory (рекомендуется 8 ГБ)."
fi

if [ ! -f data/kb.zip ]; then
  title "Нужна база знаний kb.zip из материалов кейса"
  while :; do
    read -r -p "Перетащите файл kb.zip в это окно (или введите путь к нему) и нажмите Enter: " src || fail "Путь к kb.zip не введён."
    src="$(clean_path "$src")"
    if [ -f "$src" ] && [ "$(head -c 2 "$src")" = "PK" ]; then break; fi
    say "Это не zip-архив или файл не найден: $src"
  done
  mkdir -p data
  cp "$src" data/kb.zip
  say "База скопирована в data/kb.zip"
fi

base_url="${LLM_BASE_URL:-$(env_get LLM_BASE_URL)}"
base_url="${base_url:-$DEFAULT_BASE_URL}"
base_url="${base_url%/}"
model="${LLM_MODEL:-$(env_get LLM_MODEL)}"
model="${model:-$DEFAULT_MODEL}"
saved_key="$(env_get LLM_API_KEY)"

title "API-ключ языковой модели ($base_url)"
while :; do
  if [ -n "$saved_key" ]; then
    read -r -s -p "Ключ уже сохранён (…${saved_key: -4}). Enter — оставить его, или вставьте новый: " key || fail "Ключ не введён."
  else
    read -r -s -p "Вставьте API-ключ (символы не отображаются) и нажмите Enter: " key || fail "Ключ не введён."
  fi
  echo
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  key="${key:-$saved_key}"
  if [ -z "$key" ]; then say "Ключ не введён."; continue; fi
  code="$(api_status "$base_url/models" "$key")"
  case "$code" in
    200) say "Ключ принят."; break ;;
    401|403) say "API отклонил ключ (HTTP $code). Проверьте его и вставьте снова."; saved_key="" ;;
    *) warn "Не удалось проверить ключ (HTTP ${code:-нет ответа}) — продолжаю без проверки."; break ;;
  esac
done
if [[ "$base_url" == *api.deepseek.com* ]]; then
  balance="$(printf 'Authorization: Bearer %s\n' "$key" | curl -s --max-time 15 -H @- "$base_url/user/balance" || true)"
  if printf '%s' "$balance" | grep -Eq '"is_available"[[:space:]]*:[[:space:]]*false'; then
    warn "На балансе DeepSeek нет средств — ассистент не сможет отвечать. Пополните баланс на platform.deepseek.com."
  fi
fi

docker compose down --remove-orphans >/dev/null 2>&1 || true
ui_port="$(free_port "${WEBUI_PORT:-3000}")"
api_port="$(free_port "${LEGAL_API_PORT:-8000}")"

env_set LLM_API_KEY "$key"
env_set LLM_BASE_URL "$base_url"
env_set LLM_MODEL "$model"
env_set WEBUI_PORT "$ui_port"
env_set LEGAL_API_PORT "$api_port"
env_set PUBLIC_BASE_URL "http://localhost:$api_port"
chmod 600 "$ENV_FILE"
export WITH_LOCAL_MODELS="${WITH_LOCAL_MODELS:-true}"

title "Собираю и запускаю контейнеры"
say "Первый запуск занимает 10–20 минут: скачиваются зависимости, модели поиска (~4,5 ГБ) и собирается интерфейс. Повторные запуски — около минуты."
for image in $(base_images); do
  docker image inspect "$image" >/dev/null 2>&1 || retry "Загрузка образа $image" docker pull -q "$image" \
    || fail "Не удалось скачать образ $image. Проверьте интернет и запустите скрипт ещё раз."
done
retry "Сборка ассистента" docker compose build legal-api \
  || fail "Не удалось собрать ассистента — причина в сообщениях выше. Если это ошибка сети, запустите скрипт ещё раз."
retry "Сборка интерфейса" docker compose build webui \
  || fail "Не удалось собрать интерфейс — причина в сообщениях выше. Если это ошибка сети, запустите скрипт ещё раз."
docker compose up -d || fail "Не удалось запустить контейнеры — причина в сообщениях выше."

title "Запускаю ассистента"
if ! wait_http "http://localhost:$api_port/health" "ассистента (при первом запуске строится индекс базы — несколько минут)" 1800 legal-api; then
  docker compose logs --tail 60 legal-api || true
  fail "Ассистент не запустился — последние строки журнала выше. Частая причина — мало памяти у Docker (Settings → Resources → Memory, нужно 8 ГБ)."
fi
if ! wait_http "http://localhost:$ui_port/health" "интерфейс" 600 webui; then
  docker compose logs --tail 60 webui || true
  fail "Интерфейс не запустился — последние строки журнала выше."
fi

url="http://localhost:$ui_port"
case "$(uname -s)" in
  Darwin) open "$url" >/dev/null 2>&1 || true ;;
  *) command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 || true ;;
esac

title "Готово"
say "Чат с ассистентом:      $url"
say "Документы базы знаний:  http://localhost:$api_port/kb"
say ""
say "Остановить:             docker compose down"
say "Запустить снова:        ./start.sh"
say "Журнал ассистента:      docker compose logs -f legal-api"
