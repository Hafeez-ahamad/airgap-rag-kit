# Roadmap

Milestones follow the master prompt §6, mapped onto the existing BUILD_PROMPT phases. "Exit"
criteria need recorded evidence at the stated validation level (master prompt §10.1). Levels:
**L1** static · **L2** unit · **L3** simulated-profile · **L4** mock-adapter/integration ·
**L5** lightweight real service · **L6** full-stack acceptance · **L7** physical target device.

| # | Milestone | Deliverables | Depends on | Exit (evidence level) | Hardware needed |
|---|---|---|---|---|---|
| M0 | Audit & stabilisation | `IMPLEMENTATION_AUDIT.md`, baseline, memory diagnosis | – | audit + baseline recorded (L1–L2) | any |
| M1 | Vertical slice | Lean native profile; General Assistant + Document Assistant through real local LLM; e2e script | M0 | `eval/smoke/phase1_e2e.py` passes against one real profile (**L6**) | ≥16 GB RAM recommended; 7.4 GB laptop marginal |
| M2 | Hardware scan & planner | `capacity/` probes, catalogue, KV maths, planner, dry-run plan, Compatibility Lab | M0 | deterministic planner tests over 5+ simulated profiles (L3); real probe on this laptop (L5) | none for L3 |
| M3 | Setup, prerequisites, model manager | setup state machine, prerequisite checks, Ollama pull with progress, resumable verified downloads, CLI | M2 | mock tests for interruption/resume (L4); one real pull on a target (L5/L7) | network + disk for real pull |
| M4 | Production RAG & chat UX | onboarding wizard, workspace, Markdown, citations, document library, diagnostics | M1, M3 | UI tests (L4); UI-driven e2e (L6) | as M1 |
| M5 | Security & operations | auth/roles/ACL editing, audit log, rate limits, injection set, no-egress test, backup/restore, uninstall | M1 | ACL leak = 0 on committed set (L4 with real Qdrant local mode); no-egress test on a live stack (L6) | as M1 |
| M6 | Compatibility, performance, release | hardware matrix, bake-off, load test, bundle build/verify, runbook, release report | all | measured results per tested machine (L7) | each claimed tier |

## Hardware matrix plan
Profiles start **not yet validated**. They move only with committed evidence.

| Target | Planned validation | Status |
|---|---|---|
| Windows 11, 8 GB RAM, 4 GB NVIDIA (this laptop) | native profile, L5–L6 | in progress |
| Windows, 16 GB RAM, CPU only | native profile, L7 | not yet validated |
| Linux, 32 GB RAM, 12 GB NVIDIA | compose profile, L6–L7 | not yet validated |
| Linux, 64 GB RAM, 24 GB NVIDIA | compose profile, L7 | not yet validated |
| macOS Apple Silicon | native profile, L7 | not yet validated |
| Offline / air-gapped Linux server | bundle install, L7 | not yet validated |
