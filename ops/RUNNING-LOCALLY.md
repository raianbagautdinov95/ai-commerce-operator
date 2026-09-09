# Running the whole thing on a laptop

The stack is PostgreSQL, Redis, the API, a worker and the frontend, in Docker.
`start.bat` and the virtualenv in the README run the API and frontend directly
and are still fine for engine work, but row-level security only does anything
against real PostgreSQL, so anything touching tenants, credentials or the
Shopify cycle needs this.

## Once

Copy `.env.example` to `.env.production` and fill it in. Everything the API
refuses to start without is listed there; `python -m app.preflight --env-file
.env.production` says which are missing before Docker does.

## Every time

```bash
ENV_FILE=.env.production docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.local.yml up -d
```

Both env arguments are needed and they do different things. `--env-file` feeds
compose's own `${...}` interpolation — the passwords in `docker-compose.yml`.
`ENV_FILE` is read by the `env_file:` directive and is what the containers
actually receive. Passing only one leaves the other reading `.env`, and a
container starts with settings nobody chose.

The app is then on <http://localhost:3000> and the API on
<http://localhost:8000>.

## The API address is compiled in

`NEXT_PUBLIC_API_BASE` is fixed when the frontend image is **built**, so it is
not something a restart can correct — a wrong value ships. It has shipped twice:
once as a domain with no DNS record, once as a developer's own `localhost:8001`
left in a shell variable. Both looked identical from the browser — "Failed to
fetch" on every screen — while the API answered every request put to it
directly, and nothing in the running container hinted at why.

There is one knob, `FRONTEND_API_BASE`, and each compose file supplies its own
default:

| File | Value | For |
|---|---|---|
| `docker-compose.local.yml` | `http://localhost:8000` | the app opened on localhost |
| `docker-compose.production.yml` | `https://$PUBLIC_DOMAIN` | a real deployment |
| `docker-compose.yml` alone | `UNSET` — the build refuses | nobody chose |

A build under the production file also refuses `http://`, `localhost` and
throwaway tunnels: an address that would ship to real browsers should stop a
build, not a customer.

**Clear the variable before rebuilding.** A leftover `FRONTEND_API_BASE` or
`PUBLIC_API_BASE` in your shell wins over the compose files:

```bash
env -u FRONTEND_API_BASE -u PUBLIC_API_BASE -u NEXT_PUBLIC_API_BASE \
  ENV_FILE=.env.production docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.local.yml up -d --build frontend
```

Then check what actually got baked, because this is the failure that hides:

```bash
docker compose exec -T frontend sh -c \
  'grep -rhoE "https?://[a-zA-Z0-9._:-]+" .next/static/chunks/*.js | sort -u | head'
```

## Shopify needs a public address

Shopify will not call `localhost`, so OAuth callbacks and order notifications
need a tunnel. See `ops/TUNNEL.md` for why a quick tunnel costs you the webhook
subscriptions every restart, and `docker-compose.tunnel.yml` for the two
variables it sets. The tunnel is only for Shopify: the browser still talks to
`localhost:8000`.

## Checking it

```bash
# the whole cycle, one line per link, read-only
bash ops/check-store-cycle.sh

# configuration, judged as a local stack
docker compose exec api python -m app.preflight --live --no-env-file

# the same settings judged as a deployment
docker compose exec api python -m app.preflight --no-env-file --production

# tests, against SQLite, touching nothing that is running
cd backend && .venv/Scripts/python -m pytest tests -q
```

`--no-env-file` matters inside a container: without it preflight reads a `.env`
that may have been copied into the image and reports settings the running
process never saw.

## Two things that will bite

**Rebuilding one service does not rebuild the others.** `docker compose build
api` leaves `migrate` and `worker` on the previous image, so a migration can
report success at a revision that no longer exists in the code. Build them all,
or name each one you mean.

**`--force-recreate` does not always recreate.** A container already in
`Created` state can survive it and keep running with settings the compose file
no longer describes. `docker compose rm -sf <service>` then `up` is the version
that works.
