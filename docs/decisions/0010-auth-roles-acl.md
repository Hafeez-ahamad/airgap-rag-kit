# ADR-0010: Local accounts, roles, ACL editing, audit

Status: accepted (2026-10-29), replaces the ADR-0008 stub outside development.

- **Accounts:** local users, Argon2id (argon2-cffi defaults), 12-character minimum password.
  5 failed logins lock an account for 15 minutes; failure counts are committed even though the
  login fails. A per-client throttle (10/min) sits in front. Unknown users and wrong passwords
  return the same message.
- **Sessions:** random 256-bit token in an `HttpOnly; SameSite=Strict; Secure` cookie; only its
  SHA-256 is stored server side, 12 h TTL. Changing a user's role, groups, password or disabling
  them deletes their sessions.
- **CSRF:** double submit. The session's CSRF token is returned at login and set in a readable
  cookie; every non-GET request must echo it in `X-CSRF-Token`.
- **First admin:** `POST /auth/bootstrap`, accepted only while no account exists **and** from a
  loopback address. First-run setup routes follow the same rule; afterwards they are admin-only.
- **Public routes (complete list):** `/health`, `/auth/status` (two booleans), `/auth/login`,
  `/auth/bootstrap`. Enforced by `test_every_other_route_requires_auth_when_stub_is_off`.
- **Roles:** viewer (chat, read documents in own groups, own history); editor (+ upload, delete,
  ACL changes within own groups); admin (everything, including users, setup, models, audit,
  Compatibility Lab). Last enabled admin cannot be demoted or disabled.
- **ACLs:** enforced in the Qdrant query filter for dense and sparse search, on listing, download,
  delete and ACL change. ACL changes rewrite the payload of every indexed chunk immediately.
  Uploads can neither version nor dedupe against documents the uploader cannot see (fixes a
  cross-group overwrite/disclosure bug found in the Phase 1 code).
- **Audit:** append-only table (DB triggers reject UPDATE/DELETE on SQLite and Postgres). Questions
  are stored as a 16-hex SHA-256 prefix plus length unless `AIRGAP_AUDIT_STORE_QUESTIONS=1`.
  Retention/purge is **not implemented** (would need a privileged maintenance path).
- **Rate limits:** per-user token bucket (30/min) and concurrency (2), global concurrency 8,
  in-process. Multi-replica deployments need a shared limiter (gap).
