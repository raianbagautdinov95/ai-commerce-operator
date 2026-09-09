"""Database URL compatibility helpers."""
from __future__ import annotations


def normalize_sqlalchemy_url(url: str) -> str:
    """Select the installed psycopg v3 driver for provider Postgres URLs."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url
