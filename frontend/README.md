# Frontend (Next.js)

The two files that matter are already here:

- `app/page.tsx` — the AI Product Finder screen (MVP screen #3).
- `lib/api.ts` — typed client for the backend API.

## Scaffold the Next.js app around them

```bash
# from the repo root
npx create-next-app@latest frontend --ts --tailwind --app --eslint --src-dir=false
# when prompted, allow it to use this folder; then copy app/page.tsx and lib/api.ts back in
```

Or, in a fresh Next.js + Tailwind project, just drop `app/page.tsx` and `lib/api.ts` in place.

## Run

```bash
# point the client at the backend (defaults to http://localhost:8000)
echo "NEXT_PUBLIC_API_BASE=http://localhost:8000" > .env.local
npm run dev
# open http://localhost:3000
```

Make sure the backend is running (`uvicorn app.main:app --reload`) so the
Evaluate button has an API to call.

> Tip: build the rest of the MVP screens (Dashboard, PPC Analyzer, Inventory
> Alerts, Daily Report) the same way — a typed `lib/api.ts` function per backend
> endpoint, a page that calls it. Add shadcn/ui (`npx shadcn@latest init`) for
> polished components when you're past the rough MVP.
