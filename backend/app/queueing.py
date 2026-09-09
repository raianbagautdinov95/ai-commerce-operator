"""Redis/RQ boundary for durable background work."""
from __future__ import annotations

import os


def queue_enabled() -> bool:
    return os.getenv("QUEUE_ENABLED", "false").lower() == "true"


def validate_queue_config(*, required: bool = False) -> None:
    if required and not queue_enabled():
        raise RuntimeError("QUEUE_ENABLED=true is required for production background work.")
    if queue_enabled() and not os.getenv("REDIS_URL"):
        raise RuntimeError("REDIS_URL is required when the background queue is enabled.")


#: A worker spends almost all its time blocked on a read, which is exactly the
#: shape of connection that middleboxes drop: a NAT or a container network sees
#: no packets for minutes and reclaims the entry. Nothing is logged at either
#: end — Redis reported no errors at all while a worker here died seven times an
#: hour with "connection timeout, quitting". Keepalives make the connection
#: visibly alive to whatever is counting, and a retry survives the drop that
#: happens anyway.
SOCKET_KEEPALIVE_SECONDS = 30
SOCKET_TIMEOUT_SECONDS = 120


def redis_connection():
    from redis import Redis
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry as ConnectionRetry

    validate_queue_config()
    return Redis.from_url(
        os.environ["REDIS_URL"],
        decode_responses=False,
        socket_keepalive=True,
        socket_keepalive_options={},   # the platform defaults, tuned by health_check_interval
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=10,
        # Ask Redis whether the connection is still real before trusting it. A
        # blocked read cannot notice a dead socket on its own.
        health_check_interval=SOCKET_KEEPALIVE_SECONDS,
        retry=ConnectionRetry(ExponentialBackoff(base=1, cap=10), retries=5),
        retry_on_timeout=True,
    )


def require_worker(queue_name: str) -> None:
    """Fail fast instead of accepting work that no live worker can process."""
    if not workers_ready({queue_name}):
        raise RuntimeError(f'No active worker for the "{queue_name}" queue.')


def enqueue_autopilot_scan(*, job_id: str, payload: dict, tenant_id: str, actor_id: str) -> str:
    require_worker("autopilot")
    from rq import Queue, Retry
    queue = Queue("autopilot", connection=redis_connection(), default_timeout=900)
    job = queue.enqueue(
        "app.tasks.run_autopilot_scan",
        kwargs={"payload": payload, "tenant_id": tenant_id, "actor_id": actor_id,
                "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id, "operation": "autopilot.scan"},
        retry=Retry(max=3, interval=[10, 60, 300]),
        result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def enqueue_amazon_sync(*, job_id: str, tenant_id: str, actor_id: str, region: str) -> str:
    require_worker("integrations")
    from rq import Queue, Retry
    queue = Queue("integrations", connection=redis_connection(), default_timeout=1800)
    job = queue.enqueue(
        "app.tasks.run_amazon_sync",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "region": region,
                "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id, "operation": "amazon.sync"},
        retry=Retry(max=3, interval=[30, 120, 600]),
        result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def enqueue_amazon_sales_report(*, job_id: str, tenant_id: str, actor_id: str,
                                region: str, marketplace_id: str, days: int) -> str:
    require_worker("reports")
    from rq import Queue, Retry
    queue = Queue("reports", connection=redis_connection(), default_timeout=1200)
    job = queue.enqueue(
        "app.tasks.run_amazon_sales_report",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "region": region,
                "marketplace_id": marketplace_id, "days": days,
                "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id,
              "operation": "amazon.sales_report"},
        retry=Retry(max=3, interval=[60, 120, 300]),
        result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def enqueue_amazon_finances_sync(*, job_id: str, tenant_id: str, actor_id: str,
                                 region: str, marketplace_id: str, days: int) -> str:
    require_worker("reports")
    from rq import Queue, Retry
    queue = Queue("reports", connection=redis_connection(), default_timeout=1200)
    job = queue.enqueue(
        "app.tasks.run_amazon_finances_sync",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "region": region,
                "marketplace_id": marketplace_id, "days": days,
                "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id,
              "operation": "amazon.finances_sync"},
        retry=Retry(max=3, interval=[60, 120, 300]), result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def enqueue_shopify_sync(*, job_id: str, tenant_id: str, actor_id: str,
                         channel_id: str, days: int) -> str:
    require_worker("integrations")
    from rq import Queue, Retry
    queue = Queue("integrations", connection=redis_connection(), default_timeout=1200)
    job = queue.enqueue(
        "app.tasks.run_shopify_sync",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "channel_id": channel_id,
                "days": days, "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id, "operation": "shopify.sync"},
        retry=Retry(max=3, interval=[30, 120, 600]), result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def enqueue_woocommerce_sync(*, job_id: str, tenant_id: str, actor_id: str,
                             channel_id: str, days: int) -> str:
    require_worker("integrations")
    from rq import Queue, Retry
    queue = Queue("integrations", connection=redis_connection(), default_timeout=1200)
    job = queue.enqueue(
        "app.tasks.run_woocommerce_sync",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "channel_id": channel_id,
                "days": days, "idempotency_record_id": job_id}, job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id,
              "operation": "woocommerce.sync"},
        retry=Retry(max=3, interval=[30, 120, 600]), result_ttl=86400,
        failure_ttl=604800,
    )
    return job.id


def job_status(job_id: str, *, tenant_id: str) -> dict | None:
    from rq.exceptions import NoSuchJobError
    from rq.job import Job
    try:
        job = Job.fetch(job_id, connection=redis_connection())
    except NoSuchJobError:
        return None
    if job.meta.get("tenant_id") != tenant_id:
        return None
    status = job.get_status(refresh=True)
    result = job.result if status == "finished" and isinstance(job.result, dict) else None
    return {"job_id": job.id, "status": status, "result": result}


def redis_ready() -> bool:
    if not queue_enabled():
        return True
    return bool(redis_connection().ping())


def workers_ready(required_queues: set[str] | None = None) -> bool:
    """Return true only when every required queue has a live RQ worker."""
    if not queue_enabled():
        return True
    from rq import Worker

    required = required_queues or {
        name.strip() for name in os.getenv(
            "RQ_REQUIRED_QUEUES", "autopilot,integrations,reports"
        ).split(",") if name.strip()
    }
    available: set[str] = set()
    for worker in Worker.all(connection=redis_connection()):
        if str(worker.get_state()).lower() not in {"idle", "busy", "started"}:
            continue
        available.update(queue.name for queue in worker.queues)
    return required.issubset(available)


def enqueue_ads_scan(*, job_id: str, tenant_id: str, actor_id: str, region: str,
                     days: int, profile_id: str | None) -> str:
    from rq import Queue, Retry
    require_worker("integrations")
    queue = Queue("integrations", connection=redis_connection(), default_timeout=1800)
    job = queue.enqueue(
        "app.tasks.run_ads_scan",
        kwargs={"tenant_id": tenant_id, "actor_id": actor_id, "region": region,
                "days": days, "profile_id": profile_id,
                "idempotency_record_id": job_id},
        job_id=job_id,
        meta={"tenant_id": tenant_id, "actor_id": actor_id, "operation": "ads.scan"},
        retry=Retry(max=3, interval=[60, 300, 900]),
        result_ttl=86400, failure_ttl=604800,
    )
    return job.id


def enqueue_notification(*, receipt_id: str, tenant_id: str, kind: str,
                         context: dict) -> str:
    """Hand one email to the worker. Deliberately not sent inside the Stripe
    webhook: Stripe wants an answer in seconds and an SMTP round trip is not
    something to make it wait for."""
    from rq import Queue, Retry
    queue = Queue("notifications", connection=redis_connection(), default_timeout=120)
    job = queue.enqueue(
        "app.tasks.send_subscription_email",
        kwargs={"receipt_id": receipt_id, "tenant_id": tenant_id, "kind": kind,
                "context": context},
        job_id=f"email:{receipt_id}",
        meta={"tenant_id": tenant_id, "operation": f"email.{kind}"},
        # Widening gaps: a provider that is briefly unreachable recovers, and a
        # tight retry loop only turns one outage into a rate limit.
        retry=Retry(max=4, interval=[30, 120, 600, 1800]),
        result_ttl=86400, failure_ttl=604800,
    )
    return job.id
