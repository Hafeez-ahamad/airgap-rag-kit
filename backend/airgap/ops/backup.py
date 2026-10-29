"""Backup and restore for the native profile's data directory.

Contents: SQLite database (consistent online snapshot), embedded Qdrant directory, original
document files, setup state; plus ``MANIFEST.json`` with a SHA-256 per file.

- Stop the app first: the embedded vector store holds a lock while running; backup refuses
  if it cannot take it (prevents an inconsistent index snapshot).
- Restore verifies every checksum BEFORE touching the target, extracts with tarfile's
  ``data`` filter (no absolute paths, no traversal, no devices), and never deletes existing
  data: with ``force`` the old directory is renamed to ``<dir>.old-<timestamp>``.
- Not encrypted. Store archives on encrypted media or encrypt them with OS tools
  (documented in docs/runbook.md). Contains document contents and chat history.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from airgap import __version__

DB_NAME = "airgap.db"
INCLUDE = ("qdrant", "blobs", "setup.json")


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _check_not_running(data_dir: Path) -> None:
    lock = data_dir / "qdrant" / ".lock"
    if not lock.exists():
        return
    try:
        import portalocker  # qdrant-client's lock implementation

        with lock.open("r+") as fh:
            portalocker.lock(
                fh, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
            )
            portalocker.unlock(fh)
    except ImportError:
        return
    except Exception as exc:
        raise BackupError("the app appears to be running; stop it before backing up") from exc


def create_backup(data_dir: Path, out: Path) -> Path:
    data_dir = data_dir.resolve()
    if not (data_dir / DB_NAME).is_file():
        raise BackupError(f"no database at {data_dir / DB_NAME}")
    _check_not_running(data_dir)
    with tempfile.TemporaryDirectory() as tmp_s:
        stage = Path(tmp_s) / "data"
        stage.mkdir()
        src = sqlite3.connect(str(data_dir / DB_NAME))
        dst = sqlite3.connect(str(stage / DB_NAME))
        with dst:
            src.backup(dst)  # consistent snapshot even if a writer is active
        src.close()
        dst.close()
        for name in INCLUDE:
            p = data_dir / name
            if p.is_dir():
                shutil.copytree(p, stage / name, ignore=shutil.ignore_patterns(".lock"))
            elif p.is_file():
                shutil.copy2(p, stage / name)
        files = sorted(f for f in stage.rglob("*") if f.is_file())
        manifest: dict[str, Any] = {
            "kind": "airgap-backup",
            "format": 1,
            "app_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "files": [
                {
                    "path": f.relative_to(stage).as_posix(),
                    "bytes": f.stat().st_size,
                    "sha256": _sha256(f),
                }
                for f in files
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            raw = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("MANIFEST.json")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
            for f in files:
                tar.add(f, arcname=f"data/{f.relative_to(stage).as_posix()}")
    return out


def restore_backup(archive: Path, data_dir: Path, *, force: bool = False) -> Path:
    data_dir = data_dir.resolve()
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        manifest_path = tmp / "MANIFEST.json"
        if not manifest_path.is_file():
            raise BackupError("not an AirGap backup (no MANIFEST.json)")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "airgap-backup" or manifest.get("format") != 1:
            raise BackupError("unsupported backup format")
        stage = tmp / "data"
        listed = {e["path"] for e in manifest["files"]}
        present = {f.relative_to(stage).as_posix() for f in stage.rglob("*") if f.is_file()}
        if listed != present:
            raise BackupError("archive contents do not match its manifest")
        for entry in manifest["files"]:
            if _sha256(stage / entry["path"]) != entry["sha256"]:
                raise BackupError(f"checksum mismatch: {entry['path']} (archive corrupt?)")
        if data_dir.exists() and any(data_dir.iterdir()):
            if not force:
                raise BackupError(
                    f"{data_dir} is not empty; use --force to replace it "
                    "(the current data is kept as a .old directory)"
                )
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            data_dir.rename(data_dir.with_name(f"{data_dir.name}.old-{stamp}"))
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stage, data_dir)
    return data_dir
