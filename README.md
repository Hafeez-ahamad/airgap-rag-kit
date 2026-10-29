# AirGap RAG Kit

A private assistant that runs on your own hardware. It checks the device, recommends local
models that fit, and offers a **General Assistant** (local LLM chat) and a **Document Assistant**
that answers only from your documents, with verified citations, or says it cannot find the answer.

> Status: pre-release. See [docs/STATUS.md](docs/STATUS.md) and
> [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) for what is verified, simulated or not yet
> validated. Hardware results: [docs/hardware-profiles.md](docs/hardware-profiles.md).

## Quick start (native profile: no Docker, no WSL)
Install [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com/download) and Node.js 22+, then:
```bash
scripts/bootstrap.sh --with-document-assistant
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -WithDocumentAssistant
```
Open http://127.0.0.1:8000, create the admin account and follow setup. Details, the compose
profile, offline bundles, backup and troubleshooting: [docs/runbook.md](docs/runbook.md).

## What it does
- **Device scan (read-only, after you approve it):** OS, CPU and AVX2, RAM total and free, NVIDIA
  VRAM or Apple unified memory, free disk, Ollama/Docker/WSL status.
- **Resource planner:** weights + KV cache for the context + runtime overhead + retrieval models +
  app services + headroom, against free RAM/VRAM and disk. Every model is listed with a reason
  ("needs about 9.3 GiB ... 3.2 GiB VRAM available", "licence not permitted"). Estimates are
  labelled as estimates; setup finishes only after a **real inference test**.
- **Compatibility Lab (admin):** the same planner on synthetic devices, clearly marked
  *SIMULATION — NOT A REAL HARDWARE SCAN*; it cannot install or run anything.
- **Setup:** prerequisites with actionable steps, dry-run plan, resumable verified downloads,
  cancel/retry, state that survives restarts.
- **Document Assistant:** upload PDF/DOCX/TXT/MD/CSV/XLSX (OCR for scans when Tesseract is
  installed), hybrid dense+BM25 retrieval, reranking, a code-decided confidence gate, quotes
  verified against the source, per-group access enforced inside the search query.
- **Security:** local accounts (Argon2), roles, CSRF, lockout, append-only audit log, rate limits,
  redacted diagnostics, signed offline bundles, backups with checksums.

## Development
```bash
cd backend && uv sync && uv run pytest -q && uv run ruff check . && uv run mypy
cd frontend && npm ci && npm test && npm run lint && npm run build
uv run --directory backend airgap recommend --profile cpu-only-16   # simulated planner run
```
Validation levels used throughout: L1 static, L2 unit, L3 simulated profile, L4 mock adapter,
L5 lightweight real service, L6 full-stack acceptance, L7 physical target device.
Validate on a bigger machine: [docs/VALIDATION.md](docs/VALIDATION.md).

Licence: Apache-2.0 (see LICENSE and NOTICE). Model licences are recorded in the catalogue
(`backend/airgap/capacity/data/catalog.yaml`); non-permissive licences are excluded by default.
