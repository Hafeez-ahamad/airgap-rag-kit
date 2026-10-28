# Phase 1 report: Core (interim)

_Status 2026-10-28: **code complete, acceptance partly verified.** Phase 1 is not closed. The
`docker compose up` criterion has not been run, because this machine has no Docker or WSL (see
Gaps). Branch: `phase-1/core`, not merged to `main`._

## What was built
| Area | Where |
|---|---|
| Ports and fakes (LLM, embedder, reranker, vector store, OCR, blob store, queue, clock) | `backend/airgap/core/ports.py`, `backend/airgap/testing/fakes.py` |
| Upload checks: content-sniffed allow-list, extension must match content, size limit, zip-bomb guard, filename and path sanitisation | `backend/airgap/ingestion/validate.py` |
| Parsers: PDF (pypdfium2, OCR fallback per image-only page), DOCX (heading styles, tables), TXT, MD, CSV, XLSX | `backend/airgap/ingestion/parsers/` |
| Tesseract adapter (CLI, no shell) | `backend/airgap/ingestion/ocr.py` |
| Structure-aware chunker (section path, page-bounded chunks, table chunks with repeated header, overlap) | `backend/airgap/ingestion/chunker.py` |
| Dedupe and versioning, delete across all versions and the index, status state machine | `backend/airgap/ingestion/documents.py`, `backend/airgap/ingestion/pipeline.py` |
| Hybrid retrieval: dense + BM25 sparse in Qdrant, ACL filter on both, RRF, reranker, confidence gate | `backend/airgap/retrieval/` |
| Citation verification (`[n: "quote"]`, exact match after normalisation) | `backend/airgap/retrieval/citations.py` |
| Query flow: rewrite → retrieve → gate → delimited prompt → stream → verify → one retry → flag; Grounded and General modes; coding-mode hook | `backend/airgap/chat/` |
| Persistence and Alembic migration (documents, versions, conversations, messages, feedback) | `backend/airgap/db/`, `backend/migrations/` |
| API: `POST/GET/DELETE /documents`, `GET /documents/{id}/file`, `POST /chat` (SSE), `GET /chat/history`, `POST /feedback`, `GET /health` | `backend/airgap/api/` |
| Ollama adapter, model-service clients, file blob store, Redis queue | `backend/airgap/llm_backends/`, `backend/airgap/adapters/` |
| Embedding and reranker service (local paths, offline variables enforced) | `backend/airgap/model_service/app.py` |
| Process-level egress guard (blocks connects to non-private IPs) | `backend/airgap/offline.py` |
| Compose: backend (internal) and edge networks, health checks, `restart: unless-stopped`, read-only app containers, `cap_drop` | `deploy/docker-compose.yml` |
| Nginx: TLS, body limits, rate limit, CSP, SSE pass-through | `frontend/nginx.conf` |
| React UI: chat with mode selector and streaming, citation viewer with open-source link, upload of files and folders with per-file status, feedback, history, degraded banner | `frontend/src/` |
| CI: lint, format, mypy, pytest, UI lint and build, compose network-policy check, image build, gitleaks | `.github/workflows/ci.yml` |

## Acceptance criteria
Commands were run from `backend/` on 2026-10-28 (Windows 11, Python 3.12.16, uv 0.13.6).

| Criterion | Result | Evidence |
|---|---|---|
| `docker compose up` from a clean clone | **NOT RUN, blocked** | Docker and WSL are not installed. Enabling WSL needs admin rights and a reboot. |
| PDF, DOCX, scanned PDF (OCR), CSV, XLSX and folder upload all reach `ready` | **PASS with fakes** | `tests/test_ingestion.py::test_every_supported_format_reaches_ready`. OCR goes through `FakeOCR`. The real Tesseract test is **skipped**, because Tesseract is not installed here; CI installs it. |
| Oversized file and disallowed type rejected with clear errors | **PASS** | `test_validate.py`, `test_ingestion.py::test_rejections_do_not_block_other_files`, `test_api.py::test_upload_folder_reports_per_file_outcomes` |
| Answerable question gets a cited answer with page or section; unanswerable question is refused (fake LLM, fixture corpus) | **PASS** | `test_orchestrator.py::test_answerable_question_gets_cited_answer_with_page_and_section`, `::test_unanswerable_question_is_refused_without_calling_the_model`, `test_api.py::test_chat_streams_cited_answer_then_history_and_feedback` |
| Unchanged re-upload is a no-op; a changed file replaces the old version in dense **and** sparse search | **PASS** | `test_ingestion.py::test_unchanged_reupload_is_a_noop_and_duplicate_content_is_detected`, `::test_changed_file_replaces_old_version_in_dense_and_sparse` (real QdrantStore on qdrant-client local mode) |
| An invented quote is caught | **PASS** | `test_retrieval_units.py::test_invented_quote_is_caught`, `test_orchestrator.py::test_invented_quote_is_retried_once_then_flagged` |
| Follow-up "and what about the second one?" is rewritten and answered | **PASS (plumbing)** | `test_orchestrator.py::test_follow_up_question_is_rewritten_and_answered`. The rewrite comes from a scripted fake, so this proves the rewritten query drives retrieval. It does not show that a real model rewrites well. |

Tool output:
```
uv run ruff check .          -> All checks passed!
uv run ruff format --check . -> 64 files already formatted
uv run mypy                  -> Success: no issues found in 62 source files
uv run pytest -q             -> 75 passed, 1 skipped (tesseract binary not installed)
npm run lint                 -> no problems
npm run build                -> tsc ok; vite build ok (234.95 kB JS, 73.48 kB gzip)
```
UI smoke test: I ran the real API wired to the fakes and the Vite dev server, then drove the UI in a browser. Upload with one rejection, a cited answer with document and section, a refusal, the citation viewer and the document list all behaved correctly. This was manual, not automated; the Playwright smoke test comes later.

## Known gaps and things not verified
- **No real model has run.** Nothing here measures answer quality, latency or memory, and no
  numbers are claimed.
- The Docker images, compose stack, nginx config and Postgres migration have not been executed.
  The Alembic migration was tested on SQLite only.
- `sentence-transformers` `local_files_only` on `SentenceTransformer`/`CrossEncoder` is marked
  `TODO(verify)` against the pinned version (6.2.0).
- The confidence threshold of 0.30 is **uncalibrated** (ADR-0006).
- Base images are pinned by tag, not yet by digest (Phase 4).
- CI has not run yet: the repo has not been pushed to GitHub.
- The UI uses Vite 6 and ESLint 9 because local Node is 22.11. Vite 8 and ESLint 10 need
  Node 22.12 or later.
- Auth is the dev stub (ADR-0008). With the stub off, every route except `/health` returns 401,
  as tested.

## Risks for the next phase
- 7.4 GB RAM may not run Qdrant + Postgres + Valkey + the model service (bge-m3 + reranker on CPU)
  + Ollama (4B Q4) at the same time. If so, the embedder or reranker may need to move to fp16 or
  share a GPU.
- Phase 2 needs 2+ real machines and Phase 2b needs a 32 GB-class GPU. Only a 4 GB laptop GPU
  is available.
