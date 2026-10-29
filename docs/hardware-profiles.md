# Hardware matrix

Only rows marked **physically validated** have been tested on real hardware, and only to the
level stated. Everything else is **not yet validated**: supported by planner logic and simulated
tests, not by evidence. Raw results live in `eval/results/`.

| Target | Profile | What ran | Level | Status |
|---|---|---|---|---|
| Windows 11, Ryzen 7 4800H, 7.4 GB RAM, RTX 3050 Laptop 4 GiB (driver 572.47), Ollama 0.40.12/0.40.13 | native | real scan; **real setup install** (qwen3:4b pulled, 4.59 GB retrieval models SHA-verified, inference check); Phase 1 e2e acceptance 21/23; live eval; General Assistant in the UI; no-egress observation | **L6 (full-stack acceptance, native profile)** | **physically validated, with gaps**: OCR not installed (2 e2e checks fail); memory tight (paging) |
| same laptop | compose | images built (earlier run); stack ran out of memory | L6 attempted | **failed** (memory, Docker engine HTTP 500) |
| Windows/Linux, 16 GB RAM, CPU only | native | simulated profile `cpu-only-16` | L3 | not yet validated |
| Linux, 32 GB RAM, 12 GB NVIDIA | native/compose | simulated profile `balanced-32-12` | L3 | not yet validated |
| Linux, 64 GB RAM, 24 GB NVIDIA | compose | simulated profile `high-64-24` | L3 | not yet validated |
| macOS Apple Silicon, 16 GB | native | simulated profile `apple-silicon-16` | L3 | not yet validated (unified-memory GPU share is an estimate) |
| Offline / air-gapped Linux | bundle | bundle verify + tamper tests (unit) | L2/L4 | not yet validated |
| AMD ROCm, Intel GPUs | – | not modelled | – | unsupported (planned as CPU-only) |

## Measured on the dev laptop (single samples, not benchmarks)
From `eval/results/smoke-llm-20261029-*.json` (qwen2.5:7b, num_ctx 2048):
- cold load + first reply: 74.2 s; General Assistant answer: first chunk 1.5 s, 26.8 s total.
- GPU memory with the model loaded: 2,239 MiB of 4,096 MiB (Ollama offloaded only part of the
  model; the planner predicted ~3.0 GiB VRAM for a 4K context: the planner over-estimates VRAM
  use here, and under-estimates CPU spill. One sample; not yet fed back into the catalogue).

### Native full-stack run, 2026-10-29 (Docker Desktop stopped first)
- Setup through the kit's API (`setup-flow-*.json`): scan 2.1 s; install 762 s including the
  2.5 GB pull and SHA-256 verification of 4.59 GB retrieval files; real inference check 15.7 s.
- Retrieval models in-process, bf16 (`smoke-retrieval-*.json`): load 53-59 s; **peak working set
  4.31 GB** (the catalogue's 2.56 GiB was steady state; the planner now budgets the peak).
  EN/HI cosine 0.915 vs 0.518 unrelated; reranker 0.971 vs 0.000.
- Phase 1 acceptance script (`e2e-native-20261029-run3.txt`): **21/23 pass**. Failures: scanned
  PDF and its question (Tesseract not installed). Ingestion of 6 files: 55 s. First grounded
  answer 13.5 s.
- Live eval (`eval-live-*.json`, n=22, one run): hit 10/10, grounded 10/10, citation validity
  11/11, refusal recall 4/5, refusal precision 4/4, injection compliance 0/3, ACL leaks 0/4.
- Memory during ingestion: host free RAM fell to **0.23 GB**; app working set peaked at 2.76 GB;
  GPU 2.3 GiB used. The planner had warned that ~5.2 GiB was needed with 3.5 GiB free. The run
  completed, but with paging: not a comfortable configuration for daily use on this laptop.
