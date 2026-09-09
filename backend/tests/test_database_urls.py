from app.db.urls import normalize_sqlalchemy_url


def test_normalizes_postgresql_provider_url_for_psycopg_v3():
    assert normalize_sqlalchemy_url("postgresql://user:pass@db/name") == (
        "postgresql+psycopg://user:pass@db/name"
    )


def test_normalizes_legacy_postgres_provider_url_for_psycopg_v3():
    assert normalize_sqlalchemy_url("postgres://user:pass@db/name") == (
        "postgresql+psycopg://user:pass@db/name"
    )


def test_keeps_explicit_driver_and_sqlite_urls_unchanged():
    explicit = "postgresql+psycopg://user:pass@db/name"
    sqlite = "sqlite:///./aco_dev.db"

    assert normalize_sqlalchemy_url(explicit) == explicit
    assert normalize_sqlalchemy_url(sqlite) == sqlite
