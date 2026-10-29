# Release readiness

_2026-10-29, branch `phase-2/hardware-adaptive`. Not releasable yet: no full-stack (L6)
acceptance has passed on any machine._

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
| Setup wizard / installer (pull, verify, resume, cancel) | yes | yes | – | yes | partial | no | – | real scan + prereqs in UI; real pull/install not run (would download GBs) |
| Resumable SHA-256 downloads | yes | yes | – | yes | no | no | – | mock HTTP only |
| General Assistant | yes | yes | – | yes | **yes** | no | Windows laptop (qwen2.5:7b) | `smoke-llm-*.json`; UI-driven chat streamed a real answer |
| Document Assistant (ingest, retrieve, cite, refuse) | yes | yes | – | yes | **no** | no | no | needs PyTorch + ~2.6 GiB RAM; not run on dev laptop |
| Citation verification / abstention | yes | yes | – | yes | no | no | – | fake LLM only |
| Prompt-injection defences | yes | partial | – | yes (meaningless with fake LLM) | no | no | – | run `run_eval.py --adapter live` |
| Auth, roles, CSRF, lockout | yes | yes | – | yes | yes (browser, native) | no | – | `test_auth_acl.py` |
| ACL in retrieval | yes | yes | – | yes (real Qdrant, 0/12 leaks) | no | no | – | fake embedder/LLM; real Qdrant filter |
| Audit log (append-only) | yes | yes | – | yes | – | no | – | DB triggers tested on SQLite; Postgres trigger untested |
| Rate/concurrency limits | yes | yes | – | yes | – | no | – | host-protection effect not measured |
| Native lean profile | yes | yes | – | yes | yes | no | Windows laptop | restart persistence + crash recovery tested with fakes |
| Compose profile | yes | partial | – | – | no | **failed** (memory) | – | needs 16 GB+ Linux host |
| Backup / restore | yes | yes | – | yes | – | no | – | not encrypted |
| Signed offline bundle | yes | yes | – | yes | – | no | no | `build_bundle.sh` not run |
| No-egress | partial | yes (parsers, guard) | – | – | **failed then passed** | no | – | native Ollama connected to a public address in 1 of 2 runs and auto-updated; compose script not run |
| UI (wizard, workspace, library, admin) | yes | yes (8 tests) | – | yes | yes (browser) | no | – | mobile layout not checked in a device emulator |
| Eval harness | yes | – | – | yes | no | no | – | `eval-fake-*.json` proves plumbing only |

## Release blockers
1. Full-stack Document Assistant acceptance (VALIDATION.md §A) on at least one machine.
2. No-egress proven at firewall level with Ollama cloud/auto-update disabled.
3. Live evaluation (`run_eval.py --adapter live`) with real models: injection, refusal, citations.
4. Calibrate the confidence-gate threshold (0.30, uncalibrated) on the live eval set.
