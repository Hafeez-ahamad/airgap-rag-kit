# Release readiness

_2026-10-29, branch `phase-2/hardware-adaptive`. Not releasable yet. Full-stack acceptance passed
21/23 on one machine (native profile, Windows laptop); OCR, no-egress and calibration remain open._

Columns: **Impl** implemented · **Auto** covered by automated tests · **Sim** passed in
simulated-profile tests · **Mock** passed with mock adapters · **Real** passed with lightweight
real services · **Full** passed full-stack acceptance · **HW** physically validated on named
hardware. "–" = not applicable, "no" = not achieved.

| Capability | Impl | Auto | Sim | Mock | Real | Full | HW | Evidence / limitation / next step |
|---|---|---|---|---|---|---|---|---|
| Hardware probe (Win/Linux/macOS) | yes | yes | – | yes | yes (Windows) | no | Windows laptop | `test_capacity_probe.py`; real run on dev laptop. Linux/macOS real probe not run |
| Resource planner + reason codes | yes | yes | yes (6 profiles + edge cases) | – | yes (real scan) | no | – | `test_capacity_planner.py`. Estimates; VRAM over-estimated vs one measurement |
| Model catalogue (provenance, licence gate) | yes | yes | yes | – | – | – | – | sizes partly rounded registry figures; `quality_rank` all null (no bake-off) |
| Compatibility Lab (API + UI) | yes | yes | yes | yes | yes (in browser) | – | – | `test_setup_lab.py`, `ui.test.tsx`, manual browser check |
| Prerequisite checks + actions | yes | yes | – | yes | yes (dev laptop) | no | – | correctly blocked on missing PyTorch |
| Setup wizard / installer (pull, verify, resume, cancel) | yes | yes | – | yes | yes | yes (native) | Windows laptop | real install via API: pull, SHA verify, inference check (`setup-flow-*.json`); resume/cancel real-run not exercised |
| Resumable SHA-256 downloads | yes | yes | – | yes | partial | – | – | real SHA verification of 4.59 GB local files; real HTTP resume not exercised |
| General Assistant | yes | yes | – | yes | **yes** | no | Windows laptop (qwen2.5:7b) | `smoke-llm-*.json`; UI-driven chat streamed a real answer |
| Document Assistant (ingest, retrieve, cite, refuse) | yes | yes | – | yes | yes | **yes (native, 21/23)** | Windows laptop | `e2e-native-20261029-run3.txt`; OCR untested (Tesseract absent) |
| Citation verification / abstention | yes | yes | – | yes | yes | yes | Windows laptop | live eval: 11/11 valid, refusal recall 4/5; one false refusal (uncalibrated gate) |
| Prompt-injection defences | yes | partial | – | yes (meaningless with fake LLM) | yes | yes | Windows laptop | live: 0/3 complied (n=3, qwen3:4b only) |
| Auth, roles, CSRF, lockout | yes | yes | – | yes | yes (browser, native) | no | – | `test_auth_acl.py` |
| ACL in retrieval | yes | yes | – | yes (real Qdrant, 0/12 leaks) | yes | yes | Windows laptop | live: 0/4 leaks with control |
| Audit log (append-only) | yes | yes | – | yes | – | no | – | DB triggers tested on SQLite; Postgres trigger untested |
| Rate/concurrency limits | yes | yes | – | yes | – | no | – | host-protection effect not measured |
| Native lean profile | yes | yes | – | yes | yes | yes | Windows laptop | restart kept the setup-selected model; memory tight (0.23 GB free at peak) |
| Compose profile | yes | partial | – | – | no | **failed** (memory) | – | needs 16 GB+ Linux host |
| Backup / restore | yes | yes | – | yes | – | no | – | not encrypted |
| Signed offline bundle | yes | yes | – | yes | – | no | no | `build_bundle.sh` not run |
| No-egress | partial | yes (parsers, guard) | – | – | **failed then passed** | no | – | native Ollama connected to a public address in 1 of 2 runs and auto-updated; compose script not run |
| UI (wizard, workspace, library, admin) | yes | yes (8 tests) | – | yes | yes (browser) | no | – | mobile layout not checked in a device emulator |
| Eval harness | yes | – | – | yes | yes | yes | Windows laptop | `eval-live-*.json` (n=22, one run); fake run proves plumbing only |

## Release blockers
1. No-egress proven at firewall level with Ollama cloud/auto-update disabled.
2. Calibrate the confidence-gate threshold (0.30 produced one false refusal in 22 live questions)
   on a larger labelled set; n=22 is too small to tune on.
3. OCR path on real Tesseract; repeat acceptance on a 16 GB+ machine (this laptop pages).
