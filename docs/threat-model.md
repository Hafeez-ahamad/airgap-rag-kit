# Threat model (STRIDE)

Scope: the kit as installed on one host (native or compose profile), its users on the local
machine or LAN, uploaded documents, local models. Status column: **tested** means an automated
test exercises the control; **partial** or **open** are explained.

| # | Threat | Asset | Control | Status / evidence |
|---|---|---|---|---|
| S1 | Spoofing a user | sessions | Argon2 hashes, 256-bit session tokens (hash stored), HttpOnly SameSite=Strict cookie, lockout after 5 failures, per-client throttle | tested `test_auth_acl.py` |
| S2 | Remote first-admin takeover | setup | bootstrap and first-run setup only from loopback and only while no account exists | tested `test_bootstrap_only_when_empty_and_then_closed`, `test_first_run_setup_is_local_only` |
| S3 | Dev stub left on | auth | settings refuse `AIRGAP_DEV_AUTH=1` with `AIRGAP_ENV=production` | tested (Phase 1) |
| T1 | Tampered offline bundle | supply chain | Ed25519 signature + SHA-256 per file, unlisted/missing files rejected | tested `test_tampered_bundle_fails_verification` (6 attacks) |
| T2 | Tampered model download | models | catalogue-pinned SHA-256; mismatch deletes partial file and fails | tested `test_download_checksum_mismatch_removes_partial_and_fails` |
| T3 | Tampered/corrupt backup | data | manifest SHA-256 checked before any write; data-filter extraction | tested `test_tampered_backup_is_rejected_before_anything_is_written` |
| T4 | Malicious upload (zip bomb, traversal, wrong type) | host | magic-byte allow-list, size and decompression limits, sanitised names, parsing never executes content | tested (Phase 1 `test_validate.py`) |
| T5 | CSRF | state changes | double-submit token required on every non-GET with a session | tested `test_csrf_required_for_state_changes` |
| R1 | Denying an action | accountability | append-only audit table (DB triggers), login/upload/ACL/user/chat/setup events | tested `test_audit_table_is_append_only_in_the_database` |
| I1 | Reading another group's documents via chat | documents | ACL filter inside dense **and** sparse Qdrant queries; listing/download/delete/ACL checks | tested: 8-question + 4-question attack sets, 0 leaks (fake LLM, real Qdrant) |
| I2 | Upload overwriting/revealing a hidden document | documents | dedupe/versioning only within visible groups | tested (regression for a bug found 2026-10-29) |
| I3 | Prompt injection in documents | answers | documents fenced as untrusted data; refusal decided by code (gate); citations verified against retrieved text | **partial**: injection set exists; measured only with fake LLM (meaningless for real models) |
| I4 | Data leaving the host | everything | in-process egress guard; compose internal network; no CDN assets; no telemetry | **open**: native Ollama observed connecting to a public address and auto-updating (eval/results/no-egress-*). Compose test not run |
| I5 | Secrets/paths in logs or exports | diagnostics | redaction of paths, e-mails, tokens, key=value secrets; question text not stored in audit by default | tested `test_redaction_removes_personal_paths_and_secrets` |
| I6 | LAN exposure | API | native binds 127.0.0.1; warning when another host is given | by construction; not tested on a network |
| D1 | Host exhaustion by heavy requests | availability | per-user rate + concurrency limits, global concurrency (1 on constrained tiers), upload and batch limits | tested limiter; host-level effect **not measured** |
| D2 | Models that do not fit | availability | planner with headroom; installer refuses ineligible models; real inference check before completion | tested (simulated); real check proven once on dev laptop |
| E1 | Viewer using admin/editor functions | authorisation | role checks on every route; route enumeration test | tested `test_viewer_is_forbidden_on_admin_and_editor_routes`, 401 enumeration |
| E2 | Simulation driving a real install | setup | simulated assessments never stored; installer needs a stored real scan | tested `test_lab_never_touches_setup_state_or_starts_work` |

## Open items
1. Prove no-egress with a host firewall capture (native) and the compose script, including its
   self-test. Configure `OLLAMA_NO_CLOUD=1` and disable Ollama auto-update on offline targets.
2. Measure prompt-injection resistance with real models (`eval/run_eval.py --adapter live`).
3. Audit retention/purge, multi-replica rate limiting, backup encryption: not implemented.
4. Pin container base images by digest; dependency vulnerability scan in CI (gitleaks only today).
