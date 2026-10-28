# PROGRESS

_Last updated: 2026-10-28 (session 1)_

## Current state
- **Phase:** 0, pre-Phase 1. Waiting for approval of the plan below (BUILD_PROMPT §12).
- **Done:** read the spec and the build prompt end to end. Set up the repo skeleton from BUILD_PROMPT "How to use": `docs/spec/AirGap_RAG_Kit_Spec.md`, `docs/BUILD_PROMPT.md`, `CLAUDE.md`. Checked the Phase 1 model candidates against their official model cards. Probed the dev machine.
- **Not done:** no code. No git repo yet, because `git` is not installed on this machine.
- **Exact next step:** get answers to the blocking questions (section 6) → install the dev prerequisites → start Phase 1 on branch `phase-1/core`.

---

## 1. Ambiguities and contradictions in the spec (and spec vs build prompt)

| # | Where | Issue | Proposed resolution |
|---|---|---|---|
| A1 | Spec §13 vs BUILD_PROMPT §8 | The spec puts hybrid retrieval and the reranker in **Phase 3**. The build prompt puts them in **Phase 1** (task 3). | Follow the build prompt (Phase 1). **Needs your confirmation.** |
| A2 | Spec §13 Phase 1 | "three hardware profiles on machines you have" is listed under Phase 1, but probing and profiles are Phase 2 work. | Drop it from Phase 1. Phase 1 uses one hand-written dev profile. **Confirm.** |
| A3 | Spec F21, §8, §10 | "Compose network marked internal": a single internal network would make the UI unreachable from the LAN. | Use the two-network design from BUILD_PROMPT §3.1. |
| A4 | Spec §6 vs BUILD_PROMPT §3 | The spec says "SQLite for dev"; the build prompt says SQLite is for unit tests only. | Follow the build prompt: Postgres in compose, SQLite only in unit tests. |
| A5 | Phase 1 vs rule 6 | Phase 1 needs per-user history and feedback, but auth only arrives in Phase 3, while rule 6 requires auth on every route. | Phase 1 identifies the user with a **dev-only** stub (`AIRGAP_DEV_AUTH=1`, fixed users). Startup fails if the stub is enabled while `AIRGAP_ENV=production`. Phase 3 replaces it with real auth. **Confirm.** |
| A6 | Spec F5/§7 "quoted span exists" | The spec doesn't define how the model emits quotes. Small models often paraphrase. | The prompt requires `[n]` markers plus a verbatim quote per claim. The verifier normalises Unicode, whitespace and case, then requires an exact substring match in chunk `n`. No fuzzy matching, so the check stays strict. If verification fails, retry once, then flag. ADR to follow. |
| A7 | Spec §7 General mode | "Answers that use no retrieved evidence" carry a label. It is unclear whether this applies per answer or per sentence. | Per sentence: any sentence without a verified citation gets the "general knowledge" label. |
| A8 | Spec F6, §14 "stricter on small tiers" | The confidence gate threshold depends on the reranker's score scale. BGE and Qwen3 rerankers produce different scales. | Calibrate the threshold per reranker model on the eval set, store it in the profile, and add a tier offset. Phase 1 uses a provisional value marked `uncalibrated`. |
| A9 | Spec §6 keyword search | bge-m3 can produce its own learned sparse vectors, but the spec says BM25. | BM25 through Qdrant sparse vectors with the IDF modifier, as specified. bge-m3 sparse vectors become a Phase 4 ablation. |
| A10 | Spec §10 bundle | "Python wheels / lockfile": the CLI runs inside the API image (BUILD_PROMPT §6), so the host needs no Python. | Ship the lockfile for audit only, and leave wheels out unless you want a host-side CLI. **Confirm.** |
| A11 | Spec §4.7 | The gpt-oss-20b memory figures come from a secondary blog (IntuitionLabs). | Treat them as unverified. They get measured or are marked "not yet measured". |
| A12 | Spec §4.4 / BUILD_PROMPT §4.4 | The T1 row names "Gemma 4 12B". The official card names it **"Gemma 4 12B Unified"**. Third-party sources disagree on whether it exists (4 vs 5 variants). | Use the official card's naming (section 2). The card page itself says it is deprecated and points to gemma.ai, so re-check there before cataloguing. |
| A13 | Spec F1 folder upload | Folder structure semantics are undefined. | Store the relative path as metadata only. It has no ACL meaning in v1. |
| A14 | — | The kit's own licence is not chosen (BUILD_PROMPT §10: ask). | **Your decision.** Suggest Apache-2.0. |

## 2. Verified Phase 1 model details (official model cards, checked 2026-10-28)

| Role | Model | Size | Context | Architecture / attention | Licence (card metadata) |
|---|---|---|---|---|---|
| Embedding | `BAAI/bge-m3` | params not stated on card (XLM-R-large class; to be read from config) | 8192 tokens | 1024-dim; dense + sparse + multi-vector; "100+ languages" (Hindi not named explicitly, must measure) | MIT |
| Embedding (alt) | `Qwen/Qwen3-Embedding-0.6B` | 0.6B, 28 layers, BF16 | 32K | dims 32–1024 (MRL); 100+ languages; vendor MMTEB mean (task) 64.33 (vendor claim) | Apache-2.0 |
| Reranker | `BAAI/bge-reranker-v2-m3` | 0.6B, F32 weights (~2.3 GB on disk, my estimate from param count, not measured) | examples use max_length 512 | cross-encoder on bge-m3 base; multilingual | Apache-2.0 |
| Reranker (alt) | `Qwen/Qwen3-Reranker-0.6B` | 0.6B, BF16 | 32K | causal LM, scored by yes/no logits (not a classic cross-encoder) | Apache-2.0 |
| Generation (dev) | `Qwen/Qwen3-4B-Instruct-2507` | 4.0B (3.6B non-embedding); 36 layers; 32 Q heads / **8 KV heads**; head_dim 128 | 262,144 native | dense, full attention (GQA), `use_sliding_window: false`; non-thinking | Apache-2.0 |
| Generation (dev alt) | Gemma 4 E2B / E4B | 2.3B / 4.5B effective (5.1B / 8B with embeddings) | 128K | interleaved sliding-window (512) + global; KV formula will **overestimate**, so mark it `kv_estimate: approximate` | Apache 2.0 (per ai.google.dev card) |

Ollama artifact for the dev model: `qwen3:4b-instruct-2507-q4_K_M`, listed at **2.5 GB** (q8_0 4.3 GB, fp16 8.1 GB).

KV maths for Qwen3-4B (spec formula, prediction only): 2 × 36 × 8 × 128 × 2 B (f16) = **147,456 B/token (144 KiB)**. That is 576 MiB at 4K context and 1.125 GiB at 8K. With `OLLAMA_KV_CACHE_TYPE=q8_0` it is about half. On the dev GPU (4 GiB), 2.5 GB weights + 0.56 GiB KV at 4K + runtime overhead is predicted to be **marginal**. Not yet measured.

Caveat: I checked licences from card **metadata**. BUILD_PROMPT §4.5 requires reading the actual LICENSE text; I will do that when each catalog entry is written. I have not checked any vLLM flags yet (Phase 2); they stay `TODO(verify)`.

Ollama settings checked in the official FAQ (docs.ollama.com/faq, 2026-10-28): `OLLAMA_NUM_PARALLEL` (default 1; memory scales with parallel × context), `OLLAMA_MAX_LOADED_MODELS` (default 3 × GPUs, or 3 on CPU), `OLLAMA_KEEP_ALIVE` (default 5 min; the API `keep_alive` parameter overrides it), `OLLAMA_KV_CACHE_TYPE` (`f16` | `q8_0` | `q4_0`; global; needs Flash Attention), `OLLAMA_CONTEXT_LENGTH` (doc says default 4096) and per-request `num_ctx`. The Ollama version gets pinned when first tested.

**Phase 1 recommendation:** bge-m3 + bge-reranker-v2-m3 + Qwen3-4B-Instruct-2507 Q4_K_M on Ollama. The alternatives join the bake-off.

## 3. Bake-off design (`eval/model_bakeoff.py`)
- **Inputs:** catalog models present locally × a fixed eval set (`eval/datasets/`: answerable questions with gold chunk ids, unanswerable questions, an injection set, a Hindi subset). The retrieval stack is held fixed so only the generator varies. Run with temperature 0 and a fixed seed, at the same context length for every model, and record the machine's hardware report.
- **Metrics per model:**
  - G: grounded-answer rate (programmatic citation verification plus human or calibrated-judge labels on a subset)
  - C: citation correctness
  - R: refusal F1 on unanswerable questions (the gate decides refusal, but the bake-off also scores whether the model invents unsupported content in General mode)
  - I: injection resistance (1 − compliance rate)
  - H: Hindi subset grounded rate
  - P: tokens/s and TTFT
- **Score:** `0.30·G + 0.20·C + 0.15·R + 0.15·I + 0.10·H + 0.10·P_norm`, where P_norm is min-max scaled within the run and clipped. Hard floors: I ≥ 0.9 and C ≥ 0.8, or the model is ineligible. The weights go in an ADR and are provisional until you approve them.
- **Output:** `eval/results/bakeoff-<date>-<host>.csv` plus a JSON summary. A model's `quality_rank` is written only from a run that included it; otherwise it is `null` and labelled "unranked", and the fallback rule applies (the largest permitted model that fits).
- **Uncertainty:** report n per metric and a bootstrap 95% CI. Ranks whose CIs overlap are reported as ties.

## 4. Test hardware
Probed on this machine:
- **CPU:** AMD Ryzen 7 4800H, 8 cores / 16 threads
- **RAM:** **7.4 GB**
- **GPU:** NVIDIA RTX 3050 Laptop, **4 GB VRAM**, compute capability 8.6, driver 572.47
- **Disk:** 79 GB free on C:
- **OS and tools:** Windows 11 Home. Docker, git, uv and Ollama are **not installed**; WSL, Python 3.13 and Node are present.

Tier coverage: this machine is **below T1** (8–12 GB). It is effectively T0 with a small GPU for partial offload. It covers none of T1–T4, and it cannot meet Phase 2 (2+ machines), Phase 2b (32 GB-class GPU) or the vLLM path (4 GB is too small for useful vLLM serving).

Concern: the full Phase 1 stack (Qdrant, Postgres, Redis, Ollama, embedder, reranker, API, worker, Nginx) in 7.4 GB of RAM under WSL2 will be very tight. Mitigations: run embedder and reranker in one service at fp16 on CPU, use a small Qdrant and Postgres footprint, and use a compose profile that skips Prometheus and Grafana on dev.

**Question for you:** what other machines (GPU model / VRAM / RAM / OS) can you provide, and when?

## 5. Phase 1 plan (BUILD_PROMPT §7.1)
**Prerequisites (your approval needed, these install software):** git, Docker Desktop with the WSL2 backend (plus NVIDIA GPU support in WSL), uv, and Python 3.12 via uv. Ollama runs as a container, not on the host.

**Tasks, in order:**
1. **Scaffold:** `pyproject.toml` (uv, Python 3.12), ruff, mypy strict (ADR-0001), pytest markers (`gpu`, `model`, `docker`, `slow`), `.gitignore`, `.env.example`, `NOTICE`, `CHANGELOG.md`, a GitHub Actions lint/type/test job, and gitleaks.
2. **Ports and fakes:** `LLMBackend`, `Embedder`, `Reranker`, `VectorStore`, `OCR`, `Clock`, each with an in-memory fake.
3. **Ingestion:** content-sniffing allow-list, size limit, filename sanitisation, parsers (PDF via PyMuPDF, DOCX, TXT, MD, CSV, XLSX), detection of image-only pages plus Tesseract OCR, a structure-aware chunker, SHA-256 dedupe and versioning, delete, a status state machine, and a Redis-queue worker running as a non-root user.
4. **Retrieval:** Qdrant dense + BM25 sparse, RRF fusion, reranker, and a confidence gate. The ACL filter hook is present now (default group only) and tested in both dense and sparse search.
5. **Query flow:** follow-up rewrite, retrieve, gate, delimited prompt, generate, citation verification (one retry, then flag), and SSE streaming. Grounded and General modes, plus the `coding` enum value and routing hook only.
6. **Persistence:** SQLAlchemy and Alembic models for documents, versions, conversations, messages and feedback, with history isolated per user.
7. **API:** `POST/GET/DELETE /documents`, `POST /chat`, `GET /chat/history`, `POST /feedback`, `GET /health`, all in OpenAPI.
8. **Model service:** a FastAPI embed + rerank service that loads local paths only, with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` and a startup hub-access check.
9. **Compose:** two networks (`backend` internal, `edge`), health checks and `restart: unless-stopped` on every service, Nginx with a self-signed TLS certificate and body-size limits.
10. **UI:** React + Vite. Chat with a mode selector and streaming, a citation viewer, upload (files and folders) with status, feedback buttons, and history. All assets local; no CDN.
11. **Phase report:** `docs/reports/phase1.md`.

**Tests written first:** chunker (headings, tables, overlap); dedupe and versioning (unchanged → no-op; changed → old chunks gone from dense **and** sparse); confidence gate; citation verifier (an invented quote is caught); rewrite of "and what about the second one?"; upload rejection (oversize, wrong magic bytes, path traversal in filenames); per-user history isolation; and an end-to-end grounded answer and refusal with the fake LLM over a fixture corpus (fixtures include a generated scanned PDF for OCR).

**Risks:**
- 7.4 GB RAM may not hold the stack together with a 4B model.
- No Docker on this machine yet.
- The Windows and WSL2 file and GPU passthrough may be unreliable.
- The confidence threshold stays uncalibrated until the Phase 3 eval set exists.
- OCR quality on Indic scans has not been measured.

## 6. Decisions waiting on you (blocking)
1. A1, A2, A5, A10: confirm the proposed resolutions.
2. A14: the kit's licence (suggest Apache-2.0).
3. Approval to install git, Docker Desktop (WSL2), and uv on this machine.
4. Which other test machines are available (needed for Phase 2 and 2b; otherwise those items become `blocked: hardware`).
5. The Phase 1 model choice (section 2) and the provisional bake-off weights (section 3).

## Blockers
- `git` is missing, so there is no repo and no commits yet.
- Docker is missing, so the Phase 1 acceptance criterion `docker compose up` can't be run.
