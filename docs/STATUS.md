# Status

_Last updated: 2026-10-29, end of session 3. Branch `phase-2/hardware-adaptive` (from
`phase-1/core`; neither merged to `main`)._

## Completed this session (with evidence)
| Area | Evidence |
|---|---|
| Audit, roadmap, memory diagnosis | `docs/IMPLEMENTATION_AUDIT.md`, `docs/ROADMAP.md` |
| Hardware probe, catalogue, KV maths, planner | 40 tests; real probe on dev laptop |
| Compatibility Lab (API, CLI, UI) | Lab tests (API + UI); manual browser check |
| Auth, roles, ACL editing, audit, rate limits | `test_auth_acl.py` (23 tests) |
| Setup: scan, prerequisites, dry-run plan, installer | `test_setup_lab.py` (24 tests); real scan and prerequisites in browser |
| Native lean profile, CLI, backup/restore, bundle signing | `test_native_ops.py` (19 tests) |
| General Assistant mode; UI rebuild | `ui.test.tsx` (8 tests); real model chat through the UI |
| Eval harness, no-egress tests | `eval/run_eval.py`, `eval/security/no_egress.py`, `deploy/security/no_egress_compose.sh` |
| Docs | runbook, threat model, hardware matrix, validation guide, release readiness, ADR-0009/0010/0011 |

Final checks: backend `pytest` **181 passed, 1 skipped** (Tesseract absent); ruff and mypy clean
(99 files); UI `vitest` **8 passed**; eslint, tsc and vite build clean.

## Bugs found and fixed
- Upload could create a new version of, or reveal the name of, a document in a group the uploader
  cannot see (cross-ACL overwrite/disclosure).
- Failed-login counts were rolled back, so lockout never triggered.
- Session cookie max-age used wall-clock time instead of the session clock.
- The installer published its status before persisting it; a failed inference check left the
  verify step "running".
- Embedded vector store was not closed on shutdown (lock held across restarts).
- Native profile pointed at the container Ollama URL; admins could not leave unfinished setup;
  the Lab could assess a different profile from the one displayed.

## Real-service evidence (dev laptop: Windows 11, 7.4 GB RAM, RTX 3050 4 GiB)
- Real inference via the kit's adapters (qwen2.5:7b): PASS. Cold 74 s; answer 26.8 s; 2.2 GiB VRAM.
- General Assistant through the UI with the real model: PASS (answer itself was wrong: model quality).
- No-egress observation: the kit's process made no external connection in either run. **Ollama
  connected to a public address in run 1 (FAIL)**, none in run 2. Ollama also auto-updated itself
  (0.40.12 -> 0.40.13) during the session.

## Blocked or unverified
| Item | Why | Next step |
|---|---|---|
| Document Assistant with real models | needs PyTorch + ~2.6 GiB RAM; ~1 GB free here | VALIDATION.md §A on a 16 GB+ machine |
| Compose full stack | Docker Desktop engine returns HTTP 500; 7.4 GB RAM | VALIDATION.md §B on Linux 16 GB+ |
| Offline bundle install | build not run (Docker down, multi-GB downloads) | VALIDATION.md §C |
| Firewall-level no-egress | needs host firewall change (not done without approval) | VALIDATION.md §D |
| Real-model eval, gate calibration, bake-off | depends on the Document Assistant run | `run_eval.py --adapter live` |
| Linux/macOS real probes, every non-laptop tier | hardware not available | marked "not yet validated" |

## Decisions waiting on the owner
1. Restart Docker Desktop (wedged), or stop it to free RAM so the Document Assistant can be
   tried here. I have not touched it.
2. Set `OLLAMA_NO_CLOUD=1` and disable Ollama auto-update on this machine (persistent host
   settings; not changed by me).
3. Keep SQLite in the native profile (ADR-0009).
4. Merge `phase-1/core` and this branch to `main` before or after a full-stack pass elsewhere.

## Next action
Run docs/VALIDATION.md §A on a 16 GB+ machine and record the results here.
