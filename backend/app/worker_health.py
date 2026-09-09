"""
Health for a worker, which is not a web server.

The API and the worker share one image, so the worker inherited the API's
HEALTHCHECK and spent its life reporting unhealthy while working perfectly: it
was being asked for an HTTP response by a process that serves none. Under any
orchestrator that restarts unhealthy containers, that is a restart loop.

A worker is healthy when it can reach the queue and has registered itself on it.
Both halves matter: Redis answering says nothing about whether this container is
actually consuming, and a registration in Redis we cannot read is not evidence
of anything either.

    python -m app.worker_health

Exit 0 means healthy. Anything else is not, with the reason on stderr.
"""
from __future__ import annotations

import os
import socket
import sys


def check(*, connection=None, hostname: str | None = None) -> tuple[bool, str]:
    """Return (healthy, reason). Never raises: a health check that crashes is a
    health check that cannot report."""
    url = os.getenv("REDIS_URL", "")
    if not url:
        return False, "REDIS_URL is not set, so there is no queue to be healthy about."

    try:
        if connection is None:
            from redis import Redis
            connection = Redis.from_url(url, socket_connect_timeout=3,
                                        socket_timeout=3)
        connection.ping()
    except Exception as exc:
        return False, f"Queue unreachable: {type(exc).__name__}: {exc}"

    # Look this container's own worker up by name rather than filtering every
    # registration on `hostname`. RQ stopped populating that field on workers
    # read back from Redis — the registration hash came back holding only
    # `last_heartbeat` — so the filter matched nothing and a busy worker was
    # called unhealthy. Under an orchestrator that restarts unhealthy
    # containers, that is the restart loop this module exists to prevent.
    #
    # `app.worker` names the worker after the container, so both halves agree
    # on what to look for.
    host = hostname or socket.gethostname()
    try:
        raw = connection.hgetall(f"rq:worker:{host}")
    except Exception as exc:
        return False, f"Could not read the worker registration: {type(exc).__name__}: {exc}"

    if not raw:
        return False, ("Connected to the queue, but this container has not registered "
                       "as a worker on it.")

    # A registration that stopped beating is a worker that died holding its own
    # key. Presence alone would call that healthy.
    beat = raw.get(b"last_heartbeat") or raw.get("last_heartbeat")
    if not beat:
        return False, "Registered as a worker but has never reported a heartbeat."

    age = _heartbeat_age_seconds(beat)
    if age is None:
        return False, "Registered as a worker but its heartbeat cannot be read."
    if age > STALE_HEARTBEAT_SECONDS:
        return False, (f"Last heartbeat was {int(age)}s ago, over the "
                       f"{STALE_HEARTBEAT_SECONDS}s a live worker should need.")
    return True, f"Consuming the queue; last heartbeat {int(age)}s ago"


#: RQ beats at a third of the worker TTL, so anything past the TTL itself means
#: the process is gone rather than merely busy.
STALE_HEARTBEAT_SECONDS = 150


def _heartbeat_age_seconds(raw) -> float | None:
    """Seconds since the worker last said it was alive, or None if unreadable."""
    import datetime as dt

    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()


def main(argv: list[str] | None = None) -> int:
    healthy, reason = check()
    print(reason, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
