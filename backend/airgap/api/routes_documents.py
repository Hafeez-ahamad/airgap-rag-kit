"""POST/GET/DELETE /documents, GET /documents/{id}/file, PATCH /documents/{id}/acl.

ACLs: a user sees a document only if they share a group with it; editors may only assign
groups they belong to (admins any). The same filter is applied in retrieval (Qdrant).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from airgap.api.deps import EditorDep, ServicesDep, User, UserDep
from airgap.api.services import Services
from airgap.auth.service import UserRole, ValidationFailed, validate_groups
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
    acl_groups: list[str]
    updated_at: datetime


class AclPatch(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=32)


def _allowed_groups(user: User, requested: list[str] | None) -> list[str]:
    """Groups an upload/ACL change may use: the user's own unless admin."""
    if not requested:
        return [user.groups[0]] if user.groups else []
    try:
        groups = validate_groups(requested)
    except ValidationFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if not user.at_least(UserRole.ADMIN) and not set(groups) <= set(user.groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you can only assign your own groups")
    return groups


def _audit(
    services: Services, user: User, action: str, target: str | None, **detail: object
) -> None:
    if services.audit is not None:
        services.audit.record(user.username or user.id, action, target=target, detail=dict(detail))


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)  # one extra byte detects oversize without reading all
    await upload.close()
    return data


@router.post("", response_model=UploadResponse)
async def upload_documents(
    services: ServicesDep,
    user: EditorDep,
    files: Annotated[list[UploadFile], File(description="One or more files")],
    paths: Annotated[
        list[str] | None, Form(description="Optional relative paths (folder upload), same order")
    ] = None,
    groups: Annotated[
        list[str] | None, Form(description="ACL groups for new documents (default: your first)")
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
    acl = _allowed_groups(user, groups)
    results = services.documents.upload(batch, user.id, acl, user.groups)
    items = [UploadItem(**asdict(r)) for r in results]
    rejected = sum(r.outcome is Outcome.REJECTED for r in results)
    _audit(
        services,
        user,
        "document.upload",
        None,
        groups=acl,
        outcomes=[f"{r.outcome.value}:{r.doc_id}" for r in results],
    )
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
            acl_groups=list(d.acl_groups),
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
def delete_document(doc_id: str, services: ServicesDep, user: EditorDep) -> Response:
    doc = services.documents.get(doc_id)
    if doc is None or not set(doc.acl_groups) & set(user.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    services.documents.delete(doc_id)
    _audit(services, user, "document.delete", doc_id, name=doc.name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{doc_id}/acl", response_model=list[str])
def set_acl(doc_id: str, body: AclPatch, services: ServicesDep, user: EditorDep) -> list[str]:
    doc = services.documents.get(doc_id)
    if doc is None or not set(doc.acl_groups) & set(user.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    groups = _allowed_groups(user, body.groups)
    updated = services.documents.set_acl(doc_id, groups)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    _audit(services, user, "document.acl", doc_id, before=list(doc.acl_groups), after=groups)
    return list(updated.acl_groups)


def _quote(name: str) -> str:
    return quote(name, safe="")
