#!/usr/bin/env bash
# Build an offline bundle on a CONNECTED machine (REQUIREMENTS §4.1, spec §10).
#
#   deploy/build_bundle.sh --profile lite --out /media/usb/airgap-bundle --key ~/airgap-signing.pem
#
# Contents: container images (compose profile), the profile's LLM(s) exported from Ollama,
# platform retrieval models (SHA-256 pinned in the catalogue), licence files, the Python lockfile,
# the built UI, and MANIFEST.json (+ Ed25519 signature). The private key never enters the bundle.
#
# Status: written, NOT YET RUN end to end (Docker engine unavailable on the dev laptop).
# Profiles map to catalogue model ids below; edit them only together with catalog.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="lite"; OUT=""; KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done
[[ -z "$OUT" ]] && { echo "--out is required"; exit 2; }

case "$PROFILE" in
  lite)     MODELS=("qwen3:4b-instruct-2507-q4_K_M") ;;
  standard) MODELS=("qwen3:4b-instruct-2507-q4_K_M" "qwen3:8b-q4_K_M") ;;
  large)    MODELS=("qwen3:8b-q4_K_M" "qwen3:14b-q4_K_M") ;;
  xl)       MODELS=("qwen3:14b-q4_K_M" "qwen3:32b-q4_K_M") ;;
  *) echo "unknown profile $PROFILE"; exit 2 ;;
esac

mkdir -p "$OUT"/{images,models/ollama,models/retrieval,licences,app}

echo "== retrieval models (SHA-256 verified by the downloader)"
( cd backend && uv run python - "$OUT/models/retrieval" <<'EOF'
import sys
from pathlib import Path
from airgap.capacity.catalog import load_catalog
from airgap.models_manager.downloads import Downloader
from airgap.setup.installer import retrieval_files
dest = Path(sys.argv[1])
dl = Downloader()
for spec in retrieval_files(dest, load_catalog()):
    print(dl.fetch(spec), spec.dest.relative_to(dest))
EOF
)

echo "== LLMs via Ollama (pull, then copy the model store entries)"
for tag in "${MODELS[@]}"; do ollama pull "$tag"; done
OLLAMA_HOME="${OLLAMA_MODELS:-$HOME/.ollama/models}"
cp -r "$OLLAMA_HOME/manifests" "$OLLAMA_HOME/blobs" "$OUT/models/ollama/"
echo "   note: this copies the whole local Ollama store; prune it to the profile's models first"

if command -v docker >/dev/null; then
  echo "== container images (compose profile)"
  docker compose -f deploy/docker-compose.yml build api worker models proxy
  for img in airgap/api:dev airgap/worker:dev airgap/models:dev airgap/proxy:dev \
             ollama/ollama:0.40.12 qdrant/qdrant:v1.20.0 postgres:17.11-bookworm valkey/valkey:8.1.11-alpine; do
    docker save "$img" -o "$OUT/images/$(echo "$img" | tr '/:' '__').tar"
  done
else
  echo "== docker not available: bundle supports the NATIVE profile only"
fi

echo "== app, lockfiles, licences"
( cd frontend && npm ci --no-audit --no-fund && npm run build )
cp -r frontend/dist "$OUT/app/ui"
cp backend/uv.lock backend/pyproject.toml "$OUT/app/"
cp LICENSE NOTICE "$OUT/licences/"
for d in "$OUT"/models/retrieval/*/; do cp "$d/README.md" "$OUT/licences/$(basename "$d")-README.md" 2>/dev/null || true; done

echo "== manifest"
( cd backend && uv run airgap bundle manifest "$OUT" --profile "$PROFILE" ${KEY:+--key "$KEY"} )
du -sh "$OUT"
echo "Bundle ready. Verify on the target with: airgap bundle verify <dir> --public-key <pub.pem>"
