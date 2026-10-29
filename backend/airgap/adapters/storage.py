"""Filesystem blob store, Redis job queue and system clock."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import redis

from airgap.ingestion.validate import sanitize_component

INGEST_QUEUE = "airgap:ingest"


class FileBlobStore:
    """Layout: <root>/<doc_id>/<version>/<filename>. Keys are relative paths."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("blob key escapes the store root")
        return path

    def put(self, doc_id: str, version: int, filename: str, data: bytes) -> str:
        key = f"{sanitize_component(doc_id)}/{version}/{sanitize_component(filename)}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_document(self, doc_id: str) -> None:
        path = self._path(sanitize_component(doc_id))
        if path.exists():
            shutil.rmtree(path)


class RedisQueue:
    def __init__(self, url: str, name: str = INGEST_QUEUE) -> None:
        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._name = name

    def enqueue(self, job: str) -> None:
        self._redis.rpush(self._name, job)

    def dequeue(self, timeout_s: float) -> str | None:
        item = self._redis.blpop([self._name], timeout=timeout_s)
        if item is None:
            return None
        _, value = item
        return str(value)

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except redis.RedisError:
            return False


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ThreadQueue:
    """In-process job queue for the native profile (single API process)."""

    def __init__(self) -> None:
        import queue

        self._q: queue.Queue[str] = queue.Queue()

    def enqueue(self, job: str) -> None:
        self._q.put(job)

    def dequeue(self, timeout_s: float) -> str | None:
        import queue

        try:
            return self._q.get(timeout=timeout_s) if timeout_s > 0 else self._q.get_nowait()
        except queue.Empty:
            return None

    def ping(self) -> bool:
        return True
