# Roadmap — what's left to build

Status as of this plan. Done: **Product Hunter** (engine + what-if + multi-compare +
score breakdown), **PPC Analyzer** (engine + screen), LLM layer with OpenAI⇄Anthropic
fallback (en/ru), persistence (SQLite dev / Postgres prod), evaluation history.

The golden rule still holds everywhere we go: **the engine computes money & verdicts,
the LLM only explains.** Every new module follows the pattern in ARCHITECTURE.md
(engine + tests → schema → route → LLM → screen).

---

## Phase 1 — Foundation: make it real & multi-user

1. **Auth** (FastAPI JWT or Supabase). Replace the single `dev@local` user. Issue
   tokens, protect routes, attach every evaluation/analysis to the real `user_id`.
   Frontend: login screen + session + authorized API calls.
2. **Persist PPC analyses.** Product Hunter already persists; PPC does not. Wire
   `POST /api/ppc/analyze` into the `recommendations` table (it already exists) and
   add a history/read endpoint, mirroring product-hunter history.
3. **Real data ingestion** (the big one). SP-API → products, prices, sales; Ads API →
   campaigns, keywords, spend. Replace manual entry with "connect store → import".
   Needs real Amazon seller credentials and OAuth token storage (the `stores` table
   already reserves `sp_api_refresh_token` — store encrypted).
4. **Migrations (Alembic).** Today tables come from `Base.metadata.create_all`. Before
   real data lands, switch to versioned migrations so schema changes are safe.

## Phase 2 — More modules (same pattern, high product value)

5. **Inventory engine** — `inventory_engine.py`: sales-velocity forecast, days-of-cover,
   reorder date, stockout risk, suggested reorder quantity. Engine + tests → schema →
   `POST /api/inventory/analyze` → LLM `explain_inventory` → screen.
6. **Listing optimizer** — score title / bullets / keywords / images; flag gaps and
   suggest improvements (LLM rewrites copy, engine scores quality).
7. **Daily AI report** — aggregates findings from all modules, ranks by impact, and
   produces one prioritized "what matters today" digest. This is the headline feature
   of the product vision.

## Phase 3 — Product & UX polish

8. **Dashboard home** — overview across modules (top opportunities, total wasted spend,
   reorder alerts) instead of landing straight on a tool.
9. **CSV import/export** — bulk-load candidates / Ads reports; export results. Removes
   the biggest friction (manual typing) before SP-API integration is ready.
10. **Save / name / reload analyses**, compare over time (the data-moat history made useful).
11. **Frontend hygiene** — extract shared components (verdict/action badges, `Stat`,
    input grid are duplicated across pages), proper loading/empty/error states,
    client-side input validation.

## Phase 4 — Hardening & operations

12. **API integration tests** — current tests cover engines + LLM chain only. Add
    FastAPI `TestClient` tests for `/evaluate`, `/ppc/analyze`, `/history` (happy path +
    validation 422s + persistence).
13. **CI** — GitHub Actions: `pytest`, `tsc --noEmit`, lint, on every push.
14. **ESLint** — `next lint` has no config yet; add one.
15. **Deployment** — Railway/Render per the stack (Docker is ready). Managed Postgres,
    env/secrets management, prod CORS (tighten from the dev `localhost:*` regex).
16. **Security** — rotate the OpenAI key currently sitting in `.env` on disk; add rate
    limiting; never log secrets; consider per-user usage limits on LLM calls.
17. **Observability** — structured logging, error tracking (e.g. Sentry), basic metrics.

---

## Smaller tech-debt items

- Pin the frontend dev port (done: 3002) and document it in the README.
- Make LLM model names configurable per provider via env (partly done via `LLM_MODEL`).
- Cache the dev-user lookup (minor) once auth replaces it anyway.
- README "Quick start" assumes bash; add Windows/PowerShell notes.

## Suggested order (highest value first)

`Auth → Persist PPC + history → CSV import → Inventory engine → Daily report → ingestion → deploy`

Rationale: auth + CSV make the app genuinely usable by a real seller *today* without
waiting on Amazon API access; Inventory + Daily report deliver the "AI COO" promise;
ingestion and deployment turn it into a live product.
