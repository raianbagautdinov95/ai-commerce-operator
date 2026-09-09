# CLAUDE.md — instructions for the AI engineer

You are the lead engineer for **AI Commerce Operator**, an AI operating system
for online sellers. Build like a startup CTO: scalable architecture, clean
modular code, but **MVP speed over completeness** — do not overengineer early.

## Golden rules

1. **The LLM never computes money or verdicts.** All economics, scores and
   decisions live in deterministic Python engines (`decision_engine.py` and
   future `*_engine.py`). The LLM only explains. Protect this boundary.
2. **Shopify-first. No new channel until one store's result is confirmed.**
   Settled on 2026-09-04, replacing the original "Amazon sellers only" rule.
   The reason is evidence, not preference: Shopify is the only channel that has
   ever run against a live account — OAuth, sync and order notifications, end
   to end. Amazon SP-API/Ads and WooCommerce stay in the codebase as
   **experimental**: they are not promised to a user, not shown as ready, and
   not developed further until the first Shopify store has completed the whole
   loop — data → finding → proposal → your approval → the change → a measured
   result. Until then, work that deepens Amazon or Woo is work not spent
   proving the thing that pays.
   Do not add a fourth channel. Do not quietly widen the third.
3. **Keep changes additive.** The DB schema and module pattern are designed to
   grow without rewrites — follow the existing shape (see docs/ARCHITECTURE.md).
4. **Every engine ships with tests.** Verify math against known cases before
   wiring up UI. See `backend/tests/test_decision_engine.py`.
5. **Document as you go.** Update ARCHITECTURE.md when you add a module.

## Project map

- `backend/app/*_engine.py` — the deterministic core: decision, ppc, inventory,
  report, actions. Plus `guardrails.py` (what may run unattended).
- `backend/app/main.py` — the application only: config checks, middleware,
  mounting. Routes live in `backend/app/routers/`, one module per domain.
- `backend/app/llm.py` — provider-agnostic explanation layer. Never arithmetic.
- `backend/app/tasks.py` — RQ worker entrypoints. Must never import `main`.
- `backend/app/db/` — SQLAlchemy models + crud, tenant-scoped.
- `frontend/app/` — one screen per module; `frontend/lib/api.ts` — API client.
- `docs/ARCHITECTURE.md` — the layering, the loop, and the module-adding pattern.

## Two things that must not regress

- **A new store runs nothing unattended.** `guardrails.DEFAULT_POLICY` sets
  `auto_apply_below: 0`; the seller opts in. Trust is earned per store.
- **Money that was not measured is never shown as if it had been.** Profit is
  withheld without real costs, demo rows are labelled, and only actions with
  `evidence_mode="real"` reach the payroll total. When in doubt the number goes
  down, never up.

## Adding a module (PPC / Inventory / Listing)

Follow the pattern in ARCHITECTURE.md: deterministic engine → Pydantic schema →
FastAPI route → LLM explanation → frontend screen + api client. Write the engine
tests first.

## Build order (matches roadmap)

1. Auth (Supabase or FastAPI JWT) + persist evaluations.
2. SP-API + Ads API ingestion → real product/campaign data.
3. PPC engine (ACOS analysis, wasted-spend, negative-keyword suggestions).
4. Inventory engine (sales forecast, reorder date, stockout risk).
5. Daily AI report that prioritizes only what matters.

## Definition of done for any task

- Tests pass (`python tests/test_decision_engine.py` and any new tests).
- No secrets committed; new config goes through `.env.example`.
- ARCHITECTURE.md updated if a module/endpoint was added.
