# Production monitoring and alert runbook

## Required signals

- Scrape `/metrics` every 30 seconds from the private monitoring network.
- Probe `/health` for liveness and `/health/ready` before sending traffic.
- Configure Sentry with `SENTRY_DSN`, `APP_ENV`, and an immutable `APP_RELEASE`.
- Keep `send_default_pii` disabled. The application additionally removes request
  bodies, query strings, cookies, authorization headers, webhook signatures,
  email, username, and IP address before an event leaves the process.

## Alerts

Page the on-call owner immediately when readiness fails for 5 minutes, the
5xx ratio exceeds 5% for 5 minutes, Stripe/Shopify webhook failures repeat, or a
restore drill fails. Create a working-hours alert when p95 latency exceeds two
seconds for 15 minutes, Redis queue depth grows continuously for 15 minutes, or
data freshness becomes outdated.

Every alert must include environment, release, request ID, affected route, first
failure time, and a link to the runbook. Never include access tokens, request
bodies, customer email, addresses, or webhook signatures.

## Release check

Before production traffic: verify `/health/ready`, confirm `/api/observability/status`
reports the expected environment/release and Sentry initialized, send a controlled
test event from staging, and confirm the alert reaches the responsible person.


## Standing conditions

`/metrics` and `/health/ready` say whether the process is up. They say nothing
about whether the product is doing its job — a store whose access was revoked
answers 200 all day.

    docker compose exec api python -m app.alerts     # every tenant; exit 1 on critical

Run it on a schedule and let the exit code drive whatever you already page with:

    */15 * * * * docker compose exec -T api python -m app.alerts || notify-send-somehow

With `SENTRY_DSN` set, critical conditions are also captured there. `GET /api/alerts`
returns the same list for a screen or an HTTP probe.

Severity means what it says: **critical** is something a merchant is losing money
or data to right now (revoked access, notifications going nowhere, an unattended
change that could not be proven). **warning** is something to look at today, not
tonight.


## The /health/ready contract

This is the only endpoint a platform should restart a container over, so it is
deliberately narrow: it answers for **this process's ability to serve**, and for
nothing else.

`200 {"status": "ready"}` — the database answered `SELECT 1`, Redis answered a
ping, and a worker is registered for every required queue.

`503 {"detail": "service not ready"}` — one of those three failed. The reason is
in the API log; it is not in the response, because an unauthenticated caller
does not need to know which of a deployment's dependencies is down.

What it deliberately does **not** fail for: a store whose token was revoked, a
stale webhook subscription, a delivery stuck in flight, nothing synced for two
days. Those are conditions a person should see, not reasons to kill a container
that is serving correctly — restarting it fixes none of them and loses the
in-flight requests. They come from `alerts.evaluate` and surface on
`/health/operations`.

For Railway: point the healthcheck at `/health/ready` on the API service. The
worker serves no HTTP at all — give it no healthcheck path; `python -m
app.worker_health` is its check, and it asks Redis whether this container is
registered and beating.

## What is watched, and where it appears

| Condition | Where |
|---|---|
| API exceptions | Sentry, with the event scrubbed (see below) |
| Worker job failures | Sentry — the worker now initialises it too |
| Database or Redis unreachable | `/health/ready` → 503 |
| Shopify access revoked | `/health/operations`, critical |
| Notifications pointing at a dead address | `/health/operations`, critical |
| Signatures being refused | the Shopify screen: `failing_signature` |
| Deliveries stuck in flight | `/health/operations` |
| Idempotency records stuck | `/health/operations` |
| Nothing synced for `ALERT_STALE_SYNC_HOURS` (48h) | `/health/operations` |

## What never reaches Sentry

`send_default_pii=False` covers what the SDK collects by itself. It does nothing
about strings this application composed, and this application composes strings
out of URLs — so the event is scrubbed as well: request body, query string and
cookies dropped whole; `Authorization`, `Cookie` and signature headers filtered;
the user's email, name and IP removed; and the message, the exception text and
every breadcrumb passed through the same redaction as the log, so an OAuth
`code` or an API key inside an error message loses its value.

Order contents and customer identities are never stored in the first place —
only aggregate daily metrics — so there is nothing of that kind to leak.

## Related runbooks

- `ops/RUNNING-LOCALLY.md` — the local stack, and the API address that is
  compiled into the frontend rather than configured on it.
- `ops/SECRETS.md` — what is redacted, what that does not cover, and the order
  to rotate a leaked credential in.
- `ops/backup/README.md` — PostgreSQL backup and the restore drill.
- `ops/TUNNEL.md` — reaching this server from Shopify while developing.
