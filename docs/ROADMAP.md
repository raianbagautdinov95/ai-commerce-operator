# Roadmap and launch status

Status: **Shopify-first MVP is complete and verified locally as of 2026-09-16.**

The core product is built: deterministic decision engines, guardrails,
multi-tenant authentication, encrypted connection credentials, billing and
entitlements, durable background work, Shopify OAuth/sync/webhooks, inventory,
PPC, daily reports, action measurement, and a web UI. Shopify is the only
channel verified end to end against a real development store.

Quality verification on 2026-09-16:

- Backend: 914 tests passed; 31 integration-dependent tests are intentionally
  skipped when their external services are unavailable.
- Frontend: 45 tests passed; TypeScript check and production build passed.
- The production safety gate and pilot gate are in the codebase. They must be
  run again in the actual hosted environment before anyone is charged.

The product rule remains unchanged: deterministic engines compute money and
verdicts; the LLM only explains them. A new store never receives unattended
actions until the seller opts in.

## What is ready now

1. **Shopify-first seller workflow.** A merchant can connect a Shopify store,
   import data, review deterministic recommendations, approve an action, and
   later see whether it was measured.
2. **Safety and tenant boundaries.** Authentication, tenant isolation,
   credential encryption, webhook validation, retries, and guarded automation
   are implemented and covered by tests.
3. **Commercial foundation.** Stripe entitlement checks, subscription emails,
   support links, privacy/terms pages, production Compose configuration, Caddy
   TLS configuration, monitoring hooks, and backup procedures exist.
4. **Local operation and demos.** The application can run locally using the
   documented startup flow and sample data without claiming sample numbers are
   measured merchant profit.

## Remaining work before a public paid launch

These are deployment and owner decisions, not missing product screens.

1. **Deploy one stable production environment.** Choose the host, attach a
   permanent HTTPS domain, provision PostgreSQL and Redis, and configure the
   public frontend/API URLs. Do not use a temporary tunnel as a customer URL.
2. **Complete the production configuration.** Populate the production-only
   values in `.env.example`: public URL, support address and response time,
   database connection, Stripe live key/webhook/price/return URLs, monitoring,
   and offsite-backup destination. Never put real values in the repository.
3. **Set up billing and support outside the code.** Create live Stripe products
   and the customer portal; verify the email sender domain and send a real
   login email; publish a support contact that somebody monitors.
4. **Set up an installable Shopify app.** Configure the production Shopify app
   URL and HTTPS OAuth callback in Shopify Partners, then complete one fresh
   install and webhook delivery on the deployed domain.
5. **Run the paid-pilot gate on the deployed stack.** Complete the required
   backup/restore, secret rotation, terms review, email, Stripe and support
   attestations. Then run `python -m app.pilot_gate --live` inside the live API
   container. It must report no blockers before taking payment.
6. **Pilot with a small group first.** Start with 3–5 Shopify stores. The first
   goal is to observe an approved action through its measurement window, not to
   claim unmeasured financial results.
7. **Create the public sales layer.** Add a landing page, onboarding material,
   pricing copy, and a clear statement that the product is Shopify-first.

## Deliberately not part of the public Shopify-first promise

- Amazon SP-API and Amazon Ads are implemented and tested against mocks, but
  have not been verified with a live approved seller account.
- WooCommerce is experimental and not a public launch channel.
- Real supplier data needs a separately chosen and funded data provider.

Do not add another commerce channel before the Shopify pilot has proven the
complete loop.

## Completion definition

The platform is ready for a paying public pilot only when the deployed
environment passes `app.preflight --live` and `app.pilot_gate --live`, the
required human attestations are true, a Shopify install works from the public
domain, and the support/billing paths have each been exercised once.
