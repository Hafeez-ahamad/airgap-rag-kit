"""Resumable, verified file downloads (setup time only; never used by the running kit).

- Writes to ``<file>.part`` and resumes with an HTTP Range request after an interruption.
- Verifies size and SHA-256 when the catalogue pins them; a mismatch deletes the partial file
  and fails loudly (corrupt or tampered artifact).
- One download per destination at a time (in-process lock); already-verified files are skipped.
- Cancellation via ``threading.Event``; retries with exponential backoff.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

CHUNK = 1024 * 1024
Progress = Callable[[int, int | None], None]  # bytes done, total (None if unknown)


class DownloadError(RuntimeError):
    pass


class ChecksumMismatch(DownloadError):
    pass


class Cancelled(DownloadError):
    pass


@dataclass(frozen=True, slots=True)
class FileSpec:
    url: str
    dest: Path
    size: int | None = None
    sha256: str | None = None


_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path.resolve(), threading.Lock())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def verify_file(spec: FileSpec) -> bool:
    if not spec.dest.is_file():
        return False
    if spec.size is not None and spec.dest.stat().st_size != spec.size:
        return False
    return spec.sha256 is None or sha256_file(spec.dest) == spec.sha256


class Downloader:
    def __init__(
        self,
        client: httpx.Client | None = None,
        retries: int = 3,
        backoff_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(60, connect=10), follow_redirects=True
        )
        self._retries, self._backoff, self._sleep = retries, backoff_s, sleep

    def fetch(
        self,
        spec: FileSpec,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
    ) -> str:
        """Download (or resume) ``spec``. Returns ``"present"`` or ``"downloaded"``."""
        with _lock_for(spec.dest):
            if verify_file(spec):
                return "present"
            spec.dest.parent.mkdir(parents=True, exist_ok=True)
            last: Exception | None = None
            for attempt in range(self._retries + 1):
                try:
                    self._attempt(spec, progress, cancel)
                    return "downloaded"
                except (Cancelled, ChecksumMismatch):
                    raise
                except (httpx.HTTPError, DownloadError) as exc:
                    last = exc
                    if attempt < self._retries:
                        self._sleep(self._backoff * 2**attempt)
            raise DownloadError(f"download failed after retries: {type(last).__name__}") from last

    def _attempt(
        self, spec: FileSpec, progress: Progress | None, cancel: threading.Event | None
    ) -> None:
        part = spec.dest.with_name(spec.dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        if spec.size is not None and have > spec.size:
            part.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with self._client.stream("GET", spec.url, headers=headers) as resp:
            if resp.status_code == 416 and spec.size is not None and have == spec.size:
                pass  # partial file is already complete
            elif resp.status_code == 200:
                have = 0  # server ignored Range: start over
                self._write(resp, part, "wb", have, spec, progress, cancel)
            elif resp.status_code == 206:
                self._write(resp, part, "ab", have, spec, progress, cancel)
            else:
                raise DownloadError(f"HTTP {resp.status_code}")
        if spec.size is not None and part.stat().st_size != spec.size:
            raise DownloadError("incomplete download")
        if spec.sha256 is not None and sha256_file(part) != spec.sha256:
            part.unlink()
            raise ChecksumMismatch(f"SHA-256 mismatch for {spec.dest.name}; partial file removed")
        part.replace(spec.dest)

    @staticmethod
    def _write(
        resp: httpx.Response,
        part: Path,
        mode: str,
        have: int,
        spec: FileSpec,
        progress: Progress | None,
        cancel: threading.Event | None,
    ) -> None:
        done = have
        with part.open(mode) as fh:
            for block in resp.iter_bytes(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("download cancelled; partial file kept for resume")
                fh.write(block)
                done += len(block)
                if progress:
                    progress(done, spec.size)
