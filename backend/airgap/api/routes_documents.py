"""POST/GET/DELETE /documents and GET /documents/{id}/file."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from airgap.api.deps import ServicesDep, UserDep
from airgap.ingestion.documents import Outcome

router = APIRouter(prefix="/documents", tags=["documents"])

_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


class UploadItem(BaseModel):
    filename: str
    outcome: Outcome
    doc_id: str | None
    version: int | None
    message: str | None


class UploadResponse(BaseModel):
    results: list[UploadItem]
    accepted: int
    rejected: int


class DocumentOut(BaseModel):
    id: str
    name: str
    file_type: str
    version: int
    status: str
    error: str | None
    size_bytes: int
    chunk_count: int
    ocr_pages: list[int]
    updated_at: datetime


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)  # one extra byte detects oversize without reading all
    await upload.close()
    return data


@router.post("", response_model=UploadResponse)
async def upload_documents(
    services: ServicesDep,
    user: UserDep,
    files: Annotated[list[UploadFile], File(description="One or more files")],
    paths: Annotated[
        list[str] | None, Form(description="Optional relative paths (folder upload), same order")
    ] = None,
) -> UploadResponse:
    settings = services.settings
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"at most {settings.max_files_per_request} files per request",
        )
    if paths is not None and len(paths) != len(files):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "paths must match files")
    batch: list[tuple[str, bytes]] = []
    for i, upload in enumerate(files):
        name = paths[i] if paths else (upload.filename or "")
        batch.append((name, await _read_limited(upload, settings.max_upload_bytes)))
    results = services.documents.upload(batch, user.id)
    items = [UploadItem(**asdict(r)) for r in results]
    rejected = sum(r.outcome is Outcome.REJECTED for r in results)
    return UploadResponse(results=items, accepted=len(items) - rejected, rejected=rejected)


@router.get("", response_model=list[DocumentOut])
def list_documents(services: ServicesDep, user: UserDep) -> list[DocumentOut]:
    return [
        DocumentOut(
            id=d.id,
            name=d.name,
            file_type=d.file_type,
            version=d.current_version,
            status=d.status,
            error=d.error,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            ocr_pages=list(d.ocr_pages),
            updated_at=d.updated_at,
        )
        for d in services.documents.list()
        if set(d.acl_groups) & set(user.groups)
    ]


@router.get("/{doc_id}/file")
def download_document(doc_id: str, services: ServicesDep, user: UserDep) -> Response:
    found = services.documents.current_blob(doc_id)
    if found is None or not set(found[0].acl_groups) & set(user.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    doc, data = found
    leaf = doc.name.rsplit("/", 1)[-1]
    return Response(
        data,
        media_type=_MEDIA_TYPES.get(doc.file_type, "application/octet-stream"),
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{_quote(leaf)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: str, services: ServicesDep, user: UserDep) -> Response:
    doc = services.documents.get(doc_id)
    if doc is None or not set(doc.acl_groups) & set(user.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    services.documents.delete(doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _quote(name: str) -> str:
    return quote(name, safe="")
