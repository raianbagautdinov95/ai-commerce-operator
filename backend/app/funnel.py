"""The public funnel, from our side of the wire.

    python -m app.funnel              # last 7 days, by day and source
    python -m app.funnel --days 30
    python -m app.funnel --by source  # totals per source only

Three numbers per source and day: visitors who reached /try, visitors who
pressed EVALUATE, and accounts created. The first two come from
`public_funnel_events`; the third from `users`, which carries no source (an
account is made on the sign-in page, and a person who came from an ad on
Monday may sign in from a bookmark on Thursday), so it is shown per day
without attribution rather than attributed by guesswork.

Visitors are counted, not requests: a visitor is the same keyed hash for one
day, so somebody who reloads the page five times is one visit and somebody
who evaluates three products is one evaluator.
"""
from __future__ import annotations

import argparse
import datetime as dt

from sqlalchemy import func, select

from .db import models
from .db.session import SessionLocal


def funnel(db, *, days: int) -> list[dict]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ev = models.PublicFunnelEvent
    day = func.date(ev.created_at).label("day")
    rows = db.execute(
        select(day, ev.source, ev.kind, func.count(func.distinct(ev.visitor)))
        .where(ev.created_at >= since)
        .group_by(day, ev.source, ev.kind)
    ).all()
    table: dict[tuple[str, str], dict] = {}
    for d, source, kind, visitors in rows:
        key = (str(d), source or "(direct)")
        cell = table.setdefault(key, {"day": key[0], "source": key[1], "visits": 0, "evaluations": 0})
        if kind == "visit":
            cell["visits"] = visitors
        elif kind == "evaluate":
            cell["evaluations"] = visitors

    signups = db.execute(
        select(func.date(models.User.created_at), func.count())
        .where(models.User.created_at >= since)
        .group_by(func.date(models.User.created_at))
    ).all()
    for d, n in signups:
        key = (str(d), "(signups, any source)")
        table[key] = {"day": key[0], "source": key[1], "visits": 0, "evaluations": 0, "signups": n}
    return sorted(table.values(), key=lambda r: (r["day"], r["source"]))


def _print(rows: list[dict], *, by: str) -> None:
    if by == "source":
        totals: dict[str, dict] = {}
        for r in rows:
            t = totals.setdefault(r["source"], {"visits": 0, "evaluations": 0, "signups": 0})
            t["visits"] += r["visits"]; t["evaluations"] += r["evaluations"]
            t["signups"] += r.get("signups", 0)
        print(f"{'source':28} {'visits':>7} {'evaluated':>10} {'signups':>8}")
        for source, t in sorted(totals.items()):
            print(f"{source:28} {t['visits']:7d} {t['evaluations']:10d} {t['signups']:8d}")
        return
    print(f"{'day':11} {'source':28} {'visits':>7} {'evaluated':>10} {'signups':>8}")
    for r in rows:
        print(f"{r['day']:11} {r['source']:28} {r['visits']:7d} {r['evaluations']:10d} "
              f"{r.get('signups', 0):8d}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--by", choices=("day", "source"), default="day")
    args = parser.parse_args()
    with SessionLocal() as db:
        _print(funnel(db, days=args.days), by=args.by)


if __name__ == "__main__":
    main()
