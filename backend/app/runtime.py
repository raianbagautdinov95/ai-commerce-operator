"""
Process-wide state: the logger, request metrics, and access-log redaction.

Kept out of `main.py` so every router can reach it without importing the
application, and out of the routers so there is exactly one of each.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

from . import redaction

log = logging.getLogger("aco")

# Severity ordering shared by the report and dashboard views.
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

# In-process request counters behind /metrics. A lock, not two atomics, because
# the count and the duration must move together.
_metrics_lock = threading.Lock()
_request_counts: dict[tuple[str, str, int], int] = defaultdict(int)
_request_duration_seconds: dict[tuple[str, str], float] = defaultdict(float)


class _OAuthQueryRedactionFilter(logging.Filter):
    """Keep credentials out of the log, whichever endpoint carried them.

    This used to match one hard-coded path, the Amazon callback, so Shopify's
    OAuth callback wrote its `code` and `hmac` into the access log untouched for
    as long as the integration existed. Matching a path meant every new endpoint
    started out leaking and stayed that way until somebody noticed.

    It now redacts by parameter name wherever it appears — in the access log's
    request path, and in the message of anything else that gets logged, since a
    traceback from an HTTP client carries the URL it called.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            args = [redaction.redact(value) if isinstance(value, str) else value
                    for value in record.args]
            record.args = tuple(args)
        if isinstance(record.msg, str):
            record.msg = redaction.redact(record.msg)
        return True
