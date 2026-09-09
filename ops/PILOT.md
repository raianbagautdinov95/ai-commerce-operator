# Running the first paid pilot

One customer, one plan, one person answering. This is the runbook for that, and
for the handful of things that will go wrong.

Before taking money, run the gate. It reports what a process can verify and what
only you can:

    docker compose exec api python -m app.pilot_gate --live --no-env-file

It is deliberately hard to satisfy, and it never calls the product proven
because services answered 200.

## Connecting the first customer

1. They sign in with their email. A store is created for them on first sign-in;
   nothing is shared with any other customer, and PostgreSQL enforces that
   rather than the application asking nicely — see `tests/test_rls_postgres.py`.
2. They open **Channels → Shopify** and enter their `.myshopify.com` address.
   Not a custom storefront domain: OAuth is bound to the permanent one.
3. Shopify asks them to approve **read-only** access — products, orders,
   inventory. There is no write scope, so the Operator cannot change their shop
   even if it wanted to.
4. They come back to a connected store. Check three things separately, because
   they fail separately:
   - **connected** — true only when a usable token is stored, not when a row
     exists;
   - **notifications** — `active`, `pending`, `stale`, or `failing_signature`;
   - **sync** — the first one runs from the Shopify screen and finishes as a job,
     not when it is queued.
5. The sync re-reads what it wrote, so proposals appear on their own. If none do,
   that is an answer, not a failure: under a week of history, or nothing worth
   saying. The Proposals screen says which.

Tell them plainly: nothing will be changed in their shop by us. Every proposal
waits for them, `auto_apply_below` is zero for a new store, and no scope exists
to write anything.

## Shopify stopped working

**`notifications: stale`** — the subscriptions point at an address this server
no longer answers. Press *Point them here* on the Shopify screen. Usually a
domain change.

**`notifications: failing_signature`** — deliveries are arriving and being
refused. One side is holding an old `SHOPIFY_CLIENT_SECRET`. See
`ops/SECRETS.md`; the screen clears itself once one delivery verifies.

**`connected: false` with a reason** — the customer uninstalled the app, or the
token was revoked. They reconnect from the same screen; the channel row is
reused rather than duplicated.

**Nothing wrong on screen and no orders arriving** — check
`/health/operations`. A store that has not synced for 48 hours is reported
there. Then the worker log: `docker compose logs worker | tail -50`.

## Orders are missing

In order of likelihood:

1. **A stale or failing subscription** — above. Deliveries refused during a
   secret rotation are retried by Shopify, so they arrive late rather than
   vanishing.
2. **A delivery stuck in `processing`** — `/health/operations` reports these. It
   means a process died mid-delivery. The data is recoverable: run a sync, which
   reads orders directly rather than waiting to be told about them.
3. **The worker is not consuming** — `docker compose exec worker python -m
   app.worker_health`, or the queue depth in Redis.
4. **Nothing is wrong** — quiet days are real. `bash ops/check-store-cycle.sh`
   prints one line per link and names the first that is open.

A sync is always safe to re-run: it is keyed on the day and the product, so it
overwrites rather than doubling.

## Refunds and cancellation

Both happen in Stripe, not here.

- **Cancel** — the customer uses the Billing Portal, or you cancel the
  subscription in Stripe. The `customer.subscription.deleted` webhook sets the
  status to `canceled`. Their data is untouched: cancelling ends billing, not
  the account.
- **Refund** — issue it in Stripe. Nothing here needs changing; we never hold
  money and never compute an amount.
- **Failed payment** — Stripe retries and sends `past_due`. The status is
  recorded; decide deliberately what to restrict, and say so to the customer
  before restricting anything.

Whatever Stripe says is what we believe. There is no second source of truth
and no arithmetic on our side.

## Deleting a customer's data

`POST /api/privacy/deletion-request` records the request and returns
`pending_review`. It deliberately does **not** delete: erasure is irreversible
and a stray click should not perform it. Carrying it out is this procedure:

1. Confirm the request in the audit log (`privacy.deletion_requested`).
2. Take a backup first, and verify it restores.
3. Check you have the right tenant, while you still can:

       docker compose exec api python -m app.erasure --store <id> --dry-run

   It counts what would go, by table, and changes nothing.

4. Carry it out. The confirmation names the store, because typing the wrong id
   twice is harder than typing "yes":

       docker compose exec api python -m app.erasure \n         --store <id> --confirm "ERASE <id>"

   One transaction: a half-erased tenant is the worst of both, their data still
   here and their account gone, with nobody able to say what remains. It removes
   every tenant table, the stored Shopify token, their sessions — a token that
   still resolves is a door into an account that no longer exists — and the
   audit trail, replacing it with one event recording that the erasure happened,
   its date and its request id.
5. Revoke the app in the Shopify Partner Dashboard, so nothing of theirs remains
   usable from our side or Shopify's.
6. Tell the customer, with the date.

There is deliberately no endpoint for step 4. No combination of session, role and
confirmation dialog makes an irreversible cross-table delete safe to expose to
the internet; running it needs shell access to the deployment, which is a much
smaller set of people than everyone holding a token.

## Wake somebody up for these

- `/health/ready` failing for more than a few minutes — the API cannot serve.
- Redis or PostgreSQL unreachable.
- `notifications: failing_signature` — money-affecting data is being refused.
- Stripe webhooks failing — somebody's payment state is wrong.
- A backup that has not run in 26 hours. A cron that silently stopped produces
  no errors, which is what makes it dangerous.

Everything else — a stale subscription, a stuck delivery, a store that has not
synced — is a next-morning problem. Say so in advance, or every alert becomes
one nobody reads.

## What a pilot customer should be told

- Response time you will actually meet. For one pilot: same business day.
- An address that is read by a person.
- That this is a pilot, that the Operator has not yet demonstrated a measured
  financial result, and that they are early rather than buying a proven outcome.

That last one matters. The product's own claim is that it earns more than it
costs, and until one action has been carried out and measured over its full
window, that is unproven. `pilot_gate` prints it every run for the same reason.

## Rolling back

Migrations are additive and every one has a `downgrade`, but rolling a schema
back is the last resort, not the first.

1. **Redeploy the previous image.** Nearly every bad deploy is code, and the
   schema tolerates the previous version because changes are additive.
2. If a migration must be undone: back up first, verify the backup restores,
   then `alembic -c alembic.ini downgrade -1` on the API service. One step at a
   time, checking between.
3. Never restore a whole database to undo a code bug. It loses every order that
   arrived since, and those are the customer's.

The migration runs as `preDeployCommand` on the API service only. Do not add it
to the worker: two services racing the same revision is a lock at best.
