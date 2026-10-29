#!/usr/bin/env bash
# 将 config/env/.env.local 同步到项目根 .env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/config/env/.env.local"
DST="${ROOT}/.env"
[[ -f "${SRC}" ]] || { echo "Missing ${SRC}" >&2; exit 1; }
cp -f "${SRC}" "${DST}"
echo "Synced ${SRC} -> ${DST}"
