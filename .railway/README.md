# Railway Infrastructure as Code

`railway.ts` is the single source of truth for the production Railway project.
It creates PostgreSQL, Redis, API, worker, scheduler and frontend services.

Before the first plan, create the Shared Variables listed in
`../railway/VARIABLES.md`. Never put their values in this repository.

Preview only (Railway CLI 5.42+; on Windows, npm installs the CLI as a
`.cmd` shim the IaC engine cannot spawn, so point it at the real binary first:
`$env:_ = "$env:APPDATA\npm\node_modules\@railway\cli\bin\railway.exe"`):

    railway config plan

Apply only after reviewing that the plan contains no unexpected deletes:

    railway config apply

The old per-service `railway/*.toml` mechanism is deprecated and must not be
selected in a service's Config File field.

Custom domains are the one deliberate dashboard step: after applying, add
`api.aicommerceoperator.com` to `ai-commerce-operator` and
`app.aicommerceoperator.com` to `frontend`. Railway's IaC planner currently
refuses to register a new custom domain from code.
