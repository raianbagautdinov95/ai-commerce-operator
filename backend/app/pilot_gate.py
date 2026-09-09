"""Whether this deployment may take money from a stranger.

`preflight` answers a narrower question — is the configuration fit to hold a
merchant's credentials. Everything it checks can be read out of the environment,
which is why it can be trusted to answer on its own.

Charging somebody is a larger claim, and most of what makes it true is not in
any variable. Whether the leaked API key was actually revoked, whether a backup
has ever left the platform, whether a Stripe price exists — a process can see
that a value is *present* and never that the thing it names was *done*. A gate
that scores those by reading configuration would return READY for a deployment
whose owner had done none of it.

So the report has two halves that are never added together:

  CHECKED    things this process verified, by reading or by connecting.
  ATTESTED   things only a person can confirm. Outstanding until they say so,
             and their word is what the record then rests on.

And one line that is neither, because it is the product's own claim: an operator
that has never measured a result has not yet earned the money it is asking for.
Every service can answer 200 and that line still reads "not proven".

    python -m app.pilot_gate                 # this process's environment
    python -m app.pilot_gate --live          # also reach the database and queue

Exit 0 only when nothing is outstanding in either half.
"""
from __future__ import annotations

import os
import sys

from urllib.parse import urlparse

from . import preflight
from .preflight import EPHEMERAL_TUNNEL_HOSTS

#: Things a person does elsewhere and then tells us about. The variable is an
#: attestation, not a measurement: setting it is a claim by whoever set it, and
#: the wording says so, because a checkbox that reads like a test result is how
#: a gate ends up certifying nothing.
ATTESTATIONS = (
    ("PILOT_SECRETS_ROTATED",
     "Leaked credentials replaced",
     "KEEPA_API_KEY and SHOPIFY_CLIENT_SECRET have both appeared in a log or in "
     "git history. Rotate both at the provider, then set this. See ops/SECRETS.md."),
    ("PILOT_BACKUP_OFFSITE",
     "A backup exists outside this platform",
     "Railway's own snapshots live in the same project under the same account: "
     "they survive a bad migration, not a deleted project. See ops/backup/."),
    ("PILOT_BACKUP_RESTORED",
     "That backup has been restored at least once",
     "A backup nobody has restored is a hope. ops/backup/restore-drill.sh, "
     "against a scratch database, with EXPECT_MIN_ROWS=1."),
    ("PILOT_STRIPE_PRICE_LIVE",
     "The Stripe price exists and Checkout was completed once",
     "Create the product and price in Stripe, put its id in STRIPE_PRICE_*, and "
     "buy it once yourself in test mode. See railway/VARIABLES.md."),
    ("PILOT_TERMS_PUBLISHED",
     "Terms and Privacy say what this actually does",
     "Both pages exist in the app; what they say is your claim, and a pilot "
     "customer is agreeing to it. Read them before charging anyone."),
    ("PILOT_EMAIL_DELIVERY",
     "A sign-in email has arrived somewhere real",
     "Sign-in is by emailed code, so undelivered mail is a customer who cannot "
     "get in at all. python -m app.preflight --live --send-test-email."),
    ("PILOT_PORTAL_OPENS",
     "The Billing Portal opens",
     "Enable the customer portal in Stripe and open it once. A subscriber who "
     "cannot cancel without emailing you is a complaint waiting to happen."),
    ("PILOT_SUPPORT_CONTACT",
     "Somebody is reading the support address",
     "The address being configured is checked above; that a person reads it is "
     "not something this can see. See ops/PILOT.md."),
)


def _attestation_checks(env) -> list[preflight.Check]:
    checks = []
    for variable, name, fix in ATTESTATIONS:
        done = (env.get(variable) or "").strip().lower() == "true"
        checks.append(preflight.Check(
            name, done, preflight.BLOCKER,
            "attested" if done else "not attested",
            "" if done else f"{fix} Then set {variable}=true.",
        ))
    return checks


def _stripe_checks(env) -> list[preflight.Check]:
    """Configuration only. Whether the price is real is an attestation above."""
    required = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_OPERATOR",
                "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "STRIPE_PORTAL_RETURN_URL")
    missing = [name for name in required if not (env.get(name) or "").strip()]
    checks = [preflight.Check(
        "Stripe configured", not missing, preflight.BLOCKER,
        "set" if not missing else f"missing: {', '.join(missing)}",
        "" if not missing else "Billing returns 503 without these, so nobody can pay.",
    )]
    # The Checkout return URLs must be stable and public: Stripe sends the
    # customer to them after paying, and a tunnel hostname reissued overnight
    # lands somebody on nothing right after taking their money.
    for name in ("STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "STRIPE_PORTAL_RETURN_URL"):
        value = (env.get(name) or "").strip()
        if not value:
            continue
        host = urlparse(value).hostname or ""
        ephemeral = any(host.endswith(suffix) for suffix in EPHEMERAL_TUNNEL_HOSTS)
        ok = value.startswith("https://") and not ephemeral
        checks.append(preflight.Check(
            f"Return URL: {name}", ok, preflight.BLOCKER,
            host or value,
            "" if ok else ("Stripe returns the customer here after paying. It has "
                           "to be an https address that still exists tomorrow."),
        ))

    # Enforcement is what makes the plan mean anything. Checked structurally
    # because a gate that trusts a variable saying "yes we gate" gates nothing.
    import inspect
    from . import entitlement as _entitlement
    from .routers import commerce as _commerce, shopify as _shopify
    gated = all("entitlement.require_access" in inspect.getsource(module)
                for module in (_commerce, _shopify))
    checks.append(preflight.Check(
        "Paid work is gated", gated, preflight.BLOCKER,
        "sync and analysis check entitlement" if gated else "not enforced",
        "" if gated else ("A limit enforced only by hiding a button is not a "
                          "limit; the endpoints have to refuse."),
    ))

    key = (env.get("STRIPE_SECRET_KEY") or "").strip()
    if key:
        live = key.startswith("sk_live_")
        environment = (env.get("APP_ENV") or "development").lower()
        # A test key in production takes no money; a live key anywhere else takes
        # real money from a developer pressing buttons.
        consistent = live == (environment in {"production"})
        checks.append(preflight.Check(
            "Stripe key matches the environment", consistent, preflight.BLOCKER,
            f"{'live' if live else 'test'} key in {environment}",
            "" if consistent else (
                "A live key outside production charges real cards during testing; "
                "a test key in production takes no money at all."),
        ))
    return checks


def _support_checks(env) -> list[preflight.Check]:
    """An address and a promise, both readable. Whether anyone answers is an
    attestation; whether a customer can find them at all is not."""
    email = (env.get("SUPPORT_EMAIL") or "").strip()
    response = (env.get("SUPPORT_RESPONSE_TIME") or "").strip()
    return [
        preflight.Check(
            "Support address", bool(email) and "@" in email, preflight.BLOCKER,
            email or "unset",
            "" if email else ("Set SUPPORT_EMAIL. A pilot customer who hits a "
                              "problem needs somewhere to go that is not guesswork. "
                              "Not a personal address in the source: it is shown "
                              "on every onboarding error."),
        ),
        preflight.Check(
            "Support response time", bool(response), preflight.BLOCKER,
            response or "unset",
            "" if response else ("Set SUPPORT_RESPONSE_TIME to something you will "
                                 "meet, e.g. 'same business day'. An unstated one "
                                 "is promised as instant and remembered as never."),
        ),
    ]


def _notification_checks(env) -> list[preflight.Check]:
    """That a customer can be told their payment failed.

    Every check here is configuration or code. Whether a message actually
    arrived is the PILOT_EMAIL_DELIVERY attestation, because a process cannot
    see somebody's inbox.
    """
    from . import notifications

    checks = []
    provider = bool((env.get("RESEND_API_KEY") or "").strip())
    sender = (env.get("LOGIN_EMAIL_FROM") or "").strip()
    app_url = (env.get("PUBLIC_APP_URL") or "").strip()

    checks.append(preflight.Check(
        "Email provider", provider, preflight.BLOCKER,
        "set" if provider else "unset",
        "" if provider else ("Set RESEND_API_KEY. Sign-in is by emailed code, so "
                             "without it nobody can get in at all."),
    ))
    checks.append(preflight.Check(
        "Email sender", bool(sender) and "@" in sender, preflight.BLOCKER,
        sender or "unset",
        "" if sender else "Set LOGIN_EMAIL_FROM to an address on a verified domain.",
    ))
    stable = app_url.startswith("https://") and not any(
        app_url.endswith(suffix) or suffix in app_url
        for suffix in EPHEMERAL_TUNNEL_HOSTS)
    checks.append(preflight.Check(
        "Billing link in emails", stable, preflight.BLOCKER,
        app_url or "unset",
        "" if stable else ("Set PUBLIC_APP_URL to the stable https address. A link "
                           "in an email outlives the deploy that sent it, and a "
                           "tunnel hostname is dead by the time anybody clicks."),
    ))

    # The four templates have to exist and be renderable, because the first time
    # anybody finds out otherwise is when a customer is not told their payment
    # failed.
    missing = []
    for kind in notifications.KINDS:
        try:
            message = notifications.render(kind, context={"ends_on": "2026-01-01"})
            if not message.subject or not message.text:
                missing.append(kind)
        except Exception:  # noqa: BLE001
            missing.append(kind)
    checks.append(preflight.Check(
        "Subscription email templates", not missing, preflight.BLOCKER,
        f"{len(notifications.KINDS)} present" if not missing
        else f"broken: {', '.join(missing)}",
        "" if not missing else "One of the four service emails cannot be rendered.",
    ))

    # The worker has to consume the queue they are put on, or they sit there.
    from .worker import DEFAULT_QUEUES
    consumed = "notifications" in DEFAULT_QUEUES
    checks.append(preflight.Check(
        "Worker consumes the email queue", consumed, preflight.BLOCKER,
        ", ".join(DEFAULT_QUEUES),
        "" if consumed else "Queued emails would never be delivered.",
    ))
    return checks


#: One line per daily stage: what it is called on the report, and what charging
#: somebody before it has ever run would mean.
_SCHEDULER_STAGES = (
    ("trial_warnings", "Trial warnings have run",
     "An unsent trial warning is a customer surprised by the end of their "
     "trial."),
    ("shopify_sync", "The daily Shopify read has run",
     "Nothing would be reading the shop. Every screen would keep showing the "
     "numbers it already had, and no measurement could ever complete."),
    ("measurement", "The measurement run has fired",
     "Applied actions would never be priced. The product's whole claim is that "
     "it measures what it did, and nothing would be doing the measuring."),
)


def _scheduler_checks() -> list[preflight.Check]:
    """That each daily stage is actually firing.

    Reads the database, so it only runs with --live. Asked per stage rather than
    once: they are three jobs in one cron entry, and two of them succeeding says
    nothing about the third. The absence of a run is the only evidence any of
    them will ever produce.
    """
    from . import scheduler
    from .db.session import SessionLocal

    db = SessionLocal()
    try:
        seen = {job: scheduler.hours_since_success(db, job=job)
                for job, _, _ in _SCHEDULER_STAGES}
    except Exception as exc:  # noqa: BLE001
        return [preflight.Check(
            "The daily jobs have run", False, preflight.BLOCKER,
            f"cannot tell: {type(exc).__name__}",
            "The scheduler's own record could not be read.")]
    finally:
        db.close()

    checks = []
    for job, label, consequence in _SCHEDULER_STAGES:
        hours = seen[job]
        if hours is None:
            checks.append(preflight.Check(
                label, False, preflight.BLOCKER, "never",
                "Nothing has run this stage yet. Add the cron service from "
                f"railway/scheduler.toml and let it fire once before charging "
                f"anybody — {consequence[0].lower()}{consequence[1:]}"))
            continue
        ok = hours <= scheduler.stale_after_hours(job)
        checks.append(preflight.Check(
            label, ok, preflight.BLOCKER, f"{int(hours)}h ago",
            "" if ok else
            "This stage has stopped firing, and it fails silently. " + consequence))
    return checks


def _measurement_check(env) -> preflight.Check:
    """The product's own claim, which no amount of uptime can stand in for."""
    return preflight.Check(
        "A measured result exists", False, preflight.WARNING,
        "not proven — no action has completed its measurement window",
        "This is not a deployment fault and cannot be fixed by configuration. "
        "Until one proposal has been approved, carried out, witnessed and then "
        "measured over its full window, the Operator has not shown it earns more "
        "than it costs. Sell a pilot on that basis knowingly, or wait.",
    )


def run(env=None, argv: list[str] | None = None) -> tuple[list, list, preflight.Check]:
    """(checked, attested, the measurement line)."""
    env = os.environ if env is None else env
    argv = argv or []
    # Judged as a deployment regardless of APP_ENV: this question is only ever
    # asked about one.
    checked = preflight.run_checks(env, argv=["--production"])
    checked += _stripe_checks(env)
    checked += _support_checks(env)
    checked += _notification_checks(env)
    if "--live" in argv:
        checked += preflight.live_checks(env)
        checked += _scheduler_checks()
    return checked, _attestation_checks(env), _measurement_check(env)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    argv = sys.argv[1:] if argv is None else argv
    source = preflight.load_environment(argv)
    checked, attested, measurement = run(argv=argv)

    print("\nPilot gate — may this deployment take money from a stranger?")
    print(f"Reading: {source}\n")

    print("CHECKED — verified by this process")
    print(preflight.render(checked))
    print("\nATTESTED — nobody can verify these from here; your word is the record")
    print(preflight.render(attested))
    print("\nTHE PRODUCT'S OWN CLAIM")
    print(preflight.render([measurement]))
    print()

    technical = [c for c in checked if c.blocking]
    manual = [c for c in attested if c.blocking]
    if technical or manual:
        print(f"NOT READY FOR A PAYING PILOT: {len(technical)} technical blocker(s), "
              f"{len(manual)} outstanding owner action(s)")
        if technical:
            print("  technical: " + ", ".join(c.name for c in technical))
        if manual:
            print("  yours:     " + ", ".join(c.name for c in manual))
        return 1

    print("Configuration and attestations are complete.")
    print("This still says nothing about whether the Operator earns its fee — see")
    print("the line above. Services answering 200 is not evidence of value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
