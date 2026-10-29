"""Native profile, backup/restore, bundle verification and CLI.

Native-profile tests are MOCK-ADAPTER integration tests (L4): real SQLite + migrations, real
embedded Qdrant on disk, the real in-process worker and HTTP stack; fake embedder, reranker and
LLM. They prove wiring, persistence and recovery, not model quality or real inference.
"""

from __future__ import annotations

import json
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airgap.cli.main import main as cli
from airgap.config import Settings
from airgap.db.models import Document
from airgap.db.session import make_engine, make_session_factory
from airgap.native import create_native_app
from airgap.ops.backup import BackupError, create_backup, restore_backup
from airgap.ops.bundle import (
    BundleError,
    create_manifest,
    generate_keypair,
    sign_manifest,
    verify_bundle,
)
from airgap.testing.fakes import FakeEmbedder, FakeLLM, FakeReranker
from tests.test_setup_lab import FakeAdmin

PW = "native profile password"
DOC = b"# Pump manual\n\nThe P-100 pump must be serviced every 500 operating hours.\n"


def _app(data: Path, ui: Path | None = None) -> TestClient:
    settings = Settings(
        profile="native",
        data_dir=data,
        models_dir=data / "models",
        cookie_secure=False,
        confidence_threshold=0.5,
    )
    app = create_native_app(
        settings,
        ui_dist=ui or data / "no-ui",
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        llm=FakeLLM(),
        admin=FakeAdmin(),
    )
    return TestClient(app)


def _session(c: TestClient, how: str) -> dict[str, str]:
    resp = c.post(f"/api/auth/{how}", json={"username": "owner", "password": PW})
    return {"X-CSRF-Token": resp.json()["csrf_token"]}


def _wait_ready(c: TestClient, h: dict[str, str], n: int = 1, timeout: float = 20) -> list[Any]:
    deadline = time.monotonic() + timeout
    docs: list[Any] = []
    while time.monotonic() < deadline:
        docs = c.get("/api/documents", headers=h).json()
        if len(docs) >= n and all(d["status"] in {"ready", "failed"} for d in docs):
            return docs
        time.sleep(0.1)
    return docs


def _ask(c: TestClient, h: dict[str, str], q: str) -> dict[str, Any]:
    resp = c.post("/api/chat", json={"question": q, "mode": "grounded"}, headers=h)
    return [json.loads(x[6:]) for x in resp.text.splitlines() if x.startswith("data: ")][-1]  # type: ignore[no-any-return]


def test_native_profile_end_to_end_restart_and_recovery(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with _app(data) as c:
        assert c.get("/api/auth/status").json()["needs_bootstrap"]
        h = _session(c, "bootstrap")
        up = c.post(
            "/api/documents",
            files=[("files", ("pump.md", DOC))],
            data={"paths": ["pump.md"]},
            headers=h,
        )
        assert up.json()["accepted"] == 1
        docs = _wait_ready(c, h)
        assert docs[0]["status"] == "ready", docs
        answer = _ask(c, h, "How often must the P-100 pump be serviced?")
        assert not answer["refused"] and answer["citations"][0]["valid"]
        assert _ask(c, h, "Who won the 1998 World Cup?")["refused"]
        health = c.get("/api/health").json()
        assert health["components"]["database"] == "ok"

    # Simulate a crash mid-ingestion: a document left in "embedding".
    engine = make_engine(f"sqlite:///{(data / 'airgap.db').as_posix()}")
    with make_session_factory(engine).begin() as db:
        doc = db.query(Document).one()
        doc.status = "embedding"
    engine.dispose()

    with _app(data) as c:  # restart on the same data directory
        h = _session(c, "login")
        docs = _wait_ready(c, h)
        assert docs[0]["status"] == "ready", "interrupted ingestion must be recovered"
        assert not _ask(c, h, "How often must the P-100 pump be serviced?")["refused"]
        titles = [x["title"] for x in c.get("/api/chat/history", headers=h).json()]
        assert "Who won the 1998 World Cup?" in titles, "history persists across restarts"
        assert len(titles) == 3  # two from before the restart, one after


def test_native_serves_ui_with_security_headers(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>AirGap</title>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    with _app(tmp_path / "data", ui) as c:
        page = c.get("/documents")  # SPA route
        assert page.status_code == 200 and "AirGap" in page.text
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/api/health").status_code == 200


# ---- backup / restore -----------------------------------------------------------------


def _seed(data: Path) -> None:
    with _app(data) as c:
        h = _session(c, "bootstrap")
        c.post(
            "/api/documents",
            files=[("files", ("pump.md", DOC))],
            data={"paths": ["pump.md"]},
            headers=h,
        )
        _wait_ready(c, h)


def test_backup_then_restore_on_clean_install_gives_same_answers(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _seed(src)
    archive = create_backup(src, tmp_path / "b.tar.gz")
    dst = tmp_path / "fresh"
    restore_backup(archive, dst)
    with _app(dst) as c:
        h = _session(c, "login")
        assert [d["name"] for d in c.get("/api/documents", headers=h).json()] == ["pump.md"]
        assert not _ask(c, h, "How often must the P-100 pump be serviced?")["refused"]


def test_restore_refuses_non_empty_target_and_keeps_old_data_with_force(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _seed(src)
    archive = create_backup(src, tmp_path / "b.tar.gz")
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("precious")
    with pytest.raises(BackupError, match="not empty"):
        restore_backup(archive, target)
    restore_backup(archive, target, force=True)
    old = [p for p in tmp_path.iterdir() if p.name.startswith("target.old-")]
    assert len(old) == 1 and (old[0] / "keep.txt").read_text() == "precious"


def test_tampered_backup_is_rejected_before_anything_is_written(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _seed(src)
    archive = create_backup(src, tmp_path / "b.tar.gz")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(archive) as tin, tarfile.open(bad, "w:gz") as tout:
        for m in tin.getmembers():
            f = tin.extractfile(m)
            body = f.read() if f else b""
            if m.name == "data/setup.json" or m.name.endswith("pump.md"):
                body = body + b"tampered"
                m.size = len(body)
            import io

            tout.addfile(m, io.BytesIO(body))
    with pytest.raises(BackupError, match="checksum mismatch"):
        restore_backup(bad, tmp_path / "dst")
    assert not (tmp_path / "dst").exists()


# ---- offline bundle ---------------------------------------------------------------------


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "bundle"
    (root / "models").mkdir(parents=True)
    (root / "models" / "w.gguf").write_bytes(b"weights" * 100)
    (root / "images.tar").write_bytes(b"image")
    create_manifest(root, "lite")
    generate_keypair(tmp_path / "k.pem", tmp_path / "k.pub")
    sign_manifest(root, tmp_path / "k.pem")
    return root, tmp_path / "k.pub"


def test_signed_bundle_verifies(tmp_path: Path) -> None:
    root, pub = _bundle(tmp_path)
    assert len(verify_bundle(root, pub)["files"]) == 2


@pytest.mark.parametrize(
    "attack",
    ["modify_file", "add_file", "remove_file", "edit_manifest", "wrong_key", "no_signature"],
)
def test_tampered_bundle_fails_verification(tmp_path: Path, attack: str) -> None:
    root, pub = _bundle(tmp_path)
    if attack == "modify_file":
        (root / "models" / "w.gguf").write_bytes(b"evil" * 175)
    elif attack == "add_file":
        (root / "models" / "extra.bin").write_bytes(b"x")
    elif attack == "remove_file":
        (root / "images.tar").unlink()
    elif attack == "edit_manifest":
        m = json.loads((root / "MANIFEST.json").read_text())
        m["profile"] = "xl"
        (root / "MANIFEST.json").write_text(json.dumps(m))
    elif attack == "wrong_key":
        generate_keypair(tmp_path / "o.pem", tmp_path / "o.pub")
        pub = tmp_path / "o.pub"
    elif attack == "no_signature":
        (root / "MANIFEST.json.sig").unlink()
    with pytest.raises(BundleError):
        verify_bundle(root, pub)


def test_manifest_with_traversal_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "b"
    root.mkdir()
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "kind": "airgap-bundle",
                "format": 1,
                "files": [{"path": "../etc/passwd", "bytes": 1, "sha256": "0" * 64}],
            }
        )
    )
    with pytest.raises(BundleError, match="unsafe path"):
        verify_bundle(root, None)


# ---- CLI -------------------------------------------------------------------------------


def test_cli_recommend_simulated_profile_is_bannered(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(["recommend", "--profile", "cpu-only-16"]) == 0
    out = capsys.readouterr().out
    assert out.count("SIMULATION - NOT A REAL HARDWARE SCAN") == 2
    assert "Recommended:" in out
    assert cli(["recommend", "--profile", "nope"]) == 2


def test_cli_recommend_custom_spec_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"os": "linux", "arch": "amd64", "ram_total_gib": 64, "disk_free_gib": 500})
    )
    assert cli(["recommend", "--spec", str(spec), "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["simulated"] is True


def test_cli_bundle_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, pub = _bundle(tmp_path)
    assert cli(["bundle", "verify", str(root), "--public-key", str(pub)]) == 0
    (root / "images.tar").write_bytes(b"evil!")
    assert cli(["bundle", "verify", str(root), "--public-key", str(pub)]) == 1
    assert "BUNDLE REJECTED" in capsys.readouterr().err


def test_native_profile_talks_to_host_ollama_not_container_name() -> None:
    assert Settings(profile="native").ollama_url == "http://127.0.0.1:11434"
    assert Settings(profile="compose").ollama_url == "http://ollama:11434"
    custom = Settings(profile="native", ollama_url="http://10.0.0.2:11434")
    assert custom.ollama_url == "http://10.0.0.2:11434"


def test_no_egress_parsers_flag_only_public_addresses() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "eval" / "security" / "no_egress.py"
    spec = importlib.util.spec_from_file_location("no_egress", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    win = (
        "  TCP    127.0.0.1:8000     127.0.0.1:51000    ESTABLISHED     4242\n"
        "  TCP    192.168.1.5:52000  34.117.59.81:443   ESTABLISHED     4242\n"
        "  TCP    0.0.0.0:11434      0.0.0.0:0          LISTENING       77\n"
    )
    rows = mod.parse_connections(win, "Windows")
    assert [(h, p) for h, _, p in rows if mod.is_external(h)] == [("34.117.59.81", 4242)]
    linux = 'ESTAB 0 0 10.0.0.2:40000 [2606:4700::1111]:443 users:(("ollama",pid=77,fd=3))\n'
    rows = mod.parse_connections(linux, "Linux")
    assert rows == [("2606:4700::1111", "ESTAB", 77)] and mod.is_external(rows[0][0])
    assert not mod.is_external("10.1.2.3") and not mod.is_external("::1")


def test_cli_bundle_manifest_keygen_and_verify_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "b"
    (root / "models").mkdir(parents=True)
    (root / "models" / "x.gguf").write_bytes(b"abc")
    assert cli(["bundle", "keygen", str(tmp_path / "k.pem"), str(tmp_path / "k.pub")]) == 0
    assert cli(["bundle", "manifest", str(root), "--key", str(tmp_path / "k.pem")]) == 0
    assert cli(["bundle", "verify", str(root), "--public-key", str(tmp_path / "k.pub")]) == 0
