# ADR-0009: Native (lean) deployment profile

Status: accepted (2026-10-29). The maintainer may reverse; see "Decision needed".

## Context
The compose stack keeps eight services resident (Postgres, Qdrant, Valkey, nginx, api, worker,
model service, Ollama) inside a WSL2/Docker VM. On the 7.4 GB development laptop it exhausted
memory and left the Docker engine returning HTTP 500 (docs/IMPLEMENTATION_AUDIT.md §4). The
product requirements requires a lean profile for constrained devices and forbids requiring Docker/WSL
everywhere.

## Decision
Add `AIRGAP_PROFILE=native`: one Python process (`airgap serve`) with
- SQLite (migrated by Alembic at startup) instead of Postgres,
- embedded Qdrant (qdrant-client local mode, on disk) instead of the Qdrant server; the same
  `QdrantStore` adapter, so dense + sparse search and the ACL filter are identical,
- an in-process thread queue and a single ingestion worker thread instead of Valkey + worker,
- the embedder and reranker loaded lazily in-process (same loader code as the model service),
- the host's Ollama on 127.0.0.1,
- the built UI served by the same process with the CSP the compose nginx sets,
- listening on 127.0.0.1 unless configured otherwise.

The compose profile stays for servers. The planner picks native unless the host is Linux, has
16 GiB+ RAM and a healthy Docker engine.

## Consequences
- REQUIREMENTS §3 reserves SQLite for tests; the native profile deviates on purpose.
- Single process: one writer, no horizontal scaling; rate limits are in-process (correct here).
- Embedded Qdrant holds a lock; backup refuses while the app runs (tested).
- Measured on this laptop: the native API without retrieval models loaded uses far less memory
  than the compose stack; the per-service figure in the planner (0.6 GiB) is still an estimate.

## Decision needed (maintainer)
Keep SQLite in the native profile (recommended), or require Postgres everywhere.
