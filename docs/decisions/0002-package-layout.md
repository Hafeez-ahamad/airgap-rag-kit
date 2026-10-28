# ADR-0002: Backend package layout

Status: accepted (2026-10-28)

Spec §12 lists `backend/app`, `backend/capacity`, `backend/retrieval` and so on as top-level
directories. Top-level packages named `app` or `retrieval` would collide with other installed
distributions and make imports ambiguous. **Decision: the same module names live under a single
package, `backend/airgap/`:**

| Spec path | Actual path |
|---|---|
| `backend/app/` | `backend/airgap/api/` |
| `backend/ingestion/` | `backend/airgap/ingestion/` |
| `backend/retrieval/` | `backend/airgap/retrieval/` |
| `backend/llm_backends/` | `backend/airgap/llm_backends/` |
| `backend/capacity/` | `backend/airgap/capacity/` (Phase 2) |
| `backend/models_manager/` | `backend/airgap/models_manager/` (Phase 2b) |
| `backend/cli/` | `backend/airgap/cli/` |
| `backend/tests/` | `backend/tests/` |

Shared domain types and ports (interfaces) are in `airgap/core/`. Fakes are in
`airgap/testing/`; they ship in the package so that other packages' tests can use them too.
