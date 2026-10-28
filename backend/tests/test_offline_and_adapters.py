import socket
from pathlib import Path

import httpx
import pytest

from airgap.adapters.storage import FileBlobStore
from airgap.core.ports import GenerationOptions
from airgap.core.types import ChatMessage, Role
from airgap.llm_backends.ollama import LLMUnavailable, OllamaBackend
from airgap.offline import EgressBlocked, _guarded_connect, is_internal


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("172.18.0.5", True),
        ("192.168.1.10", True),
        ("::1", True),
        ("fe80::1%eth0", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("2606:4700:4700::1111", False),
        ("huggingface.co", False),
    ],
)
def test_internal_address_classification(host: str, internal: bool) -> None:
    assert is_internal(host) is internal


def test_guard_blocks_public_ip_before_any_packet_is_sent() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(EgressBlocked):
            _guarded_connect(sock, ("1.1.1.1", 443))
    finally:
        sock.close()


def test_blob_store_round_trip_and_containment(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path)
    key = store.put("doc1", 2, "report.pdf", b"%PDF-data")
    assert store.get(key) == b"%PDF-data"
    with pytest.raises(ValueError, match="escapes"):
        store.get("../outside")
    store.delete_document("doc1")
    assert not (tmp_path / "doc1").exists()


def _ollama(handler: httpx.MockTransport) -> OllamaBackend:
    backend = OllamaBackend("http://ollama:11434", "m")
    backend._client = httpx.Client(base_url="http://ollama:11434", transport=handler)
    return backend


def test_ollama_stream_parses_ndjson() -> None:
    body = (
        b'{"message":{"role":"assistant","content":"Hel"},"done":false}\n'
        b'{"message":{"role":"assistant","content":"lo"},"done":false}\n'
        b'{"message":{"role":"assistant","content":""},"done":true}\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        sent = request.read()
        assert b'"num_ctx": 2048' in sent or b'"num_ctx":2048' in sent
        return httpx.Response(200, content=body)

    out = _ollama(httpx.MockTransport(handler)).stream(
        [ChatMessage(Role.USER, "hi")], GenerationOptions(num_ctx=2048)
    )
    assert "".join(out) == "Hello"


def test_ollama_errors_become_llm_unavailable() -> None:
    backend = _ollama(httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(LLMUnavailable):
        backend.complete([ChatMessage(Role.USER, "hi")], GenerationOptions())
    with pytest.raises(LLMUnavailable):
        list(backend.stream([ChatMessage(Role.USER, "hi")], GenerationOptions()))
