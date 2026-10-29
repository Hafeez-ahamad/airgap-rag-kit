#!/usr/bin/env bash
# Dev only (connected machine): download the Phase 1 embedder and reranker into ./models.
# Licences checked on the model cards on 2026-10-28: bge-m3 MIT, bge-reranker-v2-m3 Apache-2.0.
# The air-gapped path is deploy/build_bundle.sh (Phase 4).
#
# Files are listed explicitly: `hf download --exclude` with several patterns did not filter as
# expected (it fetched only the excluded ONNX/ColBERT files on 2026-10-29).
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p models
# `hf` is the current Hugging Face CLI; older versions call it `huggingface-cli`.
HF="uvx --from huggingface_hub hf"

# bge-m3 ships weights only as pytorch_model.bin (a pickle); transformers loads it with
# torch weights_only=True. README.md carries the licence metadata.
$HF download BAAI/bge-m3 \
  README.md config.json config_sentence_transformers.json modules.json \
  sentence_bert_config.json 1_Pooling/config.json pytorch_model.bin \
  sentencepiece.bpe.model special_tokens_map.json tokenizer.json tokenizer_config.json \
  --local-dir models/bge-m3

$HF download BAAI/bge-reranker-v2-m3 \
  README.md config.json model.safetensors sentencepiece.bpe.model \
  special_tokens_map.json tokenizer.json tokenizer_config.json \
  --local-dir models/bge-reranker-v2-m3

echo "models ready under ./models (git-ignored)"
