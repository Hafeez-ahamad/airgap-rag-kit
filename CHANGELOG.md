# Changelog

All notable changes are recorded here. Versions follow SemVer once a release exists.

## [Unreleased]
### Changed
- Repository housekeeping: removed AI-assistant configuration and session-log files, renamed the
  build prompt to `docs/REQUIREMENTS.md`, and rewrote the README with architecture diagrams,
  layout, configuration and usage.

### Added
- Phase 1 (in progress): upload validation, parsers (PDF, DOCX, TXT, MD, CSV, XLSX), OCR fallback,
  structure-aware chunking, dedupe/versioning, hybrid retrieval (dense + BM25, RRF), reranking,
  confidence gate, citation verification, grounded and general modes.
