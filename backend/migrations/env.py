"""Alembic environment. Database URL from AIRGAP_DATABASE_URL."""

from __future__ import annotations

from alembic import context

from airgap.config import Settings
from airgap.db.models import Base
from airgap.db.session import make_engine

target_metadata = Base.metadata


def _url() -> str:
    """Explicit URL from the caller (native profile) wins over AIRGAP_DATABASE_URL."""
    return context.config.get_main_option("sqlalchemy.url") or Settings().resolved_database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
