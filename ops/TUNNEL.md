# Reaching this server from Shopify and Amazon

Both platforms call back over HTTPS to a public address: the OAuth redirect and,
for Shopify, every order notification. `localhost` is not reachable from either,
so development needs a tunnel.

## The problem with a quick tunnel

A no-account Cloudflare tunnel is one command:

    cloudflared tunnel --url http://localhost:8000

It prints a hostname like `ipod-remembered-piano-epa.trycloudflare.com`. That
hostname **is issued fresh on every start**. When it changes:

- `SHOPIFY_REDIRECT_URI` and `SHOPIFY_WEBHOOK_URI` in `.env` are wrong,
- the redirect URL registered in the Shopify Partner Dashboard is wrong,
- and — the quiet one — the webhook subscriptions already registered with Shopify
  still point at the old address. Shopify keeps calling it and gets nothing.
  No error surfaces at either end.

The app now catches that last case: `GET /api/integrations/shopify/account`
compares the address the subscriptions were registered against with the one this
server currently listens on, and reports `notifications: "stale"` when they differ.
The Shopify screen shows it and offers **Point them here**, which deletes the
subscriptions aimed elsewhere and recreates them.

That makes the failure visible and one click to fix. It does not make it stop
happening. For anything beyond a day of testing, use a named tunnel.

## A named tunnel (stable hostname)

Needs a free Cloudflare account and a domain on it. No server: Cloudflare
routes the hostname to whatever machine is running `cloudflared`, so a laptop
serves a real HTTPS address.

**Use a name of its own.** A tunnel points at the API on port 8000, and under
`ops/RAILWAY.md` `app.` is the frontend while `api.` is the backend. Reusing
either here means one DNS record has to mean two different things the day both
exist, and the one that loses is whichever was set last.

    cloudflared tunnel login
    cloudflared tunnel create ai-commerce-operator
    cloudflared tunnel route dns ai-commerce-operator dev.eucompliancehq.com
    cloudflared tunnel run --url http://localhost:8000 ai-commerce-operator

The hostname now survives restarts. Set it once and stop thinking about it:

    SHOPIFY_REDIRECT_URI=https://dev.eucompliancehq.com/api/integrations/shopify/callback
    SHOPIFY_WEBHOOK_URI=https://dev.eucompliancehq.com/api/webhooks/shopify

and register the same redirect URL in the Shopify Partner Dashboard. A
development store can hold its own redirect URL, so this need not disturb
whatever a production app is registered with.

## After any address change

1. Update both URIs in `.env` and restart the API.
2. Update the redirect URL in the Partner Dashboard.
3. Open the Shopify screen and press **Point them here**.

Step 3 is what moves the existing subscriptions; without it they keep pointing at
the old address whatever `.env` says.

## Checking it end to end

    curl https://<host>/health                       # 200 through the tunnel
    curl -X POST https://<host>/api/webhooks/shopify \
         -H 'Content-Type: application/json' -d '{}'  # 401: unsigned, correctly refused

Then create a test order in the development store (Orders → Create order → Mark as
paid). A row should appear in `webhook_deliveries`. Until one does, the delivery
path is unproven no matter what the subscription list says.
