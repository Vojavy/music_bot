#!/usr/bin/env bash
# build_push.sh — Build & push Docker image for Music Bot (“music-bot/” dir)

set -eu
if ( set -o 2>/dev/null | grep -q pipefail ); then
  set -o pipefail
fi
IFS=$'\n\t'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

###############################################################################
# 0. Функция обработки EXIT
###############################################################################
on_exit() {
  local rc=$?
  echo
  if [[ $rc -eq 0 ]]; then
    echo "✔ Скрипт завершён успешно."
  else
    echo "✖ Скрипт прерван. Код ошибки: $rc."
  fi

  # Если это dev и интерактивная сессия — ждём нажатия клавиши
  if [[ "$ENV" == "dev" && -t 1 ]]; then
    read -n1 -rsp $'\nНажмите любую клавишу для выхода…'
  fi
}
trap on_exit EXIT

###############################################################################
# 1. Defaults
###############################################################################
ENV="dev"                       # dev | prod
VERSION=""
IMAGE_NAME="music-tgbot"

REGISTRY_DEV="registry.local"
REGISTRY_PROD="registry.distrbyt.dev"

###############################################################################
# 2. Парсим аргументы
###############################################################################
usage() { grep '^#' "$0" | head -n 24 | cut -c3-; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)     ENV="${2:-}"; shift 2 ;;
    -v|--version) VERSION="${2:-}"; shift 2 ;;
    -n|--name)    IMAGE_NAME="${2:-}"; shift 2 ;;
    -h|--help)    usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done
[[ "$ENV" =~ ^(dev|prod)$ ]] || { echo "ENV must be dev or prod"; exit 1; }

###############################################################################
# 3. Читаем версию из __init__.py
###############################################################################
if [[ -z "$VERSION" ]]; then
  INIT_FILE="$SCRIPT_DIR/__init__.py"
  [[ -f "$INIT_FILE" ]] || { echo "File not found: $INIT_FILE"; exit 1; }
  VERSION=$(grep -oE "^__version__\s*=\s*['\"][^'\"]+['\"]" "$INIT_FILE" \
            | head -n1 | sed -E "s/^__version__\s*=\s*['\"]([^'\"]+)['\"]/\1/")
  [[ -n "$VERSION" ]] || { echo "__version__ not found in $INIT_FILE"; exit 1; }
fi
echo "Detected version: $VERSION"

###############################################################################
# 4. Docker login для prod
###############################################################################
if [[ "$ENV" == "prod" ]]; then
  : "${REGISTRY_USER:?Set REGISTRY_USER env var}"
  : "${REGISTRY_PASS:?Set REGISTRY_PASS env var}"
  echo "Logging in to $REGISTRY_PROD as $REGISTRY_USER"
  echo "$REGISTRY_PASS" | docker login "$REGISTRY_PROD" \
                         -u "$REGISTRY_USER" --password-stdin
fi

###############################################################################
# 5. Определяем теги
###############################################################################
if [[ "$ENV" == "dev" ]]; then
  REGISTRY="$REGISTRY_DEV";  TAG_MAIN="$VERSION"; TAG_EXTRA="dev"
else
  REGISTRY="$REGISTRY_PROD"; TAG_MAIN="$VERSION"; TAG_EXTRA="latest"
fi
FULL_MAIN="${REGISTRY}/${IMAGE_NAME}:${TAG_MAIN}"
FULL_EXTRA="${REGISTRY}/${IMAGE_NAME}:${TAG_EXTRA}"

echo "Environment : $ENV"
echo "Version     : $VERSION"
echo "Image tags  : $FULL_MAIN (+ $TAG_EXTRA)"

###############################################################################
# 6. Build & Push
###############################################################################
docker build --pull -t "$FULL_MAIN" .

if [[ "$TAG_EXTRA" != "$TAG_MAIN" ]]; then
  docker tag "$FULL_MAIN" "$FULL_EXTRA"
fi

docker push "$FULL_MAIN"
if [[ "$TAG_EXTRA" != "$TAG_MAIN" ]]; then
  docker push "$FULL_EXTRA"
fi

echo "✔ Pushed: $FULL_MAIN and $FULL_EXTRA"
