# Hardware matrix

Only rows marked **physically validated** have been tested on real hardware, and only to the
level stated. Everything else is **not yet validated**: supported by planner logic and simulated
tests, not by evidence. Raw results live in `eval/results/`.

| Target | Profile | What ran | Level | Status |
|---|---|---|---|---|
| Windows 11, Ryzen 7 4800H, 7.4 GB RAM, RTX 3050 Laptop 4 GiB (driver 572.47), Ollama 0.40.12 | native | real probe + planner; UI-driven General Assistant with qwen2.5:7b (Q4_K_M); real inference check; no-egress observation | L5 (lightweight real service) | **physically validated: General Assistant only**. Document Assistant not run (needs ~2.6 GiB more RAM than was free) |
| same laptop | compose | images built (session 2); stack ran out of memory | L6 attempted | **failed** (memory, Docker engine HTTP 500) |
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
