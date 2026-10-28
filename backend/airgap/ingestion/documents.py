"""Document lifecycle: upload (dedupe / version), list, delete (ADR-0005)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from airgap.core.ports import BlobStore, Clock, JobQueue, VectorStore
from airgap.core.types import DEFAULT_GROUP, DocStatus
from airgap.db.models import Document, DocumentVersion, new_id
from airgap.ingestion.validate import UploadRejected, validate_upload


class Outcome(StrEnum):
    QUEUED = "queued"  # new document
    NEW_VERSION = "new_version"  # same name, changed content
    UNCHANGED = "unchanged"  # same name, same content: no-op
    DUPLICATE = "duplicate"  # same content already stored under another name: no-op
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class UploadResult:
    filename: str
    outcome: Outcome
    doc_id: str | None = None
    version: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_bytes: int
    max_uncompressed: int


def job_key(doc_id: str, version: int) -> str:
    return f"{doc_id}:{version}"


class DocumentService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        blobs: BlobStore,
        queue: JobQueue,
        store: VectorStore,
        clock: Clock,
        limits: UploadLimits,
    ) -> None:
        self._sessions, self._blobs, self._queue = sessions, blobs, queue
        self._store, self._clock, self._limits = store, clock, limits

    def upload(self, files: Sequence[tuple[str, bytes]], user_id: str) -> list[UploadResult]:
        return [self._upload_one(name, data, user_id) for name, data in files]

    def _upload_one(self, filename: str, data: bytes, user_id: str) -> UploadResult:
        try:
            upload = validate_upload(
                filename,
                data,
                max_bytes=self._limits.max_bytes,
                max_uncompressed=self._limits.max_uncompressed,
            )
        except UploadRejected as exc:
            return UploadResult(filename, Outcome.REJECTED, message=str(exc))
        sha = hashlib.sha256(upload.data).hexdigest()
        now = self._clock.now()
        with self._sessions.begin() as db:
            existing = db.scalar(select(Document).where(Document.name == upload.name))
            if existing is not None:
                if existing.sha256 == sha:
                    return UploadResult(
                        upload.name, Outcome.UNCHANGED, existing.id, existing.current_version
                    )
                doc, outcome = existing, Outcome.NEW_VERSION
                doc.current_version += 1
            else:
                twin = db.scalar(
                    select(Document).where(
                        Document.sha256 == sha, Document.status != DocStatus.FAILED.value
                    )
                )
                if twin is not None:
                    return UploadResult(
                        upload.name,
                        Outcome.DUPLICATE,
                        twin.id,
                        twin.current_version,
                        f"identical content already stored as {twin.name}",
                    )
                doc = Document(
                    id=new_id(),
                    name=upload.name,
                    file_type=upload.file_type.value,
                    current_version=1,
                    acl_groups=[DEFAULT_GROUP],
                    uploaded_by=user_id,
                    created_at=now,
                )
                outcome = Outcome.QUEUED
                db.add(doc)
            doc.sha256 = sha
            doc.size_bytes = len(upload.data)
            doc.file_type = upload.file_type.value
            doc.status = DocStatus.QUEUED.value
            doc.error = None
            doc.updated_at = now
            leaf = upload.name.rsplit("/", 1)[-1]
            key = self._blobs.put(doc.id, doc.current_version, leaf, upload.data)
            db.add(
                DocumentVersion(
                    document_id=doc.id,
                    version=doc.current_version,
                    sha256=sha,
                    blob_key=key,
                    size_bytes=len(upload.data),
                    created_at=now,
                )
            )
            doc_id, version = doc.id, doc.current_version
        self._queue.enqueue(job_key(doc_id, version))
        return UploadResult(upload.name, outcome, doc_id, version)

    def list(self) -> list[Document]:
        with self._sessions() as db:
            return list(db.scalars(select(Document).order_by(Document.name)))

    def get(self, doc_id: str) -> Document | None:
        with self._sessions() as db:
            return db.get(Document, doc_id)

    def current_blob(self, doc_id: str) -> tuple[Document, bytes] | None:
        with self._sessions() as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                return None
            version = next(v for v in doc.versions if v.version == doc.current_version)
            return doc, self._blobs.get(version.blob_key)

    def delete(self, doc_id: str) -> bool:
        """Remove the document, every version's file, and all dense + sparse entries."""
        with self._sessions.begin() as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                return False
            self._store.delete_document(doc_id)
            self._blobs.delete_document(doc_id)
            db.delete(doc)
        return True
