#!/usr/bin/env bash
# アプリ（NiceGUI + FastAPI + viser）の Windows 版をビルドする
#   ./windows/build.sh exe        -> dist/robot-viser.exe（backend + frontend）と dist/robot-viser-backend.exe（backend のみ）の単体 exe
#   ./windows/build.sh installer  -> dist/robot-viser-setup.exe（backend + frontend のインストーラ）
set -euo pipefail

MODE="${1:-}"
if [[ "$MODE" != "exe" && "$MODE" != "installer" ]]; then
  echo "usage: $0 {exe|installer}" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=robot-viser-win-builder

docker build -t "$IMAGE" -f "$ROOT/windows/Dockerfile" "$ROOT"

mkdir -p "$ROOT/dist"
docker run --rm \
  -v "$ROOT:/src:ro" \
  -v "$ROOT/dist:/out" \
  -e MODE="$MODE" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$IMAGE" bash /src/windows/build-in-container.sh

echo "done: $(ls "$ROOT"/dist/*.exe)"
