# ADR-0008: Phase 1 dev-only user stub

Status: accepted (2026-10-28, approved by the maintainer as resolution A5)

Real authentication arrives in Phase 3. Phase 1 still needs a user identity for per-user
history and feedback.

- `AIRGAP_DEV_AUTH=1` enables a stub. The user id comes from the `X-Dev-User` header
  (`[a-z0-9_-]{1,64}`), defaulting to `dev`. All stub users belong to the `default` group.
- With `AIRGAP_DEV_AUTH` unset, every route except `/health` returns **401**. There is no
  silent fallback.
- Settings validation refuses to start if `AIRGAP_DEV_AUTH=1` and `AIRGAP_ENV=production`.
- Phase 3 replaces the stub's dependency (`current_user`) with session auth. Routes don't
  change, because they already depend on `current_user`.
