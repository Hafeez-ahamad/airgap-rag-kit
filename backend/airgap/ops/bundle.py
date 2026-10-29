"""Offline bundle manifest: create, sign (build machine), verify (air-gapped target). ADR-0011.

Layout:
  <bundle>/MANIFEST.json      {"kind": "airgap-bundle", "format": 1, "files": [...]}
  <bundle>/MANIFEST.json.sig  raw Ed25519 signature over the exact MANIFEST.json bytes
  <bundle>/...                images/*.tar, models/..., licences/..., wheels/...

Verification order: signature (if a public key is given) -> manifest schema and safe paths ->
no unlisted files -> size and SHA-256 of every file. Any failure rejects the whole bundle;
nothing is loaded before verification passes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

MANIFEST = "MANIFEST.json"
SIGNATURE = "MANIFEST.json.sig"


class BundleError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _payload_files(root: Path) -> list[Path]:
    return sorted(
        f
        for f in root.rglob("*")
        if f.is_file() and f.relative_to(root).as_posix() not in {MANIFEST, SIGNATURE}
    )


def create_manifest(root: Path, profile: str, extra: dict[str, Any] | None = None) -> Path:
    files = [
        {"path": f.relative_to(root).as_posix(), "bytes": f.stat().st_size, "sha256": _sha256(f)}
        for f in _payload_files(root)
    ]
    manifest = {
        "kind": "airgap-bundle",
        "format": 1,
        "profile": profile,
        "created_at": datetime.now(UTC).isoformat(),
        "files": files,
        **(extra or {}),
    }
    path = root / MANIFEST
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return path


def generate_keypair(private_out: Path, public_out: Path) -> None:
    """Build machine only. The private key never goes into the bundle or the repository."""
    key = Ed25519PrivateKey.generate()
    private_out.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_out.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def sign_manifest(root: Path, private_key_pem: Path) -> Path:
    key = serialization.load_pem_private_key(private_key_pem.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise BundleError("signing key must be Ed25519")
    sig = root / SIGNATURE
    sig.write_bytes(key.sign((root / MANIFEST).read_bytes()))
    return sig


def _safe(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts and "\\" not in path


def verify_bundle(root: Path, public_key_pem: Path | None) -> dict[str, Any]:
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise BundleError("MANIFEST.json missing")
    raw = manifest_path.read_bytes()
    if public_key_pem is not None:
        key = serialization.load_pem_public_key(public_key_pem.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise BundleError("public key must be Ed25519")
        sig = root / SIGNATURE
        if not sig.is_file():
            raise BundleError("signature missing")
        try:
            key.verify(sig.read_bytes(), raw)
        except InvalidSignature as exc:
            raise BundleError("signature does not match the manifest (tampered?)") from exc
    try:
        manifest: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BundleError("manifest is not valid JSON") from exc
    if manifest.get("kind") != "airgap-bundle" or manifest.get("format") != 1:
        raise BundleError("unsupported bundle format")
    entries = manifest.get("files", [])
    listed: dict[str, dict[str, Any]] = {}
    for e in entries:
        if not _safe(str(e.get("path", ""))):
            raise BundleError(f"unsafe path in manifest: {e.get('path')!r}")
        listed[e["path"]] = e
    present = {f.relative_to(root).as_posix() for f in _payload_files(root)}
    if present - set(listed):
        raise BundleError(f"unlisted files in bundle: {sorted(present - set(listed))[:5]}")
    if set(listed) - present:
        raise BundleError(f"files missing from bundle: {sorted(set(listed) - present)[:5]}")
    for path, e in listed.items():
        f = root / path
        if f.stat().st_size != e["bytes"]:
            raise BundleError(f"size mismatch: {path}")
        if _sha256(f) != e["sha256"]:
            raise BundleError(f"checksum mismatch: {path}")
    return manifest
