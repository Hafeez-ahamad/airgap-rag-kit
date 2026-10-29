#!/usr/bin/env bash
# AirGap native profile - one entry point for Linux and macOS (no Docker required).
#   scripts/bootstrap.sh            check prerequisites, then start
#   scripts/bootstrap.sh --check    check only
#   scripts/bootstrap.sh --with-document-assistant   also install CPU PyTorch (~1 GB)
# Never installs system software or changes host settings; it tells you what is missing.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
check=0; extra=()
for a in "$@"; do
  case "$a" in
    --check) check=1 ;;
    --with-document-assistant) extra=(--extra models) ;;
  esac
done
missing=()
command -v uv >/dev/null || missing+=("uv: https://docs.astral.sh/uv/getting-started/installation/")
command -v ollama >/dev/null || missing+=("Ollama: https://ollama.com/download")
[[ -f "$root/frontend/dist/index.html" ]] || command -v npm >/dev/null || missing+=("Node.js 22+ (to build the UI once): https://nodejs.org/")
if (( ${#missing[@]} )); then
  echo "Install these first, then run this script again:"; printf '  - %s\n' "${missing[@]}"; exit 1
fi
cd "$root/backend"
uv sync --frozen "${extra[@]}"
[[ -f "$root/frontend/dist/index.html" ]] || (cd "$root/frontend" && npm ci --no-audit --no-fund && npm run build)
uv run airgap --data-dir data/app --models-dir ../models doctor || true
(( check )) && exit 0
echo "Starting on http://127.0.0.1:8000 (Ctrl+C to stop)."
exec uv run airgap --data-dir data/app --models-dir ../models serve
