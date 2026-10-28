# ADR-0005: Document identity, dedupe and versions

Status: accepted (2026-10-28)

- **Identity:** a document is identified by its sanitised logical path, which keeps the folder
  path for folder uploads (for example `manuals/pumps/p100.pdf`). The path is metadata only; it
  carries no ACL meaning in v1.
- **Same path, same SHA-256:** no-op (`unchanged`).
- **Same path, different SHA-256:** new version (`new_version`). The file is stored as version
  N+1 and re-indexed. Once the new version's chunks are written, every *other* version's chunks
  are deleted from Qdrant (dense and sparse vectors live in the same points). The old version
  stays searchable until the new one is ready, so a re-upload never leaves a gap.
- **New path, SHA-256 already stored under another path:** no-op (`duplicate`). The response
  names the existing document.
- **Versions:** the original file of every version is kept in the blob store as history. Only
  the current version is indexed. Delete removes the row, all version files and all chunks.
- **Superseded jobs:** a queued job for version N is skipped if version N+1 already exists.
