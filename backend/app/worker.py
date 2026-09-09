"""The worker process.

`rq worker --url …` builds its own Redis connection from that URL and takes no
say in how it behaves. That is how this container came to die seven times an
hour: a worker sits blocked on a read for minutes at a time, the container
network reclaims a connection nobody appears to be using, and RQ exits with
"connection timeout, quitting". Redis logged nothing, because from its side
nothing went wrong.

Starting the worker from here means it uses `queueing.redis_connection()` — the
same connection the API enqueues through, with keepalives and retries — instead
of a second, weaker one built from a command line.

It also names itself after the container. RQ stopped populating `hostname` on
workers read back from Redis, so a health check that filtered on it found
nothing and reported a perfectly busy worker as unhealthy; a name we choose is
one both sides can agree on.

    python -m app.worker              # the queues below
    python -m app.worker reports      # or just these
"""
from __future__ import annotations

import os
import socket
import sys

from . import observability
from .credential_crypto import validate_credential_encryption_config
from .queueing import redis_connection, validate_queue_config
from .runtime import log


def _is_deployed() -> bool:
    """Staging and production must have every secret; a laptop may not."""
    return os.getenv("APP_ENV", "development").lower() in {"staging", "production"}

#: The queues this process consumes. Order is priority: a stuck integration
#: should not hold up the daily report.
DEFAULT_QUEUES = ("notifications", "autopilot", "integrations", "reports")

#: How long Redis keeps this worker's registration without a heartbeat. RQ
#: beats at a third of it, so the health check can call a worker dead after
#: this long with confidence rather than by guesswork.
WORKER_TTL_SECONDS = 120


def worker_name() -> str:
    """Stable within a container, distinct between them."""
    return socket.gethostname()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    queues = tuple(argv) if argv else DEFAULT_QUEUES

    # The API called this at import and the worker never did, so every failing
    # job was invisible to error reporting - which is the half of the system
    # nobody is watching a screen for.
    observability.initialize()

    # Fail closed, here, rather than three minutes later inside a job. On
    # Railway the worker is a separate service with its own variables, so it is
    # entirely possible to deploy one that reaches the right queue and holds a
    # different encryption keyring than the API - and the symptom is a sync that
    # fails to decrypt a Shopify token, which reads as the store disconnecting.
    validate_credential_encryption_config(required=_is_deployed())
    validate_queue_config(required=_is_deployed())

    from rq import Queue, Worker

    connection = redis_connection()
    worker = Worker(
        [Queue(name, connection=connection) for name in queues],
        connection=connection,
        name=worker_name(),
        worker_ttl=WORKER_TTL_SECONDS,
    )

    log.info("Worker %s listening on %s", worker.name, ", ".join(queues))
    # A dead connection is worth one restart, not a crash loop: the container's
    # restart policy brings it back, and the queue keeps the work meanwhile.
    worker.work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
