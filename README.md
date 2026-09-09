# AI Commerce Operator

An AI operating system for multichannel commerce — an "AI COO for ecommerce". It finds
products worth selling, sources suppliers, scores the unit economics, plans ads
and inventory, and drafts the launch — keeping a human in the loop for money and
publishing.

> **The rule that makes this a product, not a GPT wrapper:** deterministic Python
> engines compute all money, scores and verdicts; the LLM only *explains* and drafts
> copy. See `docs/ARCHITECTURE.md` and `CLAUDE.md`.

## The workflow

```
Discover  →  Suppliers  →  Product Hunter  →  PPC / Inventory  →  Operator / Autopilot
find what    find cost      score & verdict    optimise ads &       chain agents into a
sells        (real COGS)    + unit economics   stock                launch plan + queue
```

## Modules (all built & tested)

| Module | What it does |
|--------|--------------|
| **Discover** | Finds real candidate products for a niche (live Keepa data) and ranks them by economics, competition and risk. Realism guards: price ceiling, conservative sales projection, estimated COGS & ad cost. Token cache. |
| **Suppliers** | Ranked supplier offers + a suggested landed COGS (closes the cost gap). Pluggable source. |
| **Product Hunter** | Scores a product 0–100 across 8 weighted criteria, hard gates (patent, margin floor), BUY/CAUTION/AVOID verdict, what-if sensitivity, and a unit-economics breakdown. |
| **PPC Analyzer** | Per-keyword ACOS, wasted spend, negative-keyword & bid recommendations, recoverable spend. |
| **Inventory** | Days of cover, reorder point/timing, order quantity, stockout risk. |
| **Daily Report** | Cross-module digest: today's prioritized to-do list + AI morning briefing. |
| **Operator** | 3-agent pipeline: find product → find supplier → re-score with real cost → draft the Amazon listing. |
| **Autopilot** | Agents scan niches into an approval queue, money-gated (BUY / margin / ROI / payback) and ranked by money. Portfolio view: capital to invest, monthly profit, break-even units, payback. Optional scheduler. |
| **Guardrails** | What the Operator may do unattended: a kill switch nothing overrides, a per-step change ceiling that binds everyone, a daily budget, and an undo that restores the value recorded before the change. |
| **Payroll** | Prices each applied action against the fortnight before it and answers the only question that matters: did the Operator earn more than it cost? |

## Channels

The Operator works on the store's own data, not on a CSV you paste in.

| Channel | State |
|---------|-------|
| **Shopify** | **Verified end to end** on a real development store: OAuth, sync, order notifications, GDPR webhooks |
| Amazon SP-API | Listings, inventory, sales reports, cost ledger. Written and tested against mocks — needs an approved app |
| Amazon Ads | Search-term reports feeding the PPC engine, plus negative keywords. Written and tested against mocks |
| WooCommerce | Written and tested against mocks |

The difference between *written* and *verified* is stated deliberately: Amazon
needs a Professional seller account plus an approved application in Developer
Central, so nothing Amazon-side has met a live account yet.

**A new store starts in observe → suggest → approve.** The Operator reads, judges
and proposes; a person applies. Nothing runs unattended until the seller raises
`auto_apply_below` themselves. Trust is earned per store, not assumed at install.

Every result is persisted (the data moat) and the economics are honest — COGS, ad
cost, referral & FBA fees, capped sales — so the numbers are trustworthy for
screening (confirm exact supplier cost before buying). Money that has not been
measured is never shown as if it had been: profit is withheld until real costs
exist, demo rows are labelled, and only actions with verified provenance reach
the payroll total.

## Quick start

**Windows:** double-click **`start.bat`** — it launches the backend + frontend and
opens the dashboard. (First time only, do the one-time setup below.)

### One-time setup
```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate   # (Windows) or: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                               # 59 tests, no services needed

# Frontend
cd ../frontend
npm install
```

### Run manually
```bash
cd backend && uvicorn app.main:app --reload      # API  -> http://localhost:8000/docs
cd frontend && npm run dev                        # app  -> http://localhost:3002
```

### Database migrations

Production never creates or changes tables on application startup. Apply the
reviewed Alembic migrations before deploying a new API version:

```bash
cd backend
alembic -c alembic.ini upgrade head
```

For a database created by an older version from `schema.sql`, first back it up,
then mark the pre-tenant schema and apply the remaining migration:

```bash
alembic -c alembic.ini stamp 0001_initial
alembic -c alembic.ini upgrade head
```

### Before pointing a real store at it

`_validate_runtime_safety` refuses to start an unsafe production process, but it
fails on the first problem and only at boot — which makes "are we ready?" a
question you answer by repeatedly crashing a deployment. Ask it all at once
instead:

```bash
cd backend
python -m app.preflight            # check the configuration
python -m app.preflight --live     # also try to reach PostgreSQL and Redis
```

Every failure says what to do about it, and the exit code is 1 while any blocker
stands, so a deploy pipeline can gate on it. It is deliberately about *this*
application: a merchant who connects a store hands over an access token to their
own business, and the question is whether this configuration is fit to hold it.

The single blocker that matters most is authentication. With `AUTH_ENABLED=false`
the middleware skips authentication entirely, so the role checks do nothing and
every visitor resolves to the same tenant. If one pilot user makes a login
unnecessary, put the whole app behind an identity proxy rather than leaving the
API open.

### Running it for real

```bash
cp .env.example .env.production        # then fill it in
ENV_FILE=.env.production docker compose --env-file .env.production up -d --build
```

Two variables must be set or compose refuses to start, by design: `POSTGRES_PASSWORD`
and `PUBLIC_API_BASE`. Generate secrets with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`.

Migrations own the schema. `backend/app/db/schema.sql` documents its shape but is
deliberately **not** mounted as a Postgres init script — Postgres would run it on
first boot and `alembic upgrade head` would then fail on "relation already
exists".

Check the deployment from inside itself, where the orchestrator supplies the
configuration:

```bash
docker compose exec api python -m app.preflight --no-env-file --live
```

### Health

`/health` is liveness and `/health/ready` is readiness, and the difference
matters when a dependency fails: with Redis down, readiness answers 503 so a load
balancer stops sending traffic, while liveness stays 200 so an orchestrator does
not kill a process that is working fine.

The worker is not a web server, so it does not answer either. It has its own
check — `python -m app.worker_health` — which is healthy only when it can reach
the queue *and* has registered itself on it. Redis answering says nothing about
whether this container is consuming.

### Signing in

Two doors, and both are optional — a deployment with neither still works, it
just means every new user needs somebody with a shell.

**Sign in with Google.** Create an OAuth 2.0 Client ID (type: *Web
application*) in the Google Cloud console and set `GOOGLE_CLIENT_ID`. The scopes
stay at `openid`/`email`/`profile`, which are non-sensitive and need no
verification review — but publish the app to **In production** rather than
leaving it in Testing, where it is capped at 100 users and authorizations expire
after seven days. Add your domain (and `http://localhost:3002` for development)
under *Authorized JavaScript origins*, and point the consent screen's privacy
and terms URLs at `/privacy` and `/terms`, which this app serves.

Google's identity token is RS256, signed by a key we do not hold and rotated on
Google's schedule, so `app/auth_google.py` delegates the cryptography to
`google-auth` and confines itself to the check the library does not make: an
account is keyed on an email address, so an unverified one is refused.

**A code sent to an email address.** Set `RESEND_API_KEY` and
`LOGIN_EMAIL_FROM` (a verified sender on your Resend domain). Six digits, ten
minutes, five attempts, five requests an hour per address; only an HMAC of the
code is stored, so a copy of the database is not a way to sign in as whoever has
one pending. Without a mailer, development writes the code to the server log and
staging and production refuse instead — a sign-in code in a production log turns
log access into account access.

Both doors resolve through `app/accounts.py`, which is the only place that turns
an address into an account. That matters more than it looks: when the Shopify
listing goes live it becomes a third door, and a seller who installs from
Shopify and later signs in with Google has to arrive at the same tenant rather
than a second empty one.

**The token itself is unchanged.** Signing in returns exactly what
`issue_token` mints and `authenticate_bearer` verifies. It lives in that
browser's localStorage and is sent as `Authorization: Bearer …`. Tokens still
cannot be revoked individually, so `SESSION_TOKEN_DAYS` (default 7, max 30) is
the only revocation there is — affordable now that signing in again is one
click. Rotating `JWT_SECRET` invalidates every token and every pending code.

Whoever runs the server can still mint one by hand, and `/signin` still accepts
a pasted token. That is the way in when email delivery is down or before either
method is configured:

```bash
docker compose exec api python -m app.issue_token \
  --email you@example.com --role owner --days 30
```

Everyone who signs in is `owner` of their own tenant; the client never proposes
a role. The others are `viewer` (read), `operator` (run analyses) and `admin`
(apply and undo changes), and they are handed out by whoever mints a token.

`python -m app.preflight` reports which doors are open, so "can anyone actually
sign up?" is answered before a deploy rather than after it.

### Credential encryption and rotation

Production requires a versioned AES-256 keyring supplied by the deployment
secret manager, never committed to `.env` or source control. Generate a key with
`python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"`
and configure, for example:

```ini
CREDENTIAL_ENCRYPTION_KEYS={"v1":"<base64-32-byte-key>"}
CREDENTIAL_ACTIVE_KEY_VERSION=v1
```

To rotate, add `v2` while retaining `v1`, set the active version to `v2`, call
the credential rotation service for stored integrations, verify every row uses
`v2`, and only then remove `v1`. Migration `0004_encrypted_credentials`
automatically encrypts any legacy SP-API token before dropping its plaintext
column; it fails closed if the keyring is missing or invalid.

### Background worker and retries

Production Autopilot scans run through the Redis-backed `autopilot` queue. Start
an API, Redis, and a worker together with `docker compose up`, or run a worker
separately with:

```bash
cd backend
rq worker --url "$REDIS_URL" autopilot
```

Jobs retry three times with 10s, 60s, and 300s backoff. Exhausted jobs remain in
RQ's failed-job registry for seven days for inspection/requeue. The API exposes
tenant-scoped status at `GET /api/jobs/{job_id}`; `/health/ready` verifies both
the database, Redis, and a live worker for every queue named by
`RQ_REQUIRED_QUEUES` (defaults to `autopilot,integrations,reports`) before a
deployment receives traffic. On Windows, `start.bat` starts the same complete
stack, including Redis and an RQ `SimpleWorker`.

### Monitoring and backups

`GET /metrics` exposes Prometheus-compatible request counts and cumulative
latency using route templates, so tenant IDs and job IDs never become metric
labels. Every response includes `X-Request-ID` plus baseline browser security
headers. Production CORS must be an explicit comma-separated HTTPS allowlist.

The backup and restore-drill runbook lives in `ops/backup/README.md`. A backup is
considered healthy only after checksum validation and a successful restore into
a temporary PostgreSQL database. Store encrypted copies outside the production
account or region.

## Configuration (`.env` at the repo root — gitignored)

```ini
# Live product data (optional; defaults to a sample catalog)
DISCOVERY_SOURCE=keepa
KEEPA_API_KEY=...

# AI explanations (optional; falls back to a deterministic template)
LLM_PROVIDER=openai            # openai | anthropic | none  (auto-fallback between providers)
LLM_LANG=ru                    # explanation language
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# Autopilot scheduler (off by default; each scan spends discovery tokens)
AUTOPILOT_ENABLED=false
```

See `.env.example` for the full list (realism guards, supplier source, etc.).

## Tech stack

Next.js · TypeScript · Tailwind · FastAPI · Python · SQLite (dev) / Postgres + pgvector
(prod) · OpenAI/Anthropic · Keepa · Docker · GitHub Actions CI.

## What's not built yet

Real publishing to Amazon (needs a Seller account + SP-API; the listing is drafted and
gated behind manual confirmation), real supplier data (pluggable — add a key), a
landing page, deployment. Sessions are bearer tokens in localStorage rather than
httpOnly cookies, and cannot be revoked one at a time. See `docs/ROADMAP.md`.

---
*Fee figures are 2026 orientation values — verify exact FBA fees in Amazon's official
calculator and real supplier cost before committing. This tool supports decisions; it
does not guarantee profit.*
