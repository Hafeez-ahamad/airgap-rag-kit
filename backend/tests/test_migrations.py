from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from airgap.db.models import Base
from airgap.db.session import make_engine

BACKEND = Path(__file__).resolve().parents[1]


def test_migrations_upgrade_match_models_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setenv("AIRGAP_DATABASE_URL", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations differ: {diff}"
    command.downgrade(cfg, "base")
    engine.dispose()
