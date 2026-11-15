"""Alembic environment.

Migrations run synchronously against psycopg while the application uses
asyncpg, so the URL is translated here rather than duplicated in alembic.ini.
Taking it from Settings means there is exactly one source of truth for the
database location, and no credential in a tracked file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import *  # noqa: F403  (import for metadata side effects)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# A caller (tests, or `alembic -x url=...`) may set the URL explicitly;
# otherwise it comes from application settings so that migrations and the app
# can never disagree about which database they target.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().alembic_url)


def include_object(obj: object, name: str | None, type_: str, *_: object) -> bool:
    """Keep LangGraph's own checkpoint tables out of our migrations.

    The library owns and migrates them via its ``setup()`` call; autogenerate
    would otherwise try to drop them on every revision.
    """
    if type_ != "table" or name is None:
        return True
    return not (name.startswith(("checkpoint", "langgraph")) or name == "store")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
