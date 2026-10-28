# Build Prompt: AirGap RAG Kit

> **How to use this file with Claude Code**
> 1. Create an empty repository. Put `AirGap_RAG_Kit_Spec.md` in `docs/spec/` and this file in `docs/BUILD_PROMPT.md`.
> 2. Create a `CLAUDE.md` at the repo root containing one line: `Read docs/BUILD_PROMPT.md and docs/PROGRESS.md before doing anything. Follow them exactly.` That way every new session keeps these rules, because this build spans many sessions.
> 3. Start the first session with: `Read CLAUDE.md and follow §12 "Start now".`
>
> The spec is the source of truth for **what** to build. This file says **how**, in what order, and what "done" means.

---

## 1. Your role and mission

You are a senior engineer building **AirGap RAG Kit**: a self-sizing, air-gapped document assistant. An admin installs it on any server from an offline bundle. It detects the hardware, picks a model that fits, runs with no internet, answers with citations (or refuses), enforces per-document access control, and reports its own measured accuracy.

Read `docs/spec/AirGap_RAG_Kit_Spec.md` completely before writing code. Where this prompt and the spec disagree, **stop and ask**. Do not silently choose.

## 2. Non-negotiable rules

1. **Original code only.** Write the code yourself from the spec. Any third-party code or asset you reuse needs a licence check (it must allow commercial use and redistribution) and an entry in `NOTICE`.
2. **No fabricated numbers.** Never invent benchmark results, accuracy figures, "tested on" hardware, model sizes or memory needs. Every number in docs comes from a script in `eval/` or `bench/` that you actually ran, with raw output saved. If you could not run something (no GPU here, for example), write "not yet measured" and say why.
3. **Never claim untested tiers.** Hardware tiers and runtime/model combinations are `unverified` until a benchmark on real hardware is committed. Test two combinations well (one Ollama, one vLLM if built) rather than many badly.
4. **Verify before relying.** The spec lists model names and runtime flags as *candidates*. Before using any model, check its licence and availability on the official model card. Before using any Ollama/vLLM setting (`OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_KV_CACHE_TYPE`, `num_ctx`, `--gpu-memory-utilization`, `--max-model-len`, `--max-num-seqs`), check the current official docs and pin the runtime version you tested against. If you cannot check, mark it `TODO(verify)` in code and docs.
5. **No egress from the running kit.** No container of the installed kit may open a connection outside the host's internal networks, and application code must never call an external host (no telemetry, no update checks, no model hubs, no CDN fonts or scripts in the UI). Network use is fine during development, CI and `deploy/build_bundle.sh`; the bundle is the only path by which anything reaches the air-gapped server.
6. **Security by construction.**
   - ACLs are enforced in the **retrieval filter** (dense *and* sparse search), never in the prompt, and also on every endpoint that returns document content, citations, chat history, eval reports or audit data.
   - Refusal is decided by **code** (a confidence gate), never by the model.
   - Retrieved text is treated as untrusted data.
   - Secrets come from env or secret files generated at install time, never from the repo or the bundle.
7. **Small verified steps.** After each task: run lint, type check and tests, then commit. Do not start a phase until the previous phase's acceptance criteria pass. Exception: a criterion that needs hardware you don't have may be marked `blocked: hardware` and deferred **only with my approval**.
8. **Be honest in reports.** When you finish a phase or a session, state what works, what was only partly done, and what you could not verify.
9. **Stay in scope.** Spec non-goals are out of scope for v1: training or fine-tuning, multi-node clustering, agentic browsing or any outbound call, code execution, and the v2 items (coding mode, model-swapping UI, LDAP/OIDC, ROCm). Leave clean extension points only where this prompt says so.

## 3. Decisions already made (do not re-litigate; flag only real problems)

| Area | Decision |
|---|---|
| Language | Python 3.12, TypeScript |
| Package manager | `uv` with a committed lockfile; `npm` with a committed `package-lock.json` |
| Lint / format / types | `ruff` (lint and format), `mypy` or `pyright` in strict-ish mode (choose one, record in an ADR), `eslint` + `tsc --noEmit` |
| API | FastAPI + Pydantic v2 + Uvicorn |
| LLM runtimes | `LLMBackend` interface with **Ollama** and **vLLM** adapters. Ollama (llama.cpp) for CPU and small GPUs; vLLM for NVIDIA GPUs where concurrency matters |
| Embedding / reranker | Separate local service; models chosen per §4 after licence check |
| Vector store | **Qdrant** (dense + sparse vectors, payload filter for ACLs) |
| Relational DB | PostgreSQL (SQLite allowed for unit tests only) with migrations (Alembic) |
| Queue / rate limits | Redis (ingestion queue, rate limits, per-user concurrency) |
| Document store | Volume for original files and their versions |
| Parsing / OCR | PyMuPDF, python-docx, openpyxl, Tesseract (English plus Indic language packs) |
| Auth | Argon2 password hashing, HTTP-only session cookie (with CSRF protection) or JWT, roles Admin / Editor / Viewer, document groups |
| Frontend | React + TypeScript + Vite, all assets bundled locally |
| Reverse proxy | Nginx with TLS (self-signed by default, or the organisation's own certificate), request size limits |
| Packaging | Docker + Compose, offline image tarballs, signed manifest |
| Observability | Prometheus metrics, Grafana dashboards, structured JSON logs, `pynvml` GPU exporter |
| Testing | pytest, Playwright (UI smoke), Locust or k6 (load), gitleaks |
| CI | GitHub Actions: lint, type check, tests, image build, **no-egress test**, gitleaks |

If you believe a decision is wrong, explain why in one paragraph and wait for an answer.

### 3.1 Network design (important: do not get this wrong)
Do **not** put every service on a single `internal: true` network: then users on the LAN cannot reach the UI either. Use two networks:
- `backend` (`internal: true`): api, workers, qdrant, postgres, redis, LLM runtime(s), embedding service, prometheus. No route outside the host.
- `edge`: only the Nginx proxy (and Grafana if exposed) publishes a port to the LAN. The proxy joins both networks.
Document in the runbook that the host itself should also block outbound traffic (firewall rule), and make the no-egress test check every container, including the proxy.

## 4. Model selection: how the kit knows which models to ship and use

An air-gapped server cannot download anything, so **model choice happens in three places**. Build all three.

### 4.1 Build time (connected machine): what goes into the bundle
`catalog/catalog.yaml` lists every model the kit supports. `deploy/build_bundle.sh --profile <name>` downloads only the models in that profile, converts or packages them for the runtime that will serve them, and writes them with SHA-256 checksums into the bundle. Profiles live in `catalog/profiles.yaml` and map to the spec's tiers (§4.4):

| Profile | Spec tier | Intended hardware | Contents |
|---|---|---|---|
| `lite` | T0–T1 | CPU only, or 8–12 GB VRAM | 1 small generation model + embedder + reranker |
| `standard` | T2 | 16–24 GB VRAM | `lite` + 1 mid-size model + 1 small "helper" model for routing |
| `large` | T3 | 32–48 GB VRAM (the reference tier) | `standard` + 1 large model sized for co-residency with the helper |
| `xl` | T4 | Multi-GPU / 80 GB+ | `large` + the largest permitted model(s) (stretch goal) |
| `custom` | any | any | admin lists model ids |

Packaging rules:
- **Format per runtime.** vLLM needs Hugging Face-format weights (safetensors); Ollama needs GGUF imported with a Modelfile (or an exported Ollama model store). The catalog entry states which runtime(s) each artifact is for. If a model is offered for both runtimes, the bundle carries both formats; show the size impact.
- **Downloading.** Use the Hugging Face CLI (`hf download`; older versions call it `huggingface-cli`, so check the installed version) or `ollama pull` on the build machine only. Gated models need a Hugging Face token; it stays on the build machine and is never written to the repo, logs or bundle.
- **Offline at runtime.** Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in every container, and load models from local paths only. A startup check fails loudly if any library tries to reach a hub.
- **Size report.** `build_bundle.sh` prints the total bundle size per profile, so the admin knows what removable media is needed.
- Never commit model weights to git (`.gitignore` them).

The installer must refuse, with a clear message, to pick a model that is not in the bundle.

### 4.2 Install time: which bundled model is used
Implement the spec's decision pipeline (§4.2). The capacity manager:
1. Takes the bundle's models.
2. Drops any that do not fit the probed hardware (weights + KV cache for the target context + runtime overhead + safety margin, per the spec's planner).
3. Drops any whose licence flag is not allowed.
4. Chooses the runtime (Ollama for CPU and small GPUs, vLLM for NVIDIA GPUs where concurrency matters), quantisation and context length.
5. Picks the **highest `quality_rank`** among those that fit.
6. Computes the concurrency limit, runs the micro-benchmark, and steps down one model or quantisation if it fails.
7. Writes `profile.yaml`.

`airgap models recommend` prints the ranked list with a reason for every exclusion ("does not fit: needs 31 GB, have 24 GB", "licence: restricted", "not in bundle").

### 4.3 Ranking: "best" is measured, not assumed
Public leaderboards measure general ability, not grounded RAG behaviour. Build a **model bake-off** (`eval/model_bakeoff.py`) that runs every catalog model on the kit's own evaluation set and fills `quality_rank` from the results:
- grounded-answer rate and citation correctness on the labelled corpus
- refusal precision/recall on unanswerable questions
- resistance to the injection test set
- a Hindi (or other Indian-language) question subset
- tokens/s and time-to-first-token on the machine tested

Rank by a documented weighted score (put the weights in an ADR). Re-run the bake-off whenever a model is added. Never write a rank you did not measure; leave it `null` and say so. Until a model has a measured rank, fall back to a documented rule (larger permitted model that fits wins) and label the choice "unranked" in the UI.

### 4.4 Candidate shortlist to evaluate
These come from secondary web sources dated October 2026, and some disagree with each other (for example "Qwen3.6-27B" vs "Qwen3.8-27B"). Treat every row as **unverified**. Check each model's official model card for existence, exact name, parameter count, context length, attention type and licence before adding it to the catalog. Prefer permissive licences (Apache 2.0 or MIT).

| Spec tier | Candidates to check | Notes (unverified) |
|---|---|---|
| T0: CPU only | a small Gemma 4 variant (QAT build), a ~3–4B permissive model | slow; UI warns |
| T1: 8–12 GB | Gemma 4 12B | Gemma 4 listed as Apache 2.0 |
| T2: 16–24 GB | gpt-oss-20b; a Qwen 27B-class dense model at 4-bit on 24 GB | gpt-oss-20b: Apache 2.0, ~14 GB in MXFP4, vendor claims it runs within 16 GB |
| T3: 32–48 GB | Gemma 4 31B, Qwen 27B-class at higher precision, another permissive 30B-class model; co-residency example from the spec: gpt-oss-20b + ~3B helper on 32 GB | check KV-cache cost at the target context |
| T4: 80 GB+ / multi-GPU | gpt-oss-120b, plus any MIT/Apache MoE model that passes the licence check and bake-off | MoE models still need all weights resident |
| Helper (small, fast) | the smallest model that passes the bake-off for query rewrite, intent classification, titles and summaries | measure; do not assume |
| Embedding | bge-m3, Qwen3-Embedding family | check licence and Indic-language quality |
| Reranker | a BGE reranker (multilingual), Qwen3 reranker | check licence and multilingual quality |

### 4.5 Licence gate (hard rule)
Each catalog entry has `licence`, `licence_url`, `commercial_use` (true / false / "conditional") and `redistribution_in_bundle` (true / false). Models with community or bespoke licences (for example attribution requirements, revenue or user thresholds, or service-provider clauses) are **excluded by default** and need my written approval to be added. Read the actual licence text; do not rely on a blog summary. Include each bundled model's licence file in the bundle.

### 4.6 Freshness
Model lineups change monthly. If this session has internet, web-search for the current best permissively licensed open-weight models per tier and confirm on the official model cards. If it does not, list what you need verified and ask me.

## 5. Capacity maths: known pitfalls
The spec's KV formula (`2 × layers × kv_heads × head_dim × bytes_per_element`) is a first approximation. Handle these cases explicitly in `capacity/kv.py` and the catalog, or mark the model `kv_estimate: approximate`:
- **KV cache dtype** (`bytes_per_element`) depends on runtime settings (for example FP16 vs FP8/Q8 KV cache). Store it in the profile, don't hard-code 2 bytes.
- **Sliding-window or hybrid attention** (some layers only cache a window) makes the formula overestimate.
- **Latent or compressed attention** (MLA-style) and other non-standard designs need model-specific formulas.
- **MoE models**: the KV cache depends on attention layers, but *all* expert weights must still fit in memory.
- **Runtime overhead** (CUDA context, activation buffers, vLLM's pre-allocation from `--gpu-memory-utilization`) must be measured once per runtime and GPU, then stored.

The benchmark phase must report predicted vs measured concurrency and the gap. Never present the formula's output as a guarantee.

## 6. Repository layout

Use the layout in spec §12 (`backend/`, `frontend/`, `catalog/`, `deploy/`, `eval/`, `bench/`, `docs/`, `.github/workflows/`). Add:
- `backend/cli/`: the `airgap` command (`profile`, `models`, `backup`, `restore`, `upgrade`, `doctor`), installed into the API image and wrapped by `deploy/airgap`
- `backend/models_manager/`: model library, VRAM planner, router, load/evict policy
- `catalog/profiles.yaml`
- `NOTICE`: third-party licences, reused code, bundled model licences
- `docs/spec/` (the spec, unchanged), `docs/decisions/` (one short ADR per non-obvious choice), `docs/reports/` (phase reports), `docs/PROGRESS.md` (see §7.4)
- `CHANGELOG.md`, `.gitignore` (weights, bundles, secrets, results scratch), `.env.example` (no real secrets)

Code rules: type hints everywhere, files under ~400 lines, one responsibility per module, interfaces (ports) for LLM backend, vector store, embedder, reranker, OCR, audit store and clock, so each can be faked in tests.

## 7. How to work

### 7.1 Before each phase
1. Write a plan (max one page): tasks, files you will create, tests you will write, risks.
2. List open questions. Ask the ones that change the design. Decide the rest and record the decision in an ADR.
3. Then build.

### 7.2 During a phase
- Write the failing test first for logic-heavy parts: KV maths, VRAM planner, tier logic, ACL filter, confidence gate, citation check, chunking, routing, rate limits.
- Use small fake implementations (fake LLM, fake embedder, fake hardware probe) so the default test suite runs with no GPU, no models and no network.
- Register pytest markers `gpu`, `model`, `docker` and `slow`. They are skipped by default and run only on suitable machines.
- Work on a branch per phase (`phase-N/<topic>`); commit often with messages `phaseN: type: summary`; merge to `main` only when the phase's tests are green.

### 7.3 After each phase
Produce `docs/reports/phaseN.md` with: what was built, acceptance criteria with pass/fail evidence (commands + output), known gaps, and next-phase risks.

### 7.4 Session continuity
At the end of every session, update `docs/PROGRESS.md`: current phase, tasks done, tasks in progress, blockers, decisions waiting on me, and the exact next step. At the start of every session, read it first.

## 8. Phases, tasks and acceptance criteria

Follow spec §13. If time is short, build the spec's "minimum viable impressive cut" first and trim Phase 5 and the dashboard before anything else.

### Phase 1: Core (spec F1–F9, §7)
**Tasks**
1. Compose stack: proxy (Nginx + TLS), api, ingestion worker, embedding/reranker service, qdrant, postgres, redis, ollama, frontend. Two networks as in §3.1. Health checks on every container, `restart: unless-stopped`.
2. `ingestion/`:
   - parsers for PDF, DOCX, TXT, MD, CSV, XLSX; single-file, multi-file and **folder** upload
   - upload checks: type allow-list (check content, not only extension), size limit, filename sanitisation; parsing runs in the low-privilege worker and never executes file content
   - automatic detection of image-only pages and OCR fallback (Tesseract, language packs configurable)
   - structure-aware chunking (headings, tables) with overlap; chunk metadata: doc id, version, page, section, language, ACL groups (default group until Phase 3)
   - SHA-256 dedupe; versioned re-upload that replaces the old version and re-indexes only that file; delete removes the file, all versions, dense and sparse entries
   - per-file status: queued / parsing / embedding / ready / failed (with reason)
3. `retrieval/`: dense + BM25 sparse hybrid with rank fusion, reranker, and a **confidence gate** (configurable threshold on the best reranked score; can be stricter on small tiers per spec §14).
4. Query flow (spec §7): follow-up rewrite to a standalone query → retrieval → gate → prompt with clearly delimited context → generation → **citation verification** (every quoted span must exist in a retrieved chunk; otherwise retry once, then flag) → streamed answer with source links.
5. Modes: **Grounded-only** (cite or refuse) and **General** (gate does not refuse; content not supported by retrieved evidence is labelled "general knowledge, not from your documents"). Coding mode is v2: leave a mode enum value and routing hook only.
6. Per-user conversation history (stored in Postgres, isolated per user).
7. Feedback (F9): thumbs up/down and "wrong answer" reports, stored with the question, answer and retrieved chunk ids so they can be promoted into the eval set.
8. Minimal UI: chat with mode selector and streaming, citation viewer (document, page/section, quoted passage; click to open the source), upload page with per-file status, feedback buttons, history.
9. API endpoints for this phase from spec §11: `POST /documents`, `GET /documents`, `DELETE /documents/{id}`, `POST /chat` (streaming, body includes `mode`), `GET /chat/history`, `POST /feedback`, `GET /health`.

**Acceptance**
- `docker compose up` from a clean clone works on one dev machine (network allowed for the first build).
- Upload a PDF, a DOCX, a scanned PDF (OCR), a CSV, an XLSX and a folder; all reach `ready`. An oversized file and a disallowed type are rejected with clear errors.
- A question answerable from the corpus returns a cited answer with page/section; an unanswerable question returns the refusal text; both covered by automated tests with a fake LLM and a small fixture corpus.
- Re-uploading an unchanged file is a no-op; a changed file replaces the old version and old chunks disappear from both dense and sparse search.
- Citation verification test: a fake LLM that invents a quote is caught.
- A follow-up question ("and what about the second one?") is rewritten and answered correctly in a test.

### Phase 2: Capacity manager (spec §4.1–4.6, F10)
**Tasks**
1. `capacity/probe.py`: NVML GPUs/VRAM/driver/compute capability, RAM/swap, CPU model, cores and flags (AVX2/AVX-512), free disk and disk type. Optional: Apple Silicon unified memory. Stretch: AMD ROCm. Must degrade gracefully when NVML is absent. Returns a typed `HardwareReport`.
2. `capacity/tiers.py` and `catalog/catalog.yaml`: tier classification (no-GPU / small / medium / large / multi-GPU); catalog entries (seeded from §4.4 after verification) with id, runtime(s), quantisation, artifact paths, SHA-256, params, layers, kv_heads, head_dim, attention type, max context, minimum memory, languages, licence fields from §4.5, `quality_rank`, and measured-results fields (empty until benchmarked).
3. `capacity/kv.py`: KV maths and concurrency estimate with safety margin, handling the pitfalls in §5. Unit-test against hand-computed examples.
4. `capacity/benchmark.py`: ~60 s micro-benchmark (tokens/s, time-to-first-token, no OOM); step down one model/quantisation if below thresholds.
5. **vLLM adapter** (OpenAI-compatible server) alongside Ollama; runtime settings generated from the profile for both (vLLM `--gpu-memory-utilization`, `--max-model-len`, `--max-num-seqs`; Ollama parallelism, context and KV cache settings). Verify flag names per rule 4.
6. Writes `profile.yaml`; `GET /admin/profile`; `POST /admin/profile/rerun`; `airgap profile --rerun`; admin UI page showing hardware, tier, model, settings, expected capacity and expected quality level. The UI shows a warning on T0 (CPU) and low tiers.
7. Runtime adaptation (spec §4.6): load shedding (queue or "busy, try again" instead of crashing), dynamic context cap based on free KV budget, swap-on-demand when two models don't fit together with swap latency shown.
8. `eval/model_bakeoff.py` and `airgap models recommend` as in §4 (bake-off runs on whatever hardware exists; record the machine).

**Acceptance**
- Probe, tiering, KV maths and step-down logic have unit tests with mocked hardware reports covering: no GPU, 8 GB, 16 GB, 24 GB, 48 GB, multi-GPU.
- Profile chosen automatically on **2+ real machines** (spec target), with a predicted-vs-measured concurrency table in `bench/results/`. If fewer machines are available, say so and mark `blocked: hardware`; do not fake it.

### Phase 2b: Multi-model manager (spec §4.7, F22–F28)
**Tasks**
1. Model library (F22): every catalog model with size, licence and a live "fits on this hardware / fits alongside current models / does not fit" indicator; install from bundle or local path with SHA-256 verification.
2. VRAM planner (F23): the spec's algorithm, showing remaining budget and resulting concurrency per model for each combination, plus "does not fit" suggestions (smaller quantisation, shorter context, drop a model).
3. Co-residency (F24): Ollama first; vLLM multi-process with `--gpu-memory-utilization` fractions summing below 1 only if time allows. Say which is done.
4. Routing (F25), rule-based only in v1: (a) user picks the model in the UI, (b) mode-based defaults, (c) pipeline roles from YAML (`routes`: grounded_answer, general_chat, query_rewrite, intent_classify, title_summary), (d) rule-based fallbacks. `PUT /admin/routes`.
5. Load/evict (F26): `pinned` vs `on_demand` models, LRU eviction after idle time, warm-up on start, cold-start latency shown in the UI.
6. Per-model limits and health (F27): per-model concurrency, queue and context caps; per-model tokens/s and latency; automatic fallback when a model fails.
7. Model-aware evaluation (F28): the trust report and bake-off can be re-run per model **and per routing policy**.
8. Endpoints: `GET /admin/models`, `POST /admin/models/plan`, `POST /admin/models/install`, `POST /admin/models/{id}/load|unload|pin`.

**Acceptance**: planner unit tests (fits, does not fit with suggestions, concurrency split, each model's own KV cache counted); on real hardware, two models resident with helper steps routed to the small one, and a mixed-load benchmark comparing "one model for everything" vs "routed" for latency and quality. If routing is not faster or equal in quality, report that in `BENCHMARK.md`.

### Phase 3: Quality and security (spec F11, F13–F15, F19–F20, §8, §9)
**Tasks**
1. Auth: local accounts, Argon2, login rate limit and lockout, `POST /auth/login`, `POST /auth/logout`, admin account created at install. **Auth required on every route except `/health` and login.** Define exactly what Admin, Editor and Viewer may do (write it in an ADR and enforce it with tests).
2. Document groups and ACLs (F13): `PATCH /documents/{id}/acl`; ACL filter inside the Qdrant query and the sparse search; existing documents migrate to a default group; ACL also checked on document view/download, citations, history, eval and audit endpoints.
3. Audit log (F14): append-only table (the app's DB user has no UPDATE/DELETE on it); records who asked what, which documents were retrieved, which model answered, answer or refusal; `GET /admin/audit` with filters and export; retention setting. By default logs do not store full document text.
4. Rate limits and quotas (F15): per-user concurrency and request caps in Redis.
5. Prompt-injection defences (F19): delimited context, instruction hierarchy, a red-team dataset of hostile documents, injection pass rate measured.
6. Optional PII redaction (F20): pattern/NER-based masking before indexing and/or in logs, configurable.
7. Eval harness (`eval/run_eval.py`) and datasets in `eval/datasets/`:
   - 60–100 labelled questions on a public + synthetic corpus (record sources and licences in `docs/data-card.md`), including deliberately unanswerable ones
   - metrics: hit@k, MRR, grounded-answer rate, refusal precision/recall, citation correctness, ACL-leak test, injection pass rate, a small Hindi (or other) set, judge-vs-human agreement
   - human labels from 2 raters on a subset; a judge model is used only after calibration against them
8. Trust report (F11): after upload (optional, admin-triggered), generate test questions from the customer's documents, add admin-pasted questions, run them, and show hit@k, grounded-answer rate, refusal accuracy and the latest no-egress test result in the UI. `POST /eval/run`, `GET /eval/report`. Note the bias risk of model-generated questions (spec §14) in the report.

**Acceptance**
- **ACL leak test: 0 leaks** across a committed test set with at least [N] attack questions (state N). Any leak is a release blocker.
- Unauthenticated access to any protected route returns 401, and a Viewer gets 403 on admin routes, covered by a route-enumeration test.
- `EVAL.md` generated from real runs with sample sizes. Judge scoring reported only alongside its agreement with human labels.
- Tests green in CI.

### Phase 4: Delivery (spec §10, F12, F16–F18, F21)
**Tasks**
1. `deploy/build_bundle.sh --profile lite|standard|large|xl|custom` (connected machine): image tarballs (`docker save`, pinned by digest), only the models in the chosen profile (per §4.1), Tesseract packs, Python wheels/lockfile, licence files, `MANIFEST.json` with SHA-256 for every file, and a **signature**. Choose a signing tool (for example minisign, cosign or GPG; record in an ADR). The private key stays off the bundle and out of the repo; the public key ships with the installer and is verified before anything is loaded.
2. Host prerequisites: document the supported host OS(es) and what must already be installed (Docker Engine + Compose plugin, NVIDIA driver and NVIDIA Container Toolkit for GPU hosts). Provide offline packages or clear offline instructions for these, and make `install.sh` and `airgap doctor` check them first.
3. `install.sh --bundle <path>`: verify manifest and signature → load images → probe hardware → select profile → run micro-benchmark → create admin account and generate secrets → start stack → smoke test + no-egress test → print URL, profile summary and next steps.
4. Day-2 operations (F16, F17): `airgap backup` (Postgres dump, Qdrant snapshot, document store, config, `profile.yaml`, versioned), `airgap restore`, `airgap upgrade --bundle <path>` with automatic rollback on failed health check, new models arriving as signed bundles, `airgap profile --rerun`, `airgap doctor` (diagnostics).
5. Health and self-healing (F18): container health probes, automatic restart, degraded-mode banner in the UI when the model or a dependency fails.
6. Admin dashboard (F12): GPU/VRAM and RAM use, tokens/s, time-to-first-token, p50/p95 latency, queue depth, active users, error rate, both in the web UI (`GET /admin/metrics`) and in Grafana.
7. No-egress test (F21): from every container, attempt outbound connections (DNS, HTTP/HTTPS to a public IP, raw TCP) and fail if any succeeds. Runs in CI, at install time, and its last result appears in the trust report.
8. Load test (spec §9.2): 1 / 10 / 30 / 50 concurrent users; p50/p95 latency, time-to-first-token, tokens/s, GPU memory, error rate; raw CSVs and a latency-vs-concurrency chart in `bench/results/`.
9. Ablations (spec §9.2): quantisation level, context length, hybrid vs dense only, reranker on/off. Repeat per hardware tier you actually have.
10. `docs/runbook.md`: install, upgrade, rollback, backup/restore, troubleshooting, host firewall, TLS certificate replacement, optional disk-encryption guidance.

**Acceptance**
- Install from the bundle on a machine with networking **disabled**, ending with a passing smoke test.
- No-egress test passes in CI and at install, and **fails** when you deliberately attach a container to an outbound network (prove the test can catch a leak).
- A tampered bundle file fails signature/checksum verification.
- Upgrade followed by a deliberately broken release rolls back automatically.
- Backup on one install, restore on a clean install, same documents, users, ACLs and answers.
- `BENCHMARK.md` published from real runs.

### Phase 5: Polish
README in the spec's order (problem in two sentences → 20-second demo GIF → hardware profile screenshot → results table → "Tested on" hardware table → one-command quick start), plus a statement of what was built vs reused (spec §16). `docs/`: architecture as built, threat model (STRIDE table), runbook, hardware profiles, data card. Multilingual check, feedback loop into the eval set, a 3-minute demo script and shot list (I will record the video). Every number in the README links to a results file.

## 9. Definition of done (whole project)

- All phase acceptance criteria met, or explicitly listed as unmet with the reason.
- Every API endpoint in spec §11 implemented, documented in OpenAPI, and covered by at least one test.
- CI green: lint, type check, unit tests, container build, no-egress test, gitleaks.
- `EVAL.md` and `BENCHMARK.md` contain only measured values, with scripts and raw CSVs to reproduce them.
- `NOTICE` complete; every bundled model has a recorded licence, licence file and commercial-use flag.
- `docs/runbook.md` lets a new admin install, back up, upgrade, roll back and troubleshoot without help.
- A "Known limits" section that matches spec §14, and a "Tested on" table that lists only hardware actually tested.

## 10. What to ask me vs. decide yourself

**Ask me** when: the choice changes the architecture or costs more than ~2 hours to undo; a licence is unclear; a spec statement conflicts with the real behaviour of a runtime; hardware you need is unavailable; you would add a model with a non-permissive licence; or you need to choose this kit's own licence.
**Decide and record in an ADR** when: library choice among equivalent options, naming, folder details, test structure, UI layout.

## 11. Output format for each of your replies

1. One-line status.
2. What you did (files and commands, brief).
3. Evidence: test/lint output summary (pass/fail counts), not a recap of every step.
4. Problems, assumptions and anything unverified.
5. The next step, and any question that blocks it.

## 12. Start now

Do **only** this first:
1. Read the spec end to end.
2. Reply with:
   - (a) a list of ambiguities or contradictions you found in the spec;
   - (b) the verified details (name, size, context, attention type, licence) of the §4.4 candidates for Phase 1 (embedding, reranker, one generation model for the dev machine) and your proposed bake-off design;
   - (c) the hardware you will have for testing (ask me) and which tiers that covers;
   - (d) your Phase 1 plan per §7.1.
3. Create `docs/PROGRESS.md` with the plan, then wait for my approval before creating any other files.
