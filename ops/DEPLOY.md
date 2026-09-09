# Putting this on a real domain

The whole public address is one variable. Five settings used to encode the same
hostname independently, and `TUNNEL.md` exists because getting one of them out
of step fails *silently* — Shopify keeps calling the address it was given, the
store looks quiet rather than broken, and nothing errors at either end.
`docker-compose.production.yml` derives all five from `PUBLIC_DOMAIN`, so they
cannot disagree.

## Before anything

Two things must be true outside this repository:

1. **`PUBLIC_DOMAIN` resolves to the server.** An A record (and AAAA if it has
   IPv6) pointing at its address.
2. **Ports 80 and 443 reach it.** 80 is not optional. It is how Let's Encrypt
   issues the certificate and how it renews it ninety days later.

A certificate that lapses takes the product down for every store at once, at
whatever hour it happens to expire. Caddy renews on its own, which is the only
reason that is not a recurring diary entry.

## The settings

In `.env` on the server:

    APP_ENV=production
    AUTH_ENABLED=true
    JWT_SECRET=<48 random bytes: python -c "import secrets;print(secrets.token_urlsafe(48))">
    POSTGRES_PASSWORD=<random>
    APP_DB_ROLE=aco_app
    APP_DB_PASSWORD=<random, letters and digits only>
    LEGAL_ENTITY=...
    LEGAL_ADDRESS=...
    PRIVACY_CONTACT=...
    SHOPIFY_CLIENT_ID=...
    SHOPIFY_CLIENT_SECRET=...
    CREDENTIAL_ENCRYPTION_KEYS=...

`APP_DB_PASSWORD` is letters and digits on purpose: migration 0016 interpolates
it into a `CREATE ROLE` statement, so a quote in it breaks the SQL.

**Delete `SHOPIFY_REDIRECT_URI`, `SHOPIFY_WEBHOOK_URI`, `SP_API_REDIRECT_URI`
and `CORS_ALLOWED_ORIGINS` from `.env`.** The production overlay sets them from
`PUBLIC_DOMAIN`; a leftover value in `.env` is only there to be picked up by
some service you forgot to override, which is the original failure wearing a
different hat.

## Bringing it up

    export PUBLIC_DOMAIN=app.example.com
    export TLS_EMAIL=you@example.com

    docker compose -f docker-compose.yml -f docker-compose.production.yml build
    docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm migrate
    docker compose -f docker-compose.yml -f docker-compose.production.yml up -d

`migrate` is run explicitly rather than left to `depends_on`, so a migration
that fails stops the deploy instead of being noticed later in a log.

Then, before pointing a store at it:

    docker compose -f docker-compose.yml -f docker-compose.production.yml \
      exec api python -m app.preflight --live --no-env-file

It must print `READY`. It is the same check that refuses to let the API start
in production without authentication.

## Shopify

The redirect URL in the Partner Dashboard must be the same string as
`SHOPIFY_REDIRECT_URI`, character for character:

    https://<PUBLIC_DOMAIN>/api/integrations/shopify/callback

Shopify compares it exactly. A trailing slash is a different URL.

Webhook subscriptions are registered by this app, not in the dashboard, and
they remember the address they were registered against. After a domain change,
existing subscriptions still point at the old one. The app detects this —
`GET /api/integrations/shopify/account` reports `notifications: "stale"` — and
the Shopify screen offers **Point them here**, which deletes the old
subscriptions and recreates them. Use it after every address change.

## Changing the domain later

`NEXT_PUBLIC_API_BASE` is baked into the frontend image at build time, so the
frontend must be **rebuilt**, not restarted. Under the production compose file
it comes from `FRONTEND_API_BASE`, defaulting to `https://$PUBLIC_DOMAIN`, and
the build refuses `http://`, `localhost` and throwaway tunnels rather than
shipping one of them to a real browser. Clear any leftover `FRONTEND_API_BASE`
from your shell first — a shell variable beats the compose file. See
`ops/RUNNING-LOCALLY.md` for the same knob locally.

    docker compose -f docker-compose.yml -f docker-compose.production.yml \
      build frontend && \
    docker compose -f docker-compose.yml -f docker-compose.production.yml \
      up -d frontend

Then update the Partner Dashboard redirect URL and press **Point them here** on
the Shopify screen.

## What the proxy does and does not do

`ops/Caddyfile` routes `/api`, `/health`, `/docs` and `/openapi.json` to the
API and everything else to the frontend. It drops `code`, `state`, `hmac`,
`signature` and `access_token` from the access log — an OAuth code is
single-use and short-lived, but an access log is copied, shipped and kept for
months.

It does not rate-limit and it does not stand in for a firewall. Postgres
publishes 5432 in the base compose file for local work; on a server, remove
that mapping or make sure the port is closed at the network.
