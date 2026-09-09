"""
What must never be committed, and never shipped.

Two database dumps were committed and then copied into every deployed image,
carrying a user's address and an encrypted Shopify token. Nobody did anything
wrong on purpose: `COPY . .` with no `.dockerignore` takes whatever is in the
directory, and a `.bak` file looks harmless sitting next to the code that made
it.

So this is a test rather than a rule in a document. A rule in a document is read
once; a failing test is read the day somebody repeats the mistake.

The allowances are deliberately narrow. A blanket ban on `*.sql` would hide the
schema and every migration, and a check that blocks ordinary work is a check
somebody deletes.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: SQL this project needs. Anything else ending .sql is a dump until proven
#: otherwise — an export of real rows is exactly what this is looking for.
ALLOWED_SQL = (
    "backend/app/db/schema.sql",
)

#: Extensions that are a dump, a database or a key whatever they are called.
FORBIDDEN_SUFFIXES = (
    ".bak", ".dump", ".sqlite", ".sqlite3", ".db",
    ".pem", ".p12", ".pfx", ".key",
)

#: Archives are only forbidden when they look like data rather than a fixture.
ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".zip")
ARCHIVE_HINTS = ("dump", "backup", "export", "aco_dev")


def _tracked() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                            text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def tracked():
    return _tracked()


def test_no_database_dump_or_key_is_tracked(tracked):
    """The one that already happened."""
    offenders = [path for path in tracked
                 if path.lower().endswith(FORBIDDEN_SUFFIXES)]
    assert offenders == [], (
        "these are dumps, databases or keys and must not be committed: "
        f"{offenders}")


def test_no_sql_export_is_tracked(tracked):
    """Migrations and the schema are fine; an export of rows is not."""
    offenders = [
        path for path in tracked
        if path.lower().endswith(".sql")
        and path not in ALLOWED_SQL
        and "/migrations/" not in path
    ]
    assert offenders == [], f"unexpected .sql files: {offenders}"


def test_the_migrations_are_still_allowed(tracked):
    """The other half: a hygiene check that blocks ordinary work is a check
    somebody deletes."""
    assert any("/migrations/" in path and path.endswith(".sql") for path in tracked)
    assert "backend/app/db/schema.sql" in tracked


def test_no_archive_that_looks_like_data_is_tracked(tracked):
    offenders = [
        path for path in tracked
        if path.lower().endswith(ARCHIVE_SUFFIXES)
        and any(hint in path.lower() for hint in ARCHIVE_HINTS)
    ]
    assert offenders == [], f"archives that look like data: {offenders}"


def test_no_env_file_is_tracked_except_the_example(tracked):
    offenders = [path for path in tracked
                 if os.path.basename(path).startswith(".env")
                 and not path.endswith(".env.example")]
    assert offenders == [], f"environment files must not be committed: {offenders}"


def test_no_private_key_material_is_tracked(tracked):
    """By content as well as by name: a key renamed is still a key."""
    # Assembled rather than written out, because a scanner containing its own
    # search string flags itself — which it did, the first time this ran.
    marker = "-----" + "BEGIN "
    secret_word = "PRIVATE" + " KEY"
    offenders = []
    for path in tracked:
        full = ROOT / path
        try:
            if full.stat().st_size > 2_000_000:
                continue
            head = full.read_bytes()[:4096].decode("utf-8", "ignore")
        except (OSError, ValueError):
            continue
        if marker in head and secret_word in head:
            offenders.append(path)
    assert offenders == [], f"private key material: {offenders}"


def test_the_docker_context_excludes_data_and_secrets():
    """The image is the second copy nobody thinks about. Both dumps and the
    live development database shipped inside it, along with a 145 MB host
    virtualenv that cannot run there."""
    ignore = (ROOT / "backend" / ".dockerignore")
    assert ignore.exists(), "backend/.dockerignore is missing; COPY . . ships everything"
    body = ignore.read_text(encoding="utf-8")
    for rule in ("*.bak", "*.db", ".venv/", ".env"):
        assert rule in body, f"{rule} is not excluded from the image"


def test_gitignore_covers_what_was_committed():
    body = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rule in ("*.bak", "*.dump", "*.sqlite"):
        assert rule in body, f"{rule} is not ignored"


# --- the schema is somebody's job, and only one somebody's --------------------

def test_init_db_leaves_a_migrated_database_alone(tmp_path, monkeypatch):
    """Found by the API refusing to start.

    `init_db` exists for the zero-setup path: a fresh SQLite file and no
    migration run. The local Docker stack calls itself `development` because it
    has no HTTPS, so keying the skip on APP_ENV was not enough — its PostgreSQL
    is migrated like any other, the application role is deliberately
    unprivileged, and the moment the models named a table the migrations had not
    created yet the DDL was refused and the process exited.
    """
    import sqlalchemy as sa

    from app.db import session as db_session

    path = tmp_path / "migrated.sqlite"
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32))"))

    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setenv("APP_ENV", "development")
    db_session.init_db()

    tables = set(sa.inspect(engine).get_table_names())
    assert tables == {"alembic_version"}, (
        "init_db issued DDL against a database Alembic manages")


def test_init_db_still_builds_a_fresh_developer_database(tmp_path, monkeypatch):
    """The other half: a check that blocks ordinary work is a check somebody
    deletes."""
    import sqlalchemy as sa

    from app.db import session as db_session

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setenv("APP_ENV", "development")
    db_session.init_db()

    tables = set(sa.inspect(engine).get_table_names())
    assert "stores" in tables and "product_costs" in tables
