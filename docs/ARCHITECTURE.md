# Architecture

AI Commerce Operator is a **digital operations employee** for an online store: it
watches the store's own data, decides what is worth doing, does the narrow things
it is trusted with, and at the end of the month shows what that was worth in money.

Everything below follows from one rule and one promise.

---

## The rule: the LLM never computes money or verdicts

All economics, scores, limits and verdicts are computed by deterministic Python.
The LLM only ever *explains* numbers that already exist.

    ProductInput ──► decision_engine ──► Evaluation(score, verdict, economics)
                                              │
                                              └──► llm.explain()  ← narrates only

This is what separates a product from a GPT wrapper. A model asked to "score this
product" gives a different answer to the same input on Tuesday, cannot show its
work, and will confidently invent a margin. A seller cannot run a business on that.
Every engine here can be pointed at and traced: the weights are one dict, the
thresholds are named constants, and the arithmetic is plain.

The boundary is enforced in review, not by a type: every `explain_*` in `llm.py`
takes an already-computed dict and its system prompt forbids recomputation.
**Protect this boundary.** It is the moat.

## The promise: a number the seller can trust

Money that has not been measured is never shown as if it had been.

- `/api/dashboard/roi` withholds profit and returns `missing_profit_inputs` until
  real costs exist.
- Seeded demo rows are labelled `source="synthetic_demo"`, and a real connected
  channel displaces demo channels from the totals rather than summing with them.
- Payroll counts only actions whose provenance is `real`; `demo` and `unverified`
  are shown beside the total, never inside it.
- An outcome window too short to prove anything is refused, not estimated.

When in doubt the number goes **down**, never up. A savings claim that flatters
itself is worth less than no claim at all.

---

## Layers

    ┌─ frontend/            Next.js screens + one typed API client (lib/api.ts)
    │
    ├─ app/main.py          the application: config checks, middleware, mounting
    ├─ app/routers/         the HTTP surface, one module per domain
    │
    ├─ app/*_engine.py      DETERMINISTIC decisions — the core
    │  app/guardrails.py    what may happen unattended
    │  app/actions_engine.py what an applied action was worth
    │
    ├─ app/llm.py           explanation only, provider-agnostic, never arithmetic
    │
    ├─ app/amazon_*.py      channels: SP-API, Ads
    │  app/shopify.py       channels: Shopify
    │  app/woocommerce.py   channels: WooCommerce
    │
    ├─ app/tasks.py         RQ worker entrypoints (never imports the web app)
    │  app/queueing.py      the Redis/RQ boundary
    │
    └─ app/db/              SQLAlchemy models + crud, tenant-scoped

Direction of dependency is one-way: routers may call engines, engines never call
routers. `tasks.py` shares logic with the API through `autopilot_service.py`, not
by importing `main` — a worker has no business building an OpenAPI schema.

---

## The decision engines

Each is a pure module: structured data in, scored findings out. No I/O, no LLM.

| Module | Decides |
|---|---|
| `decision_engine.py` | Is this product worth selling? Score, hard gates, what-if |
| `ppc_engine.py` | Which keywords waste money, which bids to change |
| `commerce_engine.py` | What a store's own daily sales support saying — and what they refuse to price |
| `inventory_engine.py` | When to reorder, how much, what will stock out |
| `report_engine.py` | What matters most today, ranked by severity then money |
| `guardrails.py` | May this change happen, and unattended? |
| `actions_engine.py` | What was this change actually worth? |

`decision_engine.py` is the reference implementation of the shape: weights summing
to 100 in one dict, thresholds as named constants, hard gates that override the
score, and a `WhatIf` showing how far the inputs can move before the economics
break. The what-if is the most useful thing it produces — not "7/10" but "your
supplier can raise the price to $8.40 before you are losing money".

---

## The loop

This is what makes it an operator rather than a set of tools. Nobody presses a
button per keyword.

    read search terms          amazon_ads_api.search_terms()
            │
            ▼
    judge them                 ppc_engine.analyze()
            │
            ▼
    pair verdicts with ids     actions_engine.proposals_from_ppc()
            │
            ▼
    judge EACH proposal        guardrails.evaluate()
            │
       ┌────┴─────┬──────────────┐
       ▼          ▼              ▼
    ALLOWED    APPROVAL       BLOCKED
    applied    left for       refused,
    by the     a human        audited
    operator      │
            │     ├── "I did this"  /applied  → measured, then priced
            │     └── "Decline"     /dismiss  → recorded, never priced
            │
            ▼
    price the outcome          actions_engine.measure()
            │
            ▼
    /api/dashboard/payroll     "it saved €X against €Y of cost"

The same loop runs from a store's own sales, and the interesting difference is
what it declines to say:

    read the measured days     commerce_engine.daily_series()   ← quiet days filled in
            │
            ▼
    judge them                 commerce_engine.analyze()
            │
            ▼
    open one action each       actions_engine.proposals_from_commerce()
            │                  POST /api/commerce/scan
            ▼
    ... the same guardrails, the same /applied, /dismiss, /measure

Two of the three commerce rules refuse to name a figure. Missing landed costs
blocks every profit question and nobody can say what fixing it is worth until it
is fixed; a stall costs whatever the reason for it costs, which daily totals do
not know. Only refunds name money, and the figure is what was observed leaving,
not a forecast of what stopping it would return. So `Proposal.projected_impact`
is `float | None`, and None survives all the way onto the screen: 0.0 would read
as "nothing at stake", which is a different claim and usually a false one.

The day series is built once, in the engine, and read by both the scan and the
commerce dashboard. That is not tidiness — the stall rule counts silent days, so
a second series that dropped them would make a stalled store look busy to the
engine while the chart showed the truth.

### The one commerce action that can be proven

Three of the four commerce rules end in something a person does out of our
sight. We can measure what happened afterwards, but nobody watched the change
itself, so they stay `unverified` for ever and the payroll goes on excluding
them in the open, counted in `excluded_unverified`.

`RESTOCK_PRODUCT` is different, and it is the reason `channel_products`,
`product_daily_metrics`, `variant_daily_metrics` and `product_costs` exist.
Store-wide totals cannot say which product is running out, and a restock judged
against shop-wide revenue would be judged against every other product's noise as
well. Margin needs finer grain still: two variants of one snowboard can cost
different amounts to buy, so sales are kept per variant and costs are attached
to the variant that sold.

The cost itself comes from `InventoryItem.unitCost`, which `read_inventory`
already permits — the scope this app has held since the first connection. No new
scope, no reconnection, nothing asked of the merchant. Where a shop has not
filled it in, `/costs` is where a seller says what they paid; that value is
recorded as `reported` rather than `confirmed`, and every screen says which it
is looking at.

    STOCKOUT_RISK          commerce_engine.analyze_products()
            │              units sold ÷ days, against units left
            ▼
    RESTOCK_PRODUCT        one action, naming the product
            │
            ▼
    "I did this"           POST /api/commerce/actions/{id}/confirm
            │              → reads totalInventory back from Shopify
            │              → refuses, recording nothing, if it did not move
            ▼
    the window closes      app.daily → tasks.measure_due_actions
            │              (or POST /api/commerce/actions/{id}/measure, which
            │               is the same code, for somebody who will not wait)
            │              → the after-window from product_daily_metrics
            │              → nobody types a number in
            ▼
    measured               units the old stock could not have covered
            │
            ▼
    evidence_mode          "real" only when all four hold

The confirmation is the whole difference between a ledger and a diary. The
seller says the restock happened; Shopify is asked whether the shelf moved; and
a claim the store contradicts is refused rather than recorded.

#### What a restock may be said to have caused

`commerce_measurement` keeps three questions apart, because merging them is how
a product starts claiming money it did not earn:

| Question | Answered by |
|---|---|
| Did it happen? | `verified_on_hand`, read back from Shopify at confirm time |
| Were the days observed? | the window closed **and** a sync covered it |
| Was it worth anything? | `actions_engine.measure_restock` |

The pricing deliberately does **not** compare the revenue rate before and after.
Sales did rise after the shelf was refilled; they also rose after that Tuesday,
and pricing the difference as the restock's work is post-hoc reasoning. What is
used instead needs no control group:

    incremental_units = max(0, units_sold_after - starting_on_hand)
    result            = Σ (unit_revenue - unit_cost) over those units only

A shop holding one unit could not have sold thirty. Two windows with identical
sales price identically however busy or quiet the baseline was.

Which units count as the ones the old shelf could have covered is a choice, and
it is made against ourselves: the **highest-margin** units are assumed to have
sold anyway, so what is claimed is the cheapest explanation of the same sales.

`unit_revenue` is what the shop kept — `discountedTotalSet` rather than
`originalTotalSet`, less tax where the shop's prices include it, less refunds,
and cancelled orders never counted. `unit_cost` comes from `product_costs`,
which is **history**: the value in force on the day that unit sold, so a cost
that changes mid-window prices each day as it was. Everything is `Decimal`,
rounded once at the end and downwards.

An **elapsed** window is not an **observed** one. If nothing synced, the rows
are empty for those days, and empty looks exactly like no sales — so a broken
integration would price as a failed restock. `run_shopify_sync` records
`synced_from`/`synced_through` on the channel, and a window nothing has covered
leaves the action waiting rather than measured at zero. Losing a token later
does not undo days already stored.

`evidence_mode="real"` needs all of `commerce_measurement.REAL_REQUIRES`: the
change witnessed, the window closed and covered, units beyond the old shelf, the
revenue of those units known, a cost for **every** one of them, one currency on
both sides, and the arithmetic from the engine. Any of them missing leaves the
action `awaiting_cost` or `unverified` with the reason attached — never at zero,
and never at revenue with a profit's name on it.

Two refusals are deliberate. One variant without a cost blocks the whole
result rather than being dropped, because dropping it would raise the average
margin of what remains. And a cost in a different currency from the sale blocks
too: there is no FX source in this project, and inventing a rate is the one kind
of number it will not produce.

What the margin does **not** include is named in every `basis` string: shipping,
payment fees and anything else the shop pays per order rather than per unit.
Allocating those across units would be an assumption, and the figure is called
gross margin rather than profit because of it.

Two refusals are worth keeping in mind when reading the engine. It will not
invent a supplier lead time, which is why it answers a narrower question than
`inventory_engine` does: every verdict in that engine turns on a lead time
Shopify does not know and neither do we. And `on_hand` is nullable everywhere it
appears, because Shopify returns nothing for a product it does not count —
reading that as zero would report every untracked product as about to run out.

**A proposal left for a human has two exits, not one.** For a while it had only
`/applied`, so the only way to answer "no" was to ignore it — and a queue nobody
can empty stops being read, which costs more than any single bad suggestion.
`POST /api/actions/{id}/dismiss` records the refusal, with an optional reason
that is never demanded; demanding one is how a queue stays full. A dismissed
action is never priced — it was not applied, so there is nothing to measure and
nothing to claim — but it stays listed, because the same suggestion being
declined three times is worth seeing. `/proposals` is where both answers live.

A finding whose search term is not in the report is **skipped, not guessed at** —
there is no campaign or ad group to attach a negative keyword to. Proposals are
ordered by the money they save, so when a daily limit bites, it bites the small
ones. Each proposal is judged separately counting what the same run has already
applied, so the Operator cannot talk itself past its own budget inside one scan.

---

## The daily run

Two halves of the loop used to need a person. A shop was only read when somebody
pressed *Sync* or had just finished connecting, so a shop left alone stopped
producing days — and a missing day looks exactly like a quiet one. An applied
action was only priced by somebody visiting a screen they had no reason to
revisit, so it could sit `applied` for ever.

`app.daily` is one cron entry (`railway/scheduler.toml`, 07:00 UTC) running
three stages in order:

    trial_warnings   warn anybody whose trial is about to end
    shopify_sync     queue a read-only sync for every connected shop
    measurement      price every applied action whose window has closed

One process, because a shop has to be read before the results that depend on
those days can be priced. **Three jobs**, because they fail for unrelated
reasons: Resend being down must not stop a sync, a revoked Shopify token must
not stop somebody's trial warning, and "the daily job ran" is not an answer when
only two thirds of it did. Each claims its own key in `scheduler_runs`, keeps its
own freshness, and is asked about separately by `alerts` and by the pilot gate.

Running it twice is handled by the database rather than by hoping. The unique
`(job, run_key)` means a second firing loses the insert and stops; a row left
`running` by a dead process is reclaimable after six hours, because otherwise one
crash would stop that stage for ever. Underneath that each stage is idempotent in
its own right — receipts deduplicate, a sync is claimed per shop per day and
skipped while one is in flight, a measurement is guarded by `measured_at`. That
is the last line rather than the only one.

The sync reaches back far enough to cover any measurement window still waiting on
it, not merely `DAILY_SYNC_DAYS`. A result stranded for want of days nobody
fetched waits for ever, and the sync is the only thing that could have fetched
them.

---

## Guardrails

Nothing changes a live account without passing them — the Operator and the owner
alike. Three verdicts in strict precedence:

| Verdict | Meaning |
|---|---|
| `BLOCKED` | Refused outright |
| `APPROVAL` | Allowed only with a human saying yes to this specific action |
| `ALLOWED` | Proceed |

The **kill switch** (`enabled`) is checked first and nothing overrides it, not even
a person. When an owner switches the Operator off, arguments about thresholds stop
mattering — that is the whole point of a kill switch.

A change larger than `max_change_pct` in one step is refused for anyone: a 60% bid
cut is not a small mistake to undo. The remaining limits — `max_actions_per_day`,
`auto_apply_below`, `protected_spend_per_day` — ration **unattended** action only.
They bound the Operator, never the owner spending their own money.

Undo is the other half. `applied` records `revert_to` *before* the change;
`POST /api/actions/{id}/revert` hands that value back and records the undo. An
action with no recorded previous value refuses to auto-revert rather than guessing.

---

## The payroll

`actions_engine.py` + `operator_actions` answer the only question that matters to
the owner. Impact is the net of two effects, computed from **daily rates** so
windows of different lengths stay comparable:

    cost avoided   = (baseline spend/day  - outcome spend/day)   × outcome days
    revenue gained = (outcome revenue/day - baseline revenue/day) × outcome days
    impact         = cost avoided + revenue gained

Netting is the point. Cutting €112.50 of ad spend while losing €6.00 of sales is
worth €106.50, not €112.50. A negative impact is reported as readily as a positive
one; an Operator that cannot show its losses cannot be trusted with its wins.

### Delivery and decisions survive a crash

Two failures that look identical to a working system and are not:

**A process that dies mid-webhook.** The delivery row says `processing` because
nobody survived to say otherwise. Answering "duplicate" on Shopify's retry tells
it we handled an order we never recorded, and it stops retrying — the order is
gone. A row still `processing` past `STALE_PROCESSING_SECONDS` is therefore
reclaimed, while one that started moments ago is still refused as the concurrent
redelivery it is. Reclaiming too eagerly double-counts an order; refusing too
eagerly loses one.

**A second click on approve.** The browser mints a fresh `Idempotency-Key` per
click, so two clicks are two operations and the key protects nothing. A decision
is a one-time transition instead: repeating the same one is answered with the
settled state, and contradicting it is refused with 409, so a stale tab cannot
overturn a decision made elsewhere.

### What deserves a human

`alerts.py` names the conditions that today's failure testing showed are both
real and silent — each one lets the product keep answering 200 while quietly
doing less than it claims:

| Condition | Why it is silent |
|---|---|
| `channel_access_revoked` | The screen still renders; nothing syncs |
| `notifications_point_elsewhere` | Shopify calls a dead address and retries into it |
| `changes_unproven` | An unattended change could not be read back |
| `webhook_deliveries_stuck` | A process died mid-flight; the row still says "processing" |
| `operations_stuck` | A worker is gone but its claim is not |
| `operator_switched_off` | Deliberate after an incident, easy to forget after that |

All derived from the database, so they can be polled from anywhere and asserted
in tests. `GET /api/alerts` for a screen or a probe; `python -m app.alerts` for
cron, which walks **every** tenant — a monitoring job has no session, and a
condition nobody is logged in to see is exactly the kind that goes unnoticed —
and exits 1 while anything critical stands, since an exit code is the one
interface every monitoring system already speaks.

Deliberately not wired to a particular pager. It produces conditions; Sentry, a
cron job or a probe decides what to do with them. An alert nobody routes is noise.

### Tenant isolation below the application

Application filters are the readable, testable first line, and they defend
against nothing that a mistake in this codebase can cause — which is the likeliest
cause there is. One forgotten `WHERE` was the whole distance between a query and
another tenant's data.

Row-level security moves the guarantee under the application. Nineteen
tenant-scoped tables carry a policy keyed on `app.tenant_id`, which the session
sets automatically from the bound principal on every transaction. Unset, the
setting is NULL, every policy fails, and a transaction that never declared a
tenant reads **nothing** — failing closed, because the alternative is reading
everything.

Two conditions make or break this, and both are easy to get wrong:

- **The application must not connect as a superuser.** RLS never applies to a
  superuser or a role with BYPASSRLS, so enabling it while the API connects as
  the owner produces a decorative policy. The API and worker connect as
  `aco_app`, explicitly `NOSUPERUSER NOBYPASSRLS`; migrations keep using the
  owner, which is why they still run. Policies are `FORCE`d so the guarantee
  holds even when the owner queries.
- **`oauth_states` and `channel_connections` are deliberately excluded.** Both
  are read *before* the tenant is known — a callback arrives with a state hash,
  a webhook with a shop domain — and a policy keyed on the current tenant cannot
  express "look this up to discover the tenant". They stay under application
  filters, and both are looked up by a value the caller had to prove it held.
  Once those lookups resolve, `declare_tenant()` adopts the tenant and the rest
  of the transaction is scoped like any other.

Verified against the live database rather than assumed: connected as `aco_app`
with no tenant declared, `operator_actions` and `audit_events` return 0 rows;
with one declared, that tenant's rows and no others. Through the API, two tokens
for two tenants see their own actions out of three in the table.

`users` and `stores` have no `store_id` to key a policy on and are not covered;
they are reached through the tenant that owns them.

### Surviving the database going away

A pooled connection outlives the server on the other end of it. When PostgreSQL
restarts — maintenance, a failover, a container replaced — every connection in
the pool is already dead, and a pool without pre-ping hands them out anyway: one
500 per stale connection, on every restart. Verified by restarting the database
under a running API: the first request answered 500 before `pool_pre_ping`, and
200 after.

The second half is the claim the dead process was holding. An idempotency record
sits in `processing` for two reasons that need opposite answers — the operation
is in flight, or its process stopped existing. Only the first should be refused;
treating both as "in progress" turns one crash into an operation that can never
be run again under that key, which is the opposite of what an idempotency key is
for. Claims older than `STALE_CLAIM_SECONDS` are taken over, along with any
`failed` claim, and the previous attempt's half-written response is cleared with
them.

This is the same shape as the webhook rule above, and the same trade in both
directions: reclaiming eagerly runs an operation twice, refusing eagerly loses it.

### Throttling is a pause of a known length

Shopify's GraphQL limit is a leaky bucket priced in **query cost**, not a count of
requests, and a throttle arrives as HTTP 200 carrying a `THROTTLED` error rather
than a 429. Two mistakes follow from missing that.

Treated as a hard error, a whole sync fails over a pause. Worse, retrying the
*job* restarts pagination from page one — spending more budget than the attempt
that was throttled, so the retry deepens the problem it is meant to solve. That
is why it is handled per request, inside `execute`, and pagination resumes from
its cursor instead of starting again.

The wait is arithmetic, not a guess. Shopify reports the cost it wanted and the
rate the bucket refills at, so:

    wait = (requestedQueryCost - currentlyAvailable) / restoreRate

capped, with a small margin because refill is continuous and our clock is not.
Guessing is what turns a pause into an outage: too short and every retry is
throttled again, too long and a sync stalls for nothing.

A 401 is still not a pause — waiting cannot un-revoke a token — and a test holds
that line.

### Withdrawn access is an ending, not a failure

A 401 or 403 from a channel is not a bad minute; it means the merchant
uninstalled the app or revoked its access, and no number of retries makes a
revoked token valid. Treated as a generic error it would burn three RQ retries
over twelve minutes, leave the channel reading "connected", and never tell anyone
to reconnect.

`ShopifyAuthorizationError` is raised separately for those two statuses. The sync
then marks the channel disconnected with the reason, deletes the credential — a
token Shopify has revoked is not a secret worth keeping, only a liability —
audits it as `shopify.access_revoked`, and returns rather than raising, so the
job settles instead of retrying. The screen names what happened rather than
quietly offering an empty "connect a store" form.

A 500 stays transient and still raises: only 401 and 403 mean stop.

### Read-after-write: the claim rests on proof, not on a 200

The payroll says money was saved. That rests on the change having *happened* — not
on our having sent a request and received a 200. So every real mutation is
bracketed, and `change_verification.py` judges the pair of readings:

    before = read()      # precondition: is it already there?
    mutate()
    after  = read()      # postcondition: is it there now, because of us?

Only a witnessed change becomes `evidence_mode="real"` and reaches the total.
Three cases are refused, each named rather than left to arithmetic:

- **Accepted but unchanged.** The API said yes and the account did not move.
- **Already in place.** The term was negated before we acted, so the saving is
  not ours to claim — the most flattering mistake available here.
- **Read-back failed.** The write may well have worked; we cannot show it, so it
  is recorded as unproven rather than guessed either way.

The provenance of the *input* is not the provenance of the *outcome*: an action
born from a real advertising report still starts `unverified`, because the report
being real says nothing about whether the change landed.

Writing to an ad account is opt-in (`ADS_WRITE_ENABLED`, default off) and separate
from reading it. With it off the scan reads, judges and stages, and says so on
each row rather than implying it acted.

---

## Safety and multi-tenancy

- **Auth** (`security.py`) — dependency-free JWT. Every request binds a
  `Principal` (user, tenant, role); `crud.py` scopes reads and refuses
  cross-tenant writes.
- **Signing in** (`routers/auth.py`, with `accounts.py`, `auth_email.py`,
  `auth_google.py`, `mailer.py`) — the only endpoints that answer without a
  bearer token. They take a proof of an email address — a Google credential, or
  a code mailed to it — and return the same token `security.py` already knows
  how to check: no second notion of identity, no session table. Everyone who
  signs in is `owner` of a tenant `ensure_account` creates for them, so the role
  grants nothing over anybody else. `GET /api/auth/config` tells the browser
  which doors this deployment actually has open, so one frontend build serves a
  deployment with Google configured and one without.
- **Ending a session** (`sessions.py`, `token_sessions`) — a signature proves a
  token was minted here; it cannot prove the token is *still* meant to work.
  Every token carries a `jti` naming a row, the middleware checks that row on
  each request, and revoking is one UPDATE that takes effect on the next call.
  So signing out ends the session on the server instead of only clearing the
  browser, `GET /api/auth/sessions` shows every live way into the account (dates
  and method, never a credential), and `POST /api/auth/sessions/revoke-others`
  answers the lost laptop while sparing the session asking. Tokens minted before
  this existed carry no `jti` and are refused rather than grandfathered — an
  unrevocable token is what the table exists to abolish. There is no refresh
  token: a second credential has a second theft story and buys convenience a
  longer `SESSION_TOKEN_DAYS` already provides.
- **Staying signed in** (`frontend/app/session.tsx`) — one gate around the app
  asks `/api/auth/me` once and separates the two failures that used to look
  identical: a **refused** token sends you to sign in, while a **silent** server
  leaves the app standing so the screen can say the API is unreachable. A token
  that dies mid-visit surfaces as a 401 on some ordinary call, so `apiFetch`
  announces it and the gate listens. Public paths (`/signin`, `/privacy`,
  `/terms`, `/privacy-center`) are never gated; a deployment with
  `AUTH_ENABLED=false` answers `/me` happily and is labelled in the nav rather
  than left to be discovered.
- **Credentials** (`credential_crypto.py`) — AES-GCM, with the AAD binding each
  ciphertext to `(store_id, provider)`, so a row moved between tenants fails to
  decrypt. A versioned keyring allows rotation without downtime.
- **OAuth** — state is hashed in the database, single-use, five-minute TTL. The
  callback sets `Referrer-Policy: no-referrer` and `Cache-Control: no-store` so a
  single-use code cannot leak through a referrer or a cache.
- **Idempotency** — every mutating route takes an `Idempotency-Key`; a retried job
  returns the first result rather than repeating the work.
- **Audit** — `audit_events` records actor, action, before and after, including
  every guardrail refusal and every limit change.
- **Retries** — reads are retried, writes never are unless explicitly declared
  safe. A replayed negative-keyword create would duplicate negatives.

---

## Channels

### Feedback pilot

The public Shopify feedback pilot is limited by `PILOT_MAX_SHOPIFY_STORES`
(default: 10). A place is checked at the OAuth callback, after Shopify has
named the shop and before its credential is stored. Mere account creation and
an abandoned authorization do not consume a place. New subscriptions default
to a 15-day card-free trial (`TRIAL_DAYS=15`); the server owns that date.

**Shopify-first, settled 2026-09-04.** One channel is the product for now; the
other two are kept working but are not promised to anybody and are not
developed further until a real Shopify store has completed the whole loop. See
rule 2 in CLAUDE.md.

| Channel | State |
|---|---|
| Shopify | **Verified end to end** against a real development store: OAuth, sync, order notifications |
| Amazon SP-API | Experimental. Written and tested against mocks; needs an approved app |
| Amazon Ads | Experimental. Written and tested against mocks; needs an approved app |
| WooCommerce | Experimental. Written and tested against mocks |

The difference between *written* and *verified* is recorded here deliberately.
Amazon needs a Professional seller account plus an approved application in
Developer Central, so nothing Amazon-side has met a live account. In
`amazon_ads_api.py` the parts most likely to need correcting on first contact —
report column names and the v3 vendor content types — are isolated in
`SEARCH_TERM_COLUMNS` and `_CONTENT_TYPE` for exactly that reason.

Development tunnels get a new hostname on every restart, which silently orphans
registered webhooks. The connection response compares where notifications were
registered against where this server now listens and reports `stale` when they
differ. See [ops/TUNNEL.md](../ops/TUNNEL.md).

---

## Adding a module

The shape every module follows, in order:

1. **Write the engine tests first.** Verify the arithmetic against known cases
   before any wiring exists (`tests/test_decision_engine.py` is the model; it
   runs standalone as well as under pytest).
2. A deterministic engine under `app/` — structured data in, scored findings out.
   No LLM, no I/O.
3. A Pydantic schema in `schemas.py`.
4. A route in `app/routers/<domain>.py`.
5. An `explain_*` in `llm.py` that narrates the result and is forbidden to
   recompute it.
6. A screen under `frontend/app/` and a function in `frontend/lib/api.ts`.
7. Update this document.

## Offsite database backups

`ops/backup/offsite.py` is an independent one-shot PostgreSQL 18 backup job,
packaged with its own Dockerfile. It uses PG* environment variables and a
private external HTTPS S3-compatible bucket. It validates dump structure,
uploads a unique object, verifies the entire download by checksum and size,
and only then writes a success manifest. It does not import the application,
modify production rows, or share storage credentials with application services.
Daily scheduling, retention policies and failure/freshness alerts require
deployment setup; see `ops/backup/README.md`. A read-back check is distinct from
the isolated restore drill, and neither proves product impact.

## The data moat

Every evaluation, recommendation and priced action is persisted. Over time this
history of what sellers were shown, what they chose, and what it was actually
worth becomes the thing a competitor cannot copy by writing the same prompts.
