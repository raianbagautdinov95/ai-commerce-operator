"""
Tests for worker health.

A health check that lies in either direction is worse than none: a false
unhealthy gets a working container restarted forever, and a false healthy hides
a queue nobody is consuming.

Both failures have now happened here. The check used to filter every
registration by `hostname`; RQ stopped populating that field on workers read
back from Redis, the filter matched nothing, and a container consuming three
queues was reported dead. It now looks its own worker up by name and judges it
by whether the heartbeat is recent — presence alone would call a worker that
died holding its key perfectly healthy.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt

import pytest

from app.worker_health import STALE_HEARTBEAT_SECONDS, check


def _beat(seconds_ago: float) -> bytes:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)
    return stamp.isoformat().replace("+00:00", "Z").encode()


class FakeRedis:
    """Answers the two things the check actually asks."""

    def __init__(self, *, reachable=True, registrations=None, explode_on_read=False):
        self._reachable = reachable
        self._registrations = registrations or {}
        self._explode = explode_on_read

    def ping(self):
        if not self._reachable:
            raise ConnectionError("Connection refused")
        return True

    def hgetall(self, key):
        if self._explode:
            raise RuntimeError("protocol error")
        return self._registrations.get(key, {})


@pytest.fixture
def redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")


def test_a_worker_beating_recently_is_healthy(redis_url):
    conn = FakeRedis(registrations={"rq:worker:worker-1": {b"last_heartbeat": _beat(5)}})
    healthy, reason = check(connection=conn, hostname="worker-1")
    assert healthy
    assert "heartbeat" in reason


def test_an_unreachable_queue_is_unhealthy(redis_url):
    healthy, reason = check(connection=FakeRedis(reachable=False), hostname="worker-1")
    assert not healthy
    assert "Queue unreachable" in reason


def test_another_containers_worker_does_not_count(redis_url):
    """Redis answering, and somebody else consuming, says nothing about us."""
    conn = FakeRedis(registrations={"rq:worker:some-other-host": {b"last_heartbeat": _beat(5)}})
    healthy, reason = check(connection=conn, hostname="worker-1")
    assert not healthy
    assert "has not registered" in reason


def test_a_worker_that_stopped_beating_is_not_healthy(redis_url):
    """The failure that presence alone would miss: the process is gone but its
    key outlived it."""
    conn = FakeRedis(registrations={
        "rq:worker:worker-1": {b"last_heartbeat": _beat(STALE_HEARTBEAT_SECONDS + 60)}})
    healthy, reason = check(connection=conn, hostname="worker-1")
    assert not healthy
    assert "Last heartbeat" in reason


def test_a_registration_without_a_heartbeat_is_not_healthy(redis_url):
    conn = FakeRedis(registrations={"rq:worker:worker-1": {b"birth": b"whenever"}})
    healthy, reason = check(connection=conn, hostname="worker-1")
    assert not healthy
    assert "never reported a heartbeat" in reason


def test_an_unparseable_heartbeat_is_reported_not_assumed(redis_url):
    conn = FakeRedis(registrations={"rq:worker:worker-1": {b"last_heartbeat": b"nonsense"}})
    healthy, reason = check(connection=conn, hostname="worker-1")
    assert not healthy
    assert "cannot be read" in reason


def test_a_missing_redis_url_is_unhealthy_not_an_exception(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    healthy, reason = check(connection=FakeRedis())
    assert not healthy
    assert "REDIS_URL is not set" in reason


def test_an_unreadable_registration_is_reported_rather_than_crashing(redis_url):
    """A health check that raises is a health check that cannot report."""
    healthy, reason = check(connection=FakeRedis(explode_on_read=True), hostname="worker-1")
    assert not healthy
    assert "Could not read the worker registration" in reason


def test_the_worker_names_itself_the_way_the_check_looks_it_up(redis_url):
    """The two halves have to agree, or the check is back to finding nothing."""
    import socket
    from app import worker, worker_health

    assert worker.worker_name() == socket.gethostname()
    # The check reads rq:worker:<hostname>; the worker registers under that name.
    conn = FakeRedis(registrations={
        f"rq:worker:{worker.worker_name()}": {b"last_heartbeat": _beat(1)}})
    healthy, _ = worker_health.check(connection=conn)
    assert healthy


def test_the_stale_window_outlasts_the_heartbeat_interval():
    """RQ beats at a third of the TTL. A window shorter than the TTL would call
    a busy worker dead between beats."""
    from app import worker

    assert STALE_HEARTBEAT_SECONDS > worker.WORKER_TTL_SECONDS


def test_worker_ttl_is_applied_when_the_worker_is_created(monkeypatch):
    """Setting an attribute after construction is too late: RQ derives its
    dequeue and connection timeouts in the constructor."""
    from app import worker

    captured = {}

    class Queue:
        def __init__(self, name, connection=None):
            self.name = name

    class Worker:
        def __init__(self, queues, **kwargs):
            captured.update(kwargs)
            self.name = "test-worker"

        def work(self, **kwargs):
            return False

    monkeypatch.setattr(worker, "redis_connection", lambda: object())
    monkeypatch.setitem(sys.modules, "rq", type("rq", (), {"Queue": Queue, "Worker": Worker}))

    assert worker.main([]) == 0
    assert captured["worker_ttl"] == worker.WORKER_TTL_SECONDS


def test_the_worker_refuses_to_start_a_deployment_without_its_secrets(monkeypatch):
    """On Railway the worker is a separate service with its own variables, so it
    is entirely possible to deploy one that reaches the right queue and holds a
    different encryption keyring than the API. The symptom would be a sync that
    cannot decrypt a Shopify token, which reads as the store disconnecting.

    Failing at startup turns that into a deploy that does not come up.
    """
    import pytest

    from app import worker

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("CREDENTIAL_ACTIVE_KEY_VERSION", raising=False)

    with pytest.raises(RuntimeError):
        worker.main([])


def test_a_laptop_worker_still_starts_without_production_secrets(monkeypatch):
    """The same check must not make local development impossible."""
    from app import worker

    captured = {}

    class Queue:
        def __init__(self, name, connection=None):
            self.name = name

    class Worker:
        def __init__(self, queues, **kwargs):
            captured.update(kwargs)
            self.name = "test-worker"

        def work(self, **kwargs):
            return False

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setattr(worker, "redis_connection", lambda: object())
    monkeypatch.setitem(sys.modules, "rq", type("rq", (), {"Queue": Queue, "Worker": Worker}))

    assert worker.main([]) == 0


def test_railway_starts_the_worker_through_the_module():
    """`rq worker --url` is how this worker died seven times an hour: it builds
    its own Redis connection from a command line, with no keepalive, no health
    check interval and no retry. It also skips app/worker.py entirely, and with
    it error reporting and the keyring check.

    The Compose file was fixed and this one was not, which is the kind of
    difference that only shows up in production.
    """
    import pathlib

    # The per-service toml files were replaced by one IaC definition; the claim
    # this test makes did not change with them.
    config = pathlib.Path(__file__).resolve().parents[2] / ".railway" / "railway.ts"
    text = config.read_text(encoding="utf-8")
    assert 'startCommand: "python -m app.worker"' in text
    assert "rq worker --url" not in text
