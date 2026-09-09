"""
Database engine + session factory.

DATABASE_URL controls the backend:
  - unset            -> local SQLite file (zero-setup dev / tests)
  - postgresql+psycopg://...  -> Postgres (production; see .env.example, docker-compose)

Tables are created from the ORM models via `init_db()` for dev convenience.
In production the canonical schema is `schema.sql` / migrations.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .urls import normalize_sqlalchemy_url

DATABASE_URL = normalize_sqlalchemy_url(
    os.getenv("DATABASE_URL", "sqlite:///./aco_dev.db")
)

# SQLite + FastAPI's threadpool needs check_same_thread disabled.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# A pooled connection outlives the server on the other end of it. When Postgres
# restarts — maintenance, a failover, a container being replaced — every
# connection in the pool is already dead, and without pre-ping the pool hands
# them out anyway: one 500 per stale connection, on every restart. Pre-ping costs
# a trivial round trip per checkout and removes the whole class of failure.
# pool_recycle retires connections before a database or proxy idle timeout can.
_pool_options = {} if DATABASE_URL.startswith("sqlite") else {
    "pool_pre_ping": True,
    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True,
                       **_pool_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

_IS_POSTGRES = not DATABASE_URL.startswith("sqlite")


@event.listens_for(SessionLocal, "after_begin")
def _declare_tenant(session, transaction, connection) -> None:
    """Tell PostgreSQL whose data this transaction may touch.

    The row-level policies key on `app.tenant_id`, and this is what sets it. It
    hangs off the session rather than sitting in each route on purpose: a
    guarantee you have to remember to invoke is the kind of guarantee this exists
    to replace.

    Left unset, `current_setting('app.tenant_id', true)` is NULL and every policy
    fails, so a transaction that never declared a tenant reads nothing. That is
    deliberate — the alternative to failing closed here is reading everything.
    """
    if not _IS_POSTGRES:
        return
    from ..security import current_principal

    principal = current_principal()
    tenant = principal.tenant_id if principal else None
    if tenant:
        connection.exec_driver_sql("SELECT set_config('app.tenant_id', %s, true)", (tenant,))


def declare_tenant(session: Session, tenant_id) -> None:
    """Adopt a tenant mid-transaction, for work that discovers it.

    A webhook arrives knowing a shop domain and an OAuth callback a state hash;
    both learn which tenant they belong to only after a lookup. Once they know,
    they say so here, and the rest of the transaction is scoped like any other.
    """
    # Tests and maintenance code may use an independently bound SQLite session
    # even when the process-wide application engine points at PostgreSQL.
    if session.get_bind().dialect.name != "postgresql" or not tenant_id:
        return
    session.connection().exec_driver_sql(
        "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))


def init_db() -> None:
    """Create any missing tables, where creating them is ours to do.

    This exists for the zero-setup path: a developer with a fresh SQLite file
    and no migration run. Anywhere Alembic is in charge it must do nothing at
    all, and keying that on APP_ENV was not enough — the local Docker stack
    declares itself `development` because it has no HTTPS, while its PostgreSQL
    is migrated like any other.

    The consequence was not theoretical. The application role is deliberately
    unprivileged, so the moment the models described a table the migrations had
    not created yet, `create_all` tried to issue DDL, was refused, and the API
    would not start. A schema that is managed elsewhere is not this function's
    business, and `alembic_version` is how it says so.
    """
    if os.getenv("APP_ENV", "development").lower() == "production":
        return  # Production schema is managed exclusively by Alembic.
    if inspect(engine).has_table("alembic_version"):
        return  # Migrated database: whatever is missing is a migration to run.
    Base.metadata.create_all(engine)
    _upgrade_legacy_sqlite()


def _upgrade_legacy_sqlite() -> None:
    """Keep the zero-setup dev DB usable after tenant ownership was added.

    Production schema changes are applied with the reviewed SQL migration. This
    narrow compatibility upgrade exists only for legacy local SQLite files.
    """
    if engine.dialect.name != "sqlite":
        return
    columns = {column["name"] for column in inspect(engine).get_columns("product_evaluations")}
    if "store_id" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE product_evaluations ADD COLUMN store_id CHAR(32)"))
        connection.execute(text(
            "UPDATE product_evaluations SET store_id = ("
            "SELECT stores.id FROM stores "
            "WHERE stores.user_id = product_evaluations.user_id "
            "ORDER BY stores.created_at ASC LIMIT 1)"
        ))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
