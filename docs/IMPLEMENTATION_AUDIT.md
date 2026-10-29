# Implementation audit

_Audit date: 2026-10-29. Branch at audit time: `phase-1/core` @ `80b0a0c` (clean). Work after the
audit continues on `phase-2/hardware-adaptive`. Requirements: `master_prompt_on_prem_rag.md` (owner's
primary requirements document, 2026-10-29), plus `docs/BUILD_PROMPT.md` and `docs/spec/` where they
do not conflict with it._

Every statement below comes from reading the code, running a command, or a recorded earlier
session (`docs/PROGRESS.md`, `docs/reports/phase1.md`). Anything not checked says so.

## 1. Architecture as found

```
Browser (React/Vite UI) ──HTTPS──> nginx proxy (edge + backend networks)
                                      │ /api/*
                                      ▼
                        FastAPI api (airgap.api) ── Postgres (SQLAlchemy + Alembic)
                          │   │   └─ Valkey (Redis protocol): ingestion queue
                          │   └─ Qdrant: dense + BM25 sparse vectors, ACL payload filter
                          ├─ model service (FastAPI, sentence-transformers: bge-m3 + bge-reranker-v2-m3)
                          └─ Ollama (LLM, qwen3:4b-instruct-2507-q4_K_M)
              ingestion worker (same image) ── parse/OCR/chunk ─> model service ─> Qdrant
```

Data flow (grounded query): rewrite follow-up (LLM) → embed query → dense + sparse search, both
ACL-filtered in Qdrant → RRF fusion → rerank → **confidence gate in code** → delimited prompt →
stream → citation verification (exact normalised substring) → one retry → flag → persist + SSE.

Code quality: typed (mypy strict), ports-and-fakes design (`airgap/core/ports.py`,
`airgap/testing/fakes.py`), modules small and single-purpose. **Preserve it.** No framework
migration is justified.

## 2. Feature / status inventory (at audit time)

| Area | Status | Evidence / notes |
|---|---|---|
| Upload validation (magic bytes, size, zip bomb, path sanitisation) | Implemented, unit-tested | `ingestion/validate.py`, `tests/test_validate.py` |
| Parsers PDF/DOCX/TXT/MD/CSV/XLSX, OCR fallback | Implemented; real Tesseract test skipped locally | `ingestion/parsers/`, Tesseract not installed on this laptop |
| Structure-aware chunking, dedupe, versioning, delete | Implemented, tested on real Qdrant local mode | `tests/test_ingestion.py` |
| Hybrid retrieval + rerank + gate | Implemented, tested with fakes | gate threshold 0.30 **uncalibrated** |
| Citation verification + retry/flag | Implemented, tested | `retrieval/citations.py` |
| Grounded + General modes | Implemented | "General" mode still retrieves; there is **no pure General Assistant** (no-retrieval chat) as the master prompt requires |
| Conversation history, feedback | Implemented, tested | per-user isolation tested |
| Ollama adapter | Implemented, unit-tested against mock transport only | never run against a live model in the app |
| Model service (embed/rerank) | Implemented; ran once in Docker | measured 2.56 GiB RSS in bf16 (session 2) |
| Compose stack, nginx TLS, two networks | Written; images built | **full stack never passed** (memory) |
| React UI (chat, documents, history) | Implemented, manual check only | no onboarding, no setup wizard, no settings, no diagnostics, no Markdown rendering |
| Auth | **Stub only** (`X-Dev-User`, ADR-0008) | real auth, roles, ACL editing absent |
| Hardware probe, capacity planner, catalog, tiers | **Absent** | no `capacity/`, no `catalog/` |
| Model manager (download/verify/resume) | **Absent** | |
| Prerequisite manager / setup flow | **Absent** | |
| `airgap` CLI | **Broken reference** | `pyproject.toml` declares `airgap = "airgap.cli.main:main"`; module does not exist |
| Audit log, rate limits (app level), PII redaction | Absent | nginx has a coarse per-IP rate limit |
| Eval harness, bake-off, trust report | Absent | |
| Offline bundle, installer, backup/restore/upgrade | Absent | |
| No-egress proof | Partial | process-level socket guard (`offline.py`) + internal network; no runtime test |
| CI | Written, never run (no remote) | `.github/workflows/ci.yml` |

## 3. Test baseline (run 2026-10-29 on this laptop)

```
backend$ uv run ruff check .          -> All checks passed!
backend$ uv run ruff format --check . -> 64 files already formatted
backend$ uv run mypy                  -> Success: no issues found in 62 source files
backend$ uv run pytest -q             -> 75 passed, 1 skipped (tesseract binary not installed), 72.8 s
frontend$ npm run lint                -> clean
frontend$ npm run build               -> ok (234.95 kB JS)
```
No failures. The only skip is the real-OCR test. All 75 are unit / fake-adapter / embedded-Qdrant
tests; **none** exercises a real model.

## 4. Resource bottleneck: evidence

Host: Windows 11 Home, AMD Ryzen 7 4800H (8C/16T), **7.42 GB RAM**, RTX 3050 Laptop **4 GiB VRAM**
(driver 572.47), C: free **19.6 GB** (was 79 GB on 2026-10-28; Docker images and model weights
account for much of the drop; not itemised).

Observed now (read-only commands, 2026-10-29):
- `Win32_OperatingSystem`: FreePhysicalMemory **0.82 GB** of 7.42 GB with no stack running.
- `wsl -l -v`: `docker-desktop` VM **Running**.
- `docker version`: **HTTP 500** from `dockerDesktopLinuxEngine` (engine still wedged since session 2);
  `docker info` hung past 120 s, so the command was stopped. No containers could be listed.
- `%USERPROFILE%\.wslconfig`: **does not exist**, so WSL2 defaults apply. Session 2 measured the
  Docker VM at 3.5 GiB.
- Top Windows processes by working set: Memory Compression 964 MB, several Claude/Brave processes
  (≈150–470 MB each), vmmemWSL 206 MB (working set only; the VM's committed memory is larger).
- Native **Ollama 0.40.12 is installed on Windows** and serving on 127.0.0.1:11434, with
  `qwen2.5:7b` (Q4_K_M, 4,683,087,332 bytes) pulled. Its `/api/show` metadata: 28 blocks, 4 KV
  heads, 28 attention heads, embedding 3584 (head_dim 128), licence apache-2.0.

Recorded in session 2 (`docs/PROGRESS.md`): model service RSS 2.56 GiB (bf16, both models); VM had
38 MB free with swap full; Windows 370–720 MB free; the Docker engine then started returning HTTP
500, and the e2e script could not complete a TLS handshake.

**Conclusions**
1. The binding constraint is **total host RAM (7.4 GB) shared by Windows, the browser/IDE, the WSL2
   VM and eight always-on containers**, not one service. RAM is not the only problem: the engine
   failure (HTTP 500) persists after the load is gone, so the Docker Desktop VM is left in a bad
   state and needs a restart by the owner.
2. The compose stack's always-on set (Postgres, Qdrant, Valkey, nginx, api, worker, model service,
   Ollama) is the wrong shape for a constrained device. The master prompt asks for a lean profile
   with fewer services, so this is a product gap, not only a laptop problem.
3. Re-running `docker compose up` on this machine would repeat the known failure, so I did not
   retry it (master prompt §10.2).

## 5. Security / privacy risks found

| Risk | Severity | Note |
|---|---|---|
| No real authentication; dev stub trusts a header | High (dev only) | refused in production by settings validator |
| ACL groups cannot be edited; every user in `default` | Medium | retrieval filter is correct, but there is no way to restrict |
| No audit log | Medium | |
| No app-level rate/concurrency limit | Medium | only nginx per-IP limit in the compose profile |
| Ollama ≥0.40 reaches `ollama.com` (cloud, recommendations) | Medium | `OLLAMA_NO_CLOUD=1` set in compose; native install on this laptop is **not** configured |
| Health endpoint lists component names unauthenticated | Low | acceptable; no secrets |
| No no-egress runtime test | Medium | claim of "offline" is unproven |
| Model files loaded from `pytorch_model.bin` (pickle) for bge-m3 | Low–Medium | transformers loads with `weights_only=True`; prefer safetensors when available |

## 6. Proposed minimal changes (smallest path to one reliable profile)

1. **Add a lean "native" deployment profile** (no Docker, no WSL): single API process with an
   in-process ingestion worker, SQLite metadata, embedded Qdrant (local on-disk mode, already used by
   the tests), native Ollama, and in-process embedder/reranker loaded lazily. Same ports, same
   code paths, fewer services. Keep the compose profile for servers. → ADR-0009.
2. **Add `capacity/`**: hardware report model, real probes (Windows/Linux/macOS) with injectable
   command runners, simulated provider, catalogue loader, KV maths, planner with reason codes.
   The installer, CLI, setup API and Compatibility Lab all call one `assess()` function.
3. **Add a pure General Assistant mode** (no retrieval, no citation claims).
4. Add setup state, prerequisite checks, model manager (Ollama pull with progress; resumable,
   checksum-verified file downloads), and the `airgap` CLI the pyproject already references.
5. Real auth + roles + ACL editing + audit log + rate limits.
6. UI: onboarding wizard, workspace with sidebar, Markdown, settings/diagnostics, Compatibility Lab.
7. Bundle manifest/signature verification, backup/restore for the native profile, eval harness.

## 7. Decisions that need the owner

1. **Docker Desktop is wedged (HTTP 500).** Restarting it, or `wsl --shutdown`, changes host
   state, so I have not done it. The native profile does not need Docker.
2. **`.wslconfig` memory limit**: not edited (persistent host setting). Only relevant if you want the
   compose profile on this laptop; at 7.4 GB total it will stay marginal either way.
3. **SQLite outside unit tests.** BUILD_PROMPT §3 reserves SQLite for tests; the master prompt
   (primary) asks for a lean low-memory profile. I am using SQLite only in the native profile and
   recording that in ADR-0009. Say if you want this reversed.
4. **Local model for the real smoke test.** `qwen2.5:7b` (Apache-2.0, verified via Ollama
   metadata) is what is installed natively. The planned `qwen3:4b-instruct-2507` lives in the
   unreachable Docker volume. I will not pull another 2.5 GB without asking.
5. Merging `phase-1/core` to `main`: blocked by BUILD_PROMPT rule 7 until the Phase 1 live
   acceptance passes.
