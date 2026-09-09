# Railway variables, per service

**Four** services share a database, a queue and a keyring — api, worker,
frontend and the daily **scheduler** — and each is deployed separately with its
own variables. That separation is the whole risk: a worker holding a different
`CREDENTIAL_ENCRYPTION_KEYS` than the API reaches the right queue, picks up the
right job and cannot decrypt the token it needs — and the symptom on screen is a
store that appears to have disconnected.

The scheduler is the one that is easiest to forget, because nothing looks broken
without it. It is declared in `.railway/railway.ts`: a cron running `python -m app.daily`,
which warns about ending trials, queues the read-only Shopify sync and prices
every measurement window that has closed. Deploy it with the same variables as
the worker plus `PUBLIC_APP_URL`.

So the worker now refuses to start in `staging` or `production` without the
keyring and the queue configured. A deploy that would have failed quietly at the
first sync fails loudly at boot instead.

## Legend

- **api / worker / frontend** — which services need it.
- Frontend variables are **build** arguments. Next.js compiles them into the
  JavaScript the browser downloads, so changing one means a rebuild, not a
  restart, and a wrong one ships.

## Required

| Variable | api | worker | frontend | Notes |
|---|:--:|:--:|:--:|---|
| `APP_ENV` | ✓ | ✓ | | `production`. Below `staging`, every safety check is skipped by design. |
| `APP_DATABASE_URL` | ✓ | ✓ | | Railway shared variable mapped to `DATABASE_URL`. Use the `aco_app` role, not the owner — see `ops/RAILWAY.md`. |
| `REDIS_URL` | ✓ | ✓ | | Must be identical, or the API enqueues where nobody listens. |
| `QUEUE_ENABLED` | ✓ | ✓ | | `true`. The API refuses to start in production without it. |
| `AUTH_ENABLED` | ✓ | ✓ | | `true`. With it off every request is the same tenant. |
| `JWT_SECRET` | ✓ | | | ≥32 chars. Rotating it signs everyone out everywhere. |
| `CREDENTIAL_ENCRYPTION_KEYS` | ✓ | ✓ | | JSON keyring. **Identical** on both, or the worker cannot read what the API wrote. |
| `CREDENTIAL_ACTIVE_KEY_VERSION` | ✓ | ✓ | | Identical on both. |
| `CORS_ALLOWED_ORIGINS` | ✓ | | | Only `https://app.<domain>`. No localhost, no tunnel. |
| `FRONTEND_API_BASE` | | | ✓ | `https://api.<domain>`. The build refuses http, localhost and tunnels. |
| `PUBLIC_DOMAIN` | ✓ | | ✓ | The frontend's own hostname. |
| `SHOPIFY_CLIENT_ID` | ✓ | ✓ | | From the Partner Dashboard. |
| `SHOPIFY_CLIENT_SECRET` | ✓ | ✓ | | Signs callbacks, webhooks and the token exchange. See `ops/SECRETS.md`. |
| `SHOPIFY_REDIRECT_URI` | ✓ | | | `https://api.<domain>/api/integrations/shopify/callback` |
| `SHOPIFY_WEBHOOK_URI` | ✓ | ✓ | | `https://api.<domain>/api/webhooks/shopify` |
| `SHOPIFY_API_VERSION` | ✓ | ✓ | | e.g. `2026-01`. |
| `SHOPIFY_SCOPES` | ✓ | | | `read_products,read_orders,read_inventory`. |
| `LEGAL_ENTITY`, `LEGAL_ADDRESS`, `PRIVACY_CONTACT` | ✓ | | | Preflight blocks without them; the privacy policy names them. |

## Required before anybody can sign in

Sign-in is by emailed code. Without these the service starts, the screens load,
and nobody — including you — can get past the sign-in page. They were missing
from this document while the gate blocked on them, which is the wrong way round.

| Variable | api | worker | scheduler | Notes |
|---|:--:|:--:|:--:|---|
| `RESEND_API_KEY` | ✓ | ✓ | ✓ | The mail provider. The worker sends; the API and scheduler queue. |
| `LOGIN_EMAIL_FROM` | ✓ | ✓ | ✓ | A verified sender at your own domain. An unverified one is silently dropped. |
| `PUBLIC_APP_URL` | ✓ | | ✓ | `https://app.<domain>`. It becomes the link inside every email, and an email outlives the deploy that sent it — never a tunnel. |
| `SUPPORT_EMAIL` | ✓ | | ✓ | The address a person actually reads. Shown on billing, pricing and setup. |
| `SUPPORT_RESPONSE_TIME` | ✓ | | | What you promise, in words: "same business day". A promise nobody made is worse than none. |

Prove it once rather than assuming it:

```bash
python -m app.preflight --live --send-test-email
```

## Required before charging anybody

Optional for a free pilot: billing returns 503 and everything else works. **Not**
optional for a paid one — `pilot_gate` blocks on every line here, because a
customer who cannot pay, cannot cancel, or lands on a dead page after paying is
a worse outcome than not selling to them.

| Variable | api | Notes |
|---|:--:|---|
| `STRIPE_SECRET_KEY` | ✓ | `sk_test_…` outside production, `sk_live_…` in it. The gate refuses the mismatch: a test key in production takes no money, a live key elsewhere takes real money from a developer pressing buttons. |
| `STRIPE_WEBHOOK_SECRET` | ✓ | The webhook is the only thing that makes a subscription active. Without it every event is refused. |
| `STRIPE_PRICE_OPERATOR` | ✓ | The price id of the one plan. `STRIPE_PRICE_STARTER` and `_SCALE` exist in the code and are not sold in v1. |
| `STRIPE_SUCCESS_URL` | ✓ | Where Stripe returns a customer who paid. https, and still there tomorrow. |
| `STRIPE_CANCEL_URL` | ✓ | Where it returns one who did not. |
| `STRIPE_PORTAL_RETURN_URL` | ✓ | Where the Billing Portal returns them. |
| `STRIPE_API_VERSION` | ✓ | Optional but recommended: pin the version your account already speaks. Unpinned, Stripe answers in whatever version the account is on and moves that on its own schedule. |

## What you are claiming, not what we measured

`pilot_gate` blocks on eight `PILOT_*` variables. Each is an **attestation** — a
claim by whoever set it about something done elsewhere, which nothing here can
verify. Set them only after doing the thing:

`PILOT_SECRETS_ROTATED`, `PILOT_BACKUP_OFFSITE`, `PILOT_BACKUP_RESTORED`,
`PILOT_STRIPE_PRICE_LIVE`, `PILOT_TERMS_PUBLISHED`, `PILOT_EMAIL_DELIVERY`,
`PILOT_PORTAL_OPENS`, `PILOT_SUPPORT_CONTACT`.

Run `python -m app.pilot_gate --live` to see which are outstanding and what each
one is asking of you.

## Strongly recommended

| Variable | api | worker | Notes |
|---|:--:|:--:|---|
| `SENTRY_DSN` | ✓ | ✓ | Set on **both**. The worker is the half nobody watches a screen for. |
| `SENTRY_TRACES_SAMPLE_RATE` | ✓ | ✓ | `0.1` unless you have a reason. |
| `APP_RELEASE` | ✓ | ✓ | Ties an error to a deploy. |
| `ALERT_STALE_SYNC_HOURS` | ✓ | | Default 48. |

## Optional

`GOOGLE_CLIENT_ID` — an alternative sign-in. Email codes work without it.

`PREFLIGHT_EMAIL` — where `--send-test-email` sends. Yours, not a customer's.

`SESSION_TOKEN_DAYS` — default 7. Longer is more convenient and more exposure.

`KEEPA_API_KEY`, `RAPIDAPI_*` — Amazon discovery. Experimental; not needed for a
Shopify customer.

## Domains

Two stable subdomains, and neither may be a tunnel:

- `app.<domain>` → frontend service
- `api.<domain>` → API service

Both must be HTTPS with a certificate Railway manages. The Shopify Partner
Dashboard then needs, on the app's configuration:

    App URL           https://app.<domain>
    Allowed redirect  https://api.<domain>/api/integrations/shopify/callback
    Webhook endpoint  https://api.<domain>/api/webhooks/shopify

`CORS_ALLOWED_ORIGINS` lists `https://app.<domain>` and nothing else. Preflight
blocks any origin that is not https, and blocks a callback pointing at
`trycloudflare.com`, `ngrok` and the rest.

## Start commands and health

| Service | Command | Health |
|---|---|---|
| api | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | `GET /health/ready` |
| worker | `python -m app.worker` | **none** — it serves no HTTP |
| frontend | `next start -p $PORT` | `GET /` |

Giving the worker an HTTP healthcheck is how it ends up in a restart loop while
working perfectly. Its own check is `python -m app.worker_health`, which asks
Redis whether this container is registered and beating.

## Migrations

The `api` resource in `.railway/railway.ts` carries the pre-deploy command
`alembic -c alembic.ini upgrade head`, so migrations run once per deploy, before the new API serves traffic and
before the worker picks up jobs against a schema that has not moved yet. Do not
run them from the worker as well: two services racing the same migration is a
lock, at best.

Verify after deploying, from the API service shell:

    python -m app.preflight --live --no-env-file

`--no-env-file` matters: without it preflight may read a `.env` baked into the
image instead of the variables Railway actually set.

## When something is missing

Nothing starts half-configured. The API raises at startup for auth, database,
queue and encryption; the worker raises for queue and encryption; the frontend
build refuses an API address that is absent, local or ephemeral. A missing
variable is a deploy that does not come up, which is louder and cheaper than one
that comes up wrong.
