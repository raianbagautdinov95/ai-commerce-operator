# Deploying to Railway

Four services from one repository, plus Railway's PostgreSQL and Redis. The
whole production environment is declared in `.railway/railway.ts` using
Railway Infrastructure as Code (IaC).

| Service     | Root directory | Process |
|-------------|----------------|---------|
| `api`       | `/backend`     | FastAPI; runs migrations before deploy |
| `worker`    | `/backend`     | RQ queue consumer |
| `scheduler` | `/backend`     | Daily `python -m app.daily` cron |
| `frontend`  | `/frontend`    | Next.js |

Do not set a service-level **Config File** path. The old `railway/*.toml`
mechanism is deprecated and new services cannot use it. IaC owns the service
roots, commands, health checks and the two managed databases from one
project-level file. New custom domains are added once in the dashboard because
Railway's IaC planner does not currently register them from code.

Install dependencies, log in and link this directory to the production project.
Always preview first:

    npm install
    npx railway login
    npx railway link
    npx railway config plan

Only after checking that the plan has no unexpected deletions:

    npx railway config apply

After apply, add `api.aicommerceoperator.com` to the existing
`ai-commerce-operator` service (the API) and `app.aicommerceoperator.com` to
`frontend` under **Settings → Networking → Custom Domain**.

## Two subdomains, not one

Railway gives each service its own domain and there is no reverse proxy in
front of them, so `app.example.com/api/...` cannot reach the API service. Two
subdomains:

    app.aicommerceoperator.com  → frontend
    api.aicommerceoperator.com  → api

Shopify then gets:

    redirect  https://api.aicommerceoperator.com/api/integrations/shopify/callback
    webhook   https://api.aicommerceoperator.com/api/webhooks/shopify

The webhook path is `/api/webhooks/shopify`. There is no route at
`/api/integrations/shopify/webhooks`; pointing Shopify there means it POSTs to
a 404 for as long as nobody notices, and a store with no incoming orders looks
quiet rather than broken.

This differs from `docker-compose.production.yml`, which puts Caddy in front
and serves everything from one hostname. Both are correct for their topology;
neither is a default for the other.

## The database role

`aco_app` is deliberately `NOSUPERUSER NOBYPASSRLS`, because row-level security
never constrains a superuser. If the application connects as the owner, every
RLS policy in migration 0016 is decorative and one store can read another's
rows. So:

* **Railway's PostgreSQL gives you the owner URL.** That is for migrations.
* **The application gets a different URL**, for `aco_app`.

On the `api` service set both:

    MIGRATION_DATABASE_URL = <Railway's own DATABASE_URL, the owner>
    DATABASE_URL           = postgresql+psycopg://aco_app:<APP_DB_PASSWORD>@<host>:<port>/<db>
    APP_DB_PASSWORD        = <letters and digits only>
    APP_DB_ROLE            = aco_app

`migrations/env.py` prefers `MIGRATION_DATABASE_URL`, so the pre-deploy command
runs as the owner while the server itself runs restricted. On the `worker`
service set `DATABASE_URL` to the same `aco_app` URL and leave
`MIGRATION_DATABASE_URL` unset — migrations belong to one service, or two
deploys race to apply the same revision.

`APP_DB_PASSWORD` is letters and digits on purpose: migration 0016 interpolates
it into a `CREATE ROLE` statement, so a quote breaks the SQL.

Verify it took, from the Railway shell:

    psql "$MIGRATION_DATABASE_URL" -c \
      "select rolname, rolsuper, rolbypassrls from pg_roles where rolname='aco_app'"

Both flags must be `f`. If they are not, RLS is not protecting anything.

## Ports

Railway assigns a port per deployment and passes it as `PORT`. Both images read
it and fall back to 8000 and 3000 for Compose. Do not pin `PORT` as a service
variable — a health check aimed at the assigned port while the server listens
on a pinned one fails forever, and a platform that restarts unhealthy services
turns that into a loop.

## Migrations

The `api` resource in `.railway/railway.ts` sets:

    preDeployCommand = "alembic -c alembic.ini upgrade head"

Railway runs this before the new version takes traffic and stops the deploy if
it fails, so a failed migration leaves the previous version serving rather than
a new one meeting a schema that was never applied.

## The rest of the environment

Everything in `.env.example` still applies. The ones that must be right before
a real store connects:

    APP_ENV=production
    AUTH_ENABLED=true
    JWT_SECRET=<48 random bytes>
    CREDENTIAL_ENCRYPTION_KEYS=...
    CORS_ALLOWED_ORIGINS=https://app.aicommerceoperator.com
    SHOPIFY_REDIRECT_URI=https://api.aicommerceoperator.com/api/integrations/shopify/callback
    SHOPIFY_WEBHOOK_URI=https://api.aicommerceoperator.com/api/webhooks/shopify
    LEGAL_ENTITY / LEGAL_ADDRESS / PRIVACY_CONTACT

And on the frontend service, as a **build** variable:

    NEXT_PUBLIC_API_BASE=https://api.aicommerceoperator.com

Next.js inlines that into the JavaScript the browser downloads. Setting it only
at runtime changes nothing; the value present at build time is the one that
ships, so a domain change means a rebuild, not a restart. A wrong value here is
invisible until a browser fails to reach an API that is running perfectly, which
has happened twice — see `ops/RUNNING-LOCALLY.md`.

Before pointing a store at it, from the `api` service shell:

    python -m app.preflight --live --no-env-file

Preflight judges by `APP_ENV`: with `production` set, a localhost origin or a
throwaway tunnel is a blocker rather than a warning. To check a production file
from a laptop, add `--production` so it is judged strictly whatever `APP_ENV`
happens to say locally:

    python -m app.preflight --env-file .env.production --production

It must print `READY`.

## What Railway does not give you

Backups off the platform, Sentry, and a rotated Shopify secret are still
separate jobs. `ops/backup/` holds the PostgreSQL backup and restore-drill
scripts; a restore that has never been tried is not a backup.
