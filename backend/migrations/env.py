from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations need the owner; the application must not have it.
#
# 0016 creates `aco_app` — a role that is deliberately NOSUPERUSER and
# NOBYPASSRLS, because row-level security never constrains a superuser. So the
# API and the worker connect as that role while migrations, which create roles
# and alter tables, need the owner.
#
# On Docker Compose those are two services with two DATABASE_URLs and the
# distinction takes care of itself. On a platform where the migration runs
# inside the API service — Railway's pre-deploy command, for instance — the
# process inherits the API's environment, which is the restricted role, and
# `alembic upgrade` fails on the first GRANT. MIGRATION_DATABASE_URL exists for
# exactly that case and is ignored everywhere else.
database_url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
