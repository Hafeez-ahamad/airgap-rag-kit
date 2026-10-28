import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airgap.api.app import create_app
from airgap.api.services import Services
from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.chat.routing import SingleModelRouter
from airgap.config import Settings
from airgap.core.ports import GenerationOptions
from airgap.testing.fakes import FakeLLM
from tests.conftest import Kit
from tests.fixture_files import docx_file


def _services(kit: Kit, *, dev_auth: bool = True, llm: FakeLLM | None = None) -> Services:
    return Services(
        settings=Settings(dev_auth=dev_auth, max_upload_bytes=1_000_000),
        documents=kit.documents,
        chat=ChatStore(kit.sessions, kit.clock),
        orchestrator=Orchestrator(
            kit.retriever, SingleModelRouter(llm or FakeLLM()), GenerationOptions()
        ),
        health_checks={"database": lambda: True, "llm": lambda: True},
    )


@pytest.fixture
def client(kit: Kit) -> TestClient:
    return TestClient(create_app(_services(kit)))


def _events(resp: Any) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _upload(client: TestClient, kit: Kit, files: list[tuple[str, bytes]], user: str = "alice"):  # type: ignore[no-untyped-def]
    resp = client.post(
        "/documents",
        files=[("files", (name, data)) for name, data in files],
        data={"paths": [name for name, _ in files]},
        headers={"X-Dev-User": user},
    )
    kit.drain()
    return resp


def test_health_is_public(kit: Kit) -> None:
    resp = TestClient(create_app(_services(kit, dev_auth=False))).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_every_other_route_requires_auth_when_stub_is_off(kit: Kit) -> None:
    app = create_app(_services(kit, dev_auth=False))
    client = TestClient(app)
    checked = 0
    for path, ops in app.openapi()["paths"].items():
        if path == "/health":
            continue
        for method in ops:
            resp = client.request(method.upper(), path.replace("{doc_id}", "x"))
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
            checked += 1
    assert checked >= 7


def test_upload_folder_reports_per_file_outcomes(client: TestClient, kit: Kit) -> None:
    resp = _upload(
        client,
        kit,
        [
            ("handbook/leave.docx", docx_file("Leave", ["Staff get 24 days of paid leave."])),
            ("handbook/huge.txt", b"a" * 1_000_001),
            ("handbook/tool.exe", b"MZ\x90"),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["accepted"], body["rejected"]) == (1, 2)
    assert [r["outcome"] for r in body["results"]] == ["queued", "rejected", "rejected"]
    assert "larger than" in body["results"][1]["message"]
    docs = client.get("/documents").json()
    assert [(d["name"], d["status"]) for d in docs] == [("handbook/leave.docx", "ready")]


def test_chat_streams_cited_answer_then_history_and_feedback(client: TestClient, kit: Kit) -> None:
    _upload(client, kit, [("leave.txt", b"Staff get 24 days of paid leave per year.")])
    resp = client.post(
        "/chat", json={"question": "How many days of paid leave do staff get?", "mode": "grounded"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _events(resp)
    assert events[0]["type"] == "meta"
    final = events[-1]
    assert final["type"] == "final" and not final["refused"]
    assert "24 days" in final["answer"]
    cite = final["citations"][0]
    assert cite["valid"] and cite["doc_name"] == "leave.txt"

    # source link works
    src = client.get(f"/documents/{cite['doc_id']}/file")
    assert src.status_code == 200 and b"24 days" in src.content
    assert src.headers["x-content-type-options"] == "nosniff"

    convs = client.get("/chat/history").json()
    assert len(convs) == 1
    msgs = client.get("/chat/history", params={"conversation_id": final["conversation_id"]}).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]

    fb = client.post("/feedback", json={"message_id": final["message_id"], "rating": "wrong"})
    assert fb.status_code == 201


def test_unanswerable_question_streams_refusal(client: TestClient, kit: Kit) -> None:
    _upload(client, kit, [("leave.txt", b"Staff get 24 days of paid leave per year.")])
    final = _events(client.post("/chat", json={"question": "Who won the 1998 World Cup?"}))[-1]
    assert final["refused"] and final["answer"] == "I could not find this in your documents."


def test_history_and_feedback_are_isolated_per_user(client: TestClient, kit: Kit) -> None:
    _upload(client, kit, [("leave.txt", b"Staff get 24 days of paid leave per year.")])
    final = _events(
        client.post("/chat", json={"question": "paid leave days"}, headers={"X-Dev-User": "alice"})
    )[-1]
    bob = {"X-Dev-User": "bob"}
    assert client.get("/chat/history", headers=bob).json() == []
    resp = client.get(
        "/chat/history", params={"conversation_id": final["conversation_id"]}, headers=bob
    )
    assert resp.status_code == 404
    resp = client.post(
        "/chat",
        json={"question": "and then?", "conversation_id": final["conversation_id"]},
        headers=bob,
    )
    assert resp.status_code == 404
    fb = client.post(
        "/feedback", json={"message_id": final["message_id"], "rating": "up"}, headers=bob
    )
    assert fb.status_code == 404


def test_delete_document(client: TestClient, kit: Kit) -> None:
    body = _upload(client, kit, [("x.txt", b"Disposable content about gaskets.")]).json()
    doc_id = body["results"][0]["doc_id"]
    assert client.delete(f"/documents/{doc_id}").status_code == 204
    assert client.get("/documents").json() == []
    assert client.delete(f"/documents/{doc_id}").status_code == 404


def test_coding_mode_rejected_and_bad_input_validated(client: TestClient) -> None:
    assert client.post("/chat", json={"question": "x", "mode": "coding"}).status_code == 400
    assert client.post("/chat", json={"question": "", "mode": "grounded"}).status_code == 422
    assert client.post("/chat", json={"question": "x", "mode": "nope"}).status_code == 422
    assert client.get("/chat/history", headers={"X-Dev-User": "Bad User!"}).status_code == 401


def test_production_refuses_dev_auth() -> None:
    with pytest.raises(ValueError, match="must not be enabled"):
        Settings(env="production", dev_auth=True)


def test_openapi_lists_phase1_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ["/documents", "/documents/{doc_id}", "/chat", "/chat/history", "/feedback"]:
        assert path in paths
    assert client.get("/docs").status_code == 404  # no CDN-backed Swagger UI
