# Secrets: where they live, and how to replace one

## The rule that decides everything else

A secret that has appeared in a log, a traceback, a screenshot, a chat message
or a git commit is spent. Scrubbing the output afterwards does not un-spend it:
you cannot know who read it in between. Redaction stops the *next* leak; only
rotation deals with the one that happened.

Two here have leaked and must be replaced by hand. Nothing in this repository
can do it, because it means signing in to somebody else's dashboard:

| Secret | How it got out | Where to replace it |
|---|---|---|
| `KEEPA_API_KEY` | `httpx` put the request URL, key and all, into the message of a failed `raise_for_status()`, and the traceback reached the log | keepa.com account → API key → regenerate |
| `SHOPIFY_CLIENT_SECRET` | an early commit; the fingerprint is in git history | Shopify Partner Dashboard → the app → API credentials → rotate |

## What is redacted now, and what that does not cover

`app/redaction.py` hides the value of anything named like a credential —
`key`, `token`, `code`, `hmac`, `state`, `secret`, `password`, anything ending
`_key`/`_token`/`_secret`/`_code`, anything containing `secret`/`password` —
wherever it appears: a URL, a query string, the middle of an exception message.
It is wired into the access log, every root log handler, and the Keepa client,
which also rewrites the original exception's own arguments so a chained
`__context__` carries nothing either.

It does **not** cover: anything written to stdout by a library that bypasses
logging, a secret in a variable name rather than a parameter name, or a value
that never had a name attached. Do not treat it as permission to be careless.

## Replacing one

1. Create the new value at the provider. Do not revoke the old one yet.
2. Put it in the deployment's secret store — Railway variables, or the
   server's `.env.production`. **Never** in `.env.example`, a commit, an issue
   or a message.
3. Restart the services that read it: API and worker for `KEEPA_API_KEY` and
   `SHOPIFY_CLIENT_SECRET`; API only for `STRIPE_*`.
4. Verify: `python -m app.preflight --live --no-env-file` for presence, and one
   real call — a Shopify sync, a discovery search — for validity.
5. Now revoke the old value, and only now. Revoking first turns a rotation into
   an outage.

## The ones that are not like the others

**`JWT_SECRET`** signs sessions. Changing it signs everybody out on every
device and invalidates pending sign-in codes. That is the blunt instrument for
"somebody has a token they should not"; `token_sessions` exists so one laptop
can be dealt with by revoking one row instead.

**`CREDENTIAL_ENCRYPTION_KEYS`** is a keyring, not a key, precisely so it can be
rotated without downtime: add `v2` while keeping `v1`, set
`CREDENTIAL_ACTIVE_KEY_VERSION=v2`, re-encrypt stored integrations, confirm
every row reports `v2`, and only then drop `v1`. Removing `v1` early makes every
stored Shopify token undecryptable, which reads as every store disconnecting at
once.

**`SHOPIFY_CLIENT_SECRET`** signs three things: the OAuth callback's HMAC, every
webhook delivery, and the token exchange. It is read from the environment at each
use, never captured at import, so a rotation needs a restart of the API and the
worker — not a rebuild and not a code change.

The dangerous part is the window between the two sides. Shopify signs with the
new value the moment you save it there; a deployment still holding the old one
refuses every delivery with a 401 while its own screen goes on reporting the
subscriptions as active, because they are. Orders simply stop arriving.

That window is now visible rather than silent: refused signatures are counted,
and `GET /api/integrations/shopify/account` reports
`notifications: "failing_signature"` when a refusal is more recent than the last
delivery that verified. It clears itself once one verifies again. So the order
is:

1. Rotate in the Partner Dashboard.
2. Put the new value in the deployment's secret store.
3. Restart API and worker.
4. Watch the Shopify screen. `failing_signature` means one side is still on the
   old value; `active` means both agree.

Deliveries refused during the window are retried by Shopify, so they arrive
late rather than vanishing — but a long window is a long gap in the data.

## Where they are meant to be

- Local: `.env.production`, which is git-ignored. Check it: `git check-ignore
  .env.production` should print the path.
- Railway: variables on each service. `railway/*.toml` names them and does not
  hold them.
- Never: `.env.example`, which is committed and exists only to name the
  variables and their shape.
