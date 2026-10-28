#!/usr/bin/env bash
# Dev only (connected machine): download the Phase 1 embedder and reranker into ./models.
# Licences checked on the model cards on 2026-10-28: bge-m3 MIT, bge-reranker-v2-m3 Apache-2.0.
# The air-gapped path is deploy/build_bundle.sh (Phase 4).
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p models
# `hf` is the current Hugging Face CLI; older versions call it `huggingface-cli`.
HF="uvx --from huggingface_hub hf"
$HF download BAAI/bge-m3 --local-dir models/bge-m3 \
  --exclude "*.onnx" "onnx/*" "colbert_linear.pt" "sparse_linear.pt" "imgs/*"
$HF download BAAI/bge-reranker-v2-m3 --local-dir models/bge-reranker-v2-m3
echo "models ready under ./models (git-ignored)"
