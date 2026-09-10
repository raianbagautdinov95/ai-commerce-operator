"""
Preflight — is this configuration fit to face the public internet?

`_validate_runtime_safety` already refuses to start an unsafe production
process, but it fails on the *first* problem and only at boot. That makes
"are we ready?" a question you answer by repeatedly crashing a deployment.

This module answers it all at once, from outside, before anything is deployed:

    python -m app.preflight              # check the current environment
    python -m app.preflight --live       # also try to reach the database and queue
    python -m app.preflight --send-test-email   # and post one real email

`--send-test-email` is separate from `--live` on purpose. Everything else here
reads: it opens a connection, asks a question, closes it. That one *sends a
message to a mailbox somebody owns*, so it needs PREFLIGHT_EMAIL and a flag
that says what it does. A key being present is not a working mailer — the
domain can be unverified and nothing tells you until a merchant cannot sign in.

Every failed check says what to do about it. Exit code is 1 if any blocker
fails, so a deploy pipeline can gate on it.

The checks are deliberately about *this* application, not a generic hardening
list. A merchant connecting a real store hands over an access token to their
own business; the question is whether this configuration is fit to hold it.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

BLOCKER = "blocker"
WARNING = "warning"

# Hostnames handed out fresh on every restart. A webhook registered against one
# of these is orphaned the next time the tunnel comes up.
EPHEMERAL_TUNNEL_HOSTS = ("trycloudflare.com", "ngrok.io", "ngrok-free.app",
                          "loca.lt", "serveo.net")


@dataclass
class Check:
    name: str
    ok: bool
    severity: str
    detail: str
    fix: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == BLOCKER and not self.ok


def _get(env, key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def _flag(env, key: str) -> bool:
    return _get(env, key).lower() == "true"


#: What this run is judging. A localhost origin is correct on a laptop and
#: disqualifying on Railway, so the same finding cannot carry the same weight in
#: both. Reporting a developer's own machine as NOT READY four times a day is
#: how a gate stops being read.
PRODUCTION = "production"
LOCAL = "local"


def target_of(env, argv: list[str] | None = None) -> str:
    """Which kind of deployment these settings claim to be.

    `--production` forces the strict reading, so a Railway configuration can be
    judged from a laptop with `--env-file`.
    """
    if argv and "--production" in argv:
        return PRODUCTION
    value = _get(env, "APP_ENV", "development").lower()
    return PRODUCTION if value in {"staging", "production"} else LOCAL


def _severity_for(target: str) -> str:
    """Blocking in production; advisory where the setting is legitimately local."""
    return BLOCKER if target == PRODUCTION else WARNING


def _check_environment(env, target: str) -> Check:
    value = _get(env, "APP_ENV", "development").lower()
    if target == LOCAL:
        return Check(
            "Environment", True, WARNING,
            f"APP_ENV={value or 'unset'} — judged as a local stack",
        )
    return Check(
        "Environment", value in {"staging", "production"}, BLOCKER,
        f"APP_ENV={value or 'unset'}",
        "Set APP_ENV=production. Below that, every safety check below is skipped by design.",
    )


def _check_auth(env) -> list[Check]:
    enabled = _flag(env, "AUTH_ENABLED")
    checks = [Check(
        "Authentication", enabled, BLOCKER,
        "on" if enabled else "OFF — every request is treated as the same tenant",
        "Set AUTH_ENABLED=true. Without it the middleware skips authentication "
        "entirely, so role checks do nothing and one store is shared by everyone "
        "who finds the URL. If a single pilot user makes a login unnecessary, put "
        "the whole app behind an identity proxy instead and keep it off the open internet.",
    )]
    secret = _get(env, "JWT_SECRET")
    checks.append(Check(
        "Token secret", len(secret.encode()) >= 32, BLOCKER,
        f"{len(secret.encode())} bytes" if secret else "missing",
        "JWT_SECRET must be at least 32 bytes: python -c \"import secrets;"
        "print(secrets.token_urlsafe(48))\"",
    ))
    return checks


def _check_sign_in(env) -> list[Check]:
    """Can anyone but the person with shell access get in?

    Not a blocker: a deployment where the only door is `issue_token` is a
    coherent choice for a single pilot store, and it is the state this project
    shipped in. It stops being coherent the moment a stranger is supposed to be
    able to try the product, and nothing else in this list will say so.
    """
    google = bool(_get(env, "GOOGLE_CLIENT_ID"))
    mail = bool(_get(env, "RESEND_API_KEY")) and bool(_get(env, "LOGIN_EMAIL_FROM"))
    checks = [Check(
        "Sign-in", google or mail, WARNING,
        ", ".join(filter(None, ["Google" if google else "", "email code" if mail else ""]))
        or "none — only `python -m app.issue_token` can let anyone in",
        "Set GOOGLE_CLIENT_ID for Sign in with Google, or RESEND_API_KEY and "
        "LOGIN_EMAIL_FROM for codes by email. Without one of them every new user "
        "needs an administrator with a shell.",
    )]
    if _get(env, "RESEND_API_KEY") and not _get(env, "LOGIN_EMAIL_FROM"):
        checks.append(Check(
            "Sign-in email sender", False, WARNING, "LOGIN_EMAIL_FROM is missing",
            "Set LOGIN_EMAIL_FROM to a verified sender on your Resend domain, or "
            "requests for a code answer 503.",
        ))
    days = _get(env, "SESSION_TOKEN_DAYS", "7")
    ok = days.isdigit() and 1 <= int(days) <= 30
    checks.append(Check(
        "Session lifetime", ok, WARNING, f"SESSION_TOKEN_DAYS={days}",
        "Use 1-30. Tokens cannot be revoked one at a time, so their lifetime is "
        "the only revocation there is; anything outside that range is clamped.",
    ))
    return checks


_SQLITE_FIX = ("Point DATABASE_URL at PostgreSQL. SQLite allows one writer, so the API "
               "and the worker running together produce 'database is locked' under any "
               "real traffic — and production refuses to start on it.")


def _check_database(env) -> Check:
    url = _get(env, "DATABASE_URL")
    if not url:
        return Check("Database", False, BLOCKER,
                     "SQLite (unset, so it falls back to a local file)", _SQLITE_FIX)
    ok = not url.startswith("sqlite")
    return Check("Database", ok, BLOCKER,
                 "PostgreSQL" if ok else "SQLite",
                 "" if ok else _SQLITE_FIX)


def _without_credentials(url: str) -> str:
    """Where it points, never how to get in.

    A managed Redis URL carries its password in the userinfo, and this report is
    made to be read out loud, pasted into an issue and kept in CI output. The
    host is the part somebody is checking; the password is the part that must
    not travel with it.

    Anything unparseable degrades to "set" rather than to the original string —
    the failure mode of a redactor has to be silence, not the secret.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "set"
    if not parsed.hostname:
        return "set"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path if parsed.path not in ("", "/") else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{path}"


def _check_queue(env) -> list[Check]:
    enabled = _flag(env, "QUEUE_ENABLED")
    redis_url = _get(env, "REDIS_URL")
    return [
        Check("Background queue", enabled, BLOCKER,
              "on" if enabled else "off",
              "Set QUEUE_ENABLED=true and run the worker. Without it every sync "
              "endpoint answers 503, so connecting a store achieves nothing."),
        Check("Redis", bool(redis_url), BLOCKER,
              _without_credentials(redis_url) if redis_url else "missing",
              "Set REDIS_URL, e.g. redis://redis:6379/0"),
    ]


def _check_credential_keys(env) -> Check:
    raw = _get(env, "CREDENTIAL_ENCRYPTION_KEYS")
    active = _get(env, "CREDENTIAL_ACTIVE_KEY_VERSION")
    fix = ('Set CREDENTIAL_ENCRYPTION_KEYS={"v1":"<32 bytes base64>"} and '
           'CREDENTIAL_ACTIVE_KEY_VERSION=v1. Store access tokens cannot be saved without it.')
    if not raw:
        return Check("Credential encryption", False, BLOCKER, "missing", fix)
    try:
        keys = json.loads(raw)
        sizes = {v: len(base64.b64decode(k, validate=True)) for v, k in keys.items()}
    except Exception:
        return Check("Credential encryption", False, BLOCKER, "unreadable", fix)
    if any(size != 32 for size in sizes.values()):
        return Check("Credential encryption", False, BLOCKER,
                     "a key is not 32 bytes", fix)
    if active not in keys:
        return Check("Credential encryption", False, BLOCKER,
                     f"active version '{active or 'unset'}' is not in the keyring", fix)
    return Check("Credential encryption", True, BLOCKER,
                 f"{len(keys)} key(s), active '{active}'")


def _check_cors(env, target: str) -> Check:
    origins = [o.strip() for o in _get(env, "CORS_ALLOWED_ORIGINS").split(",") if o.strip()]
    detail = ", ".join(origins) if origins else "unset"
    if target == LOCAL:
        # http://localhost is what a local browser actually sends. Calling it a
        # blocker here would be reporting a correct setting as a fault.
        return Check("Browser origins", bool(origins), WARNING, detail,
                     "" if origins else "Set CORS_ALLOWED_ORIGINS; the frontend "
                                        "cannot call the API otherwise.")
    ok = bool(origins) and all(o.startswith("https://") for o in origins)
    return Check("Browser origins", ok, BLOCKER, detail,
                 "" if ok else "Set CORS_ALLOWED_ORIGINS to explicit https:// origins, "
                               "comma separated. The frontend cannot call the API otherwise.")


def _check_channel_secrets(env) -> list[Check]:
    """That the Shopify credentials are present — never what they are.

    A rotation is finished when the deployment holds the new value, and the
    check that says so must not be the thing that leaks it. Only the length and
    the first characters of the client id are shown, and nothing at all of the
    secret: a client id is public in Shopify's own install URL, a secret never
    appears anywhere.
    """
    checks = []
    client_id = _get(env, "SHOPIFY_CLIENT_ID")
    secret = _get(env, "SHOPIFY_CLIENT_SECRET")
    # Nothing to check if the channel is not configured at all.
    if not client_id and not secret:
        return checks
    checks.append(Check(
        "Shopify client id", bool(client_id), BLOCKER,
        f"{client_id[:4]}… ({len(client_id)} chars)" if client_id else "unset",
        "" if client_id else "Set SHOPIFY_CLIENT_ID from the Partner Dashboard.",
    ))
    checks.append(Check(
        "Shopify client secret", bool(secret), BLOCKER,
        f"set ({len(secret)} chars)" if secret else "unset",
        "" if secret else ("Set SHOPIFY_CLIENT_SECRET. Without it no OAuth callback "
                           "and no order notification can be verified, so every "
                           "delivery is refused. See ops/SECRETS.md to rotate one."),
    ))
    return checks


def _check_public_urls(env, target: str) -> list[Check]:
    checks = []
    severity = _severity_for(target)
    for key in ("SHOPIFY_REDIRECT_URI", "SHOPIFY_WEBHOOK_URI", "SP_API_REDIRECT_URI"):
        value = _get(env, key)
        if not value:
            continue
        host = (urlparse(value).hostname or "").lower()
        ephemeral = any(host.endswith(suffix) for suffix in EPHEMERAL_TUNNEL_HOSTS)
        secure = value.startswith("https://")
        ok = secure and not ephemeral
        if ephemeral:
            detail = f"{host} — a throwaway tunnel"
            fix = ("This hostname changes on every restart, silently orphaning "
                   "registered webhooks. Use a stable domain; see ops/TUNNEL.md.")
        elif not secure:
            detail = f"{value} — not https"
            fix = "Both Shopify and Amazon require an https callback."
        else:
            detail = host
            fix = ""
        # A throwaway tunnel is a real problem locally too - it orphans the
        # webhooks registered against it - but it is the normal way to develop,
        # so locally it is something to know rather than something to stop for.
        checks.append(Check(f"Callback: {key}", ok, severity, detail, fix))
    return checks


def _check_error_reporting(env) -> Check:
    dsn = _get(env, "SENTRY_DSN")
    return Check("Error reporting", bool(dsn), WARNING,
                 "configured" if dsn else "unset",
                 "" if dsn else "Set SENTRY_DSN, or accept that failures are only visible "
                                "in the logs of whoever thinks to look.")


def _check_blast_radius(env) -> Check:
    """Not a config error — a posture worth stating out loud before going live."""
    writes = _flag(env, "ADS_WRITE_ENABLED")
    return Check("Unattended writes", not writes, WARNING,
                 "advertising writes are ON" if writes else "off — proposes only",
                 "Consider ADS_WRITE_ENABLED=false for a first store, so the Operator "
                 "proposes and a person applies." if writes else "")


def _check_legal_identity(env) -> Check:
    """Who is accountable for the data. Not a formality: an unnamed controller
    means a data subject has nobody to exercise their rights against."""
    from .legal import details

    found = details(env)
    return Check("Privacy policy", found.complete, BLOCKER,
                 found.entity or "no legal entity named",
                 "" if found.complete else
                 "Set " + ", ".join(found.missing) + ". The privacy page says it is "
                 "incomplete until you do, which is honest but not shippable.")


def run_checks(env=None, argv: list[str] | None = None) -> list[Check]:
    env = os.environ if env is None else env
    target = target_of(env, argv)
    checks = [_check_environment(env, target)]
    checks += _check_auth(env)
    checks += _check_sign_in(env)
    checks.append(_check_database(env))
    checks += _check_queue(env)
    checks.append(_check_credential_keys(env))
    checks.append(_check_cors(env, target))
    checks += _check_public_urls(env, target)
    checks += _check_channel_secrets(env)
    checks.append(_check_legal_identity(env))
    checks.append(_check_error_reporting(env))
    checks.append(_check_blast_radius(env))
    return checks


def live_checks(env=None) -> list[Check]:
    """Actually reach the database and the queue. Config being present is not the same
    as the thing answering."""
    env = os.environ if env is None else env
    results = []

    url = _get(env, "DATABASE_URL")
    if url and not url.startswith("sqlite"):
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            results.append(Check("Database reachable", True, BLOCKER, "answered"))
        except Exception as exc:
            results.append(Check("Database reachable", False, BLOCKER,
                                 type(exc).__name__, str(exc)[:200]))

    redis_url = _get(env, "REDIS_URL")
    if redis_url:
        try:
            from redis import Redis
            Redis.from_url(redis_url, socket_connect_timeout=3).ping()
            results.append(Check("Queue reachable", True, BLOCKER, "answered"))
        except Exception as exc:
            results.append(Check("Queue reachable", False, BLOCKER,
                                 type(exc).__name__, str(exc)[:200]))
    return results


def _mask(address: str) -> str:
    """Enough of an address to recognise, not enough to reuse.

    A preflight result gets pasted into an issue or a chat window far more
    readily than a log gets read, and the recipient of a delivery check is a
    real mailbox somebody owns.
    """
    name, _, domain = address.partition("@")
    if not domain:
        return "***"
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}***@{domain}"


def delivery_check(env=None) -> list[Check]:
    """Send one real email, because a key being set proves nothing.

    Deliberately behind its own flag rather than folded into --live. Every
    other live check is a read; this one *sends something to a person*, and a
    check with a side effect that reaches somebody's inbox should never run
    because a script happened to add a flag for the database.
    """
    env = os.environ if env is None else env
    recipient = _get(env, "PREFLIGHT_EMAIL")

    if not recipient:
        return [Check(
            "Email delivery", False, BLOCKER, "PREFLIGHT_EMAIL is not set",
            "Set PREFLIGHT_EMAIL to a mailbox you can open. The check sends a "
            "real message and refuses to guess an address, because the obvious "
            "guess is a customer's.")]

    from . import mailer
    outcome, detail = mailer.send_probe(recipient)
    ok = outcome == mailer.DELIVERY_OK
    return [Check(
        "Email delivery", ok, BLOCKER,
        f"{detail} → {_mask(recipient)}" if ok else detail,
        "" if ok else mailer.DELIVERY_FIX.get(outcome, ""))]


def render(checks: list[Check]) -> str:
    width = max((len(c.name) for c in checks), default=10)
    lines = []
    for check in checks:
        mark = "OK  " if check.ok else ("FAIL" if check.severity == BLOCKER else "warn")
        lines.append(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")
        if not check.ok and check.fix:
            for piece in _wrap(check.fix, 74):
                lines.append(f"         {' ' * width}  {piece}")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def load_environment(argv: list[str]) -> str:
    """Read the same file the application would, and say which one.

    Without this the check reports configured settings as missing, which is a
    worse failure than not having the check: it teaches you to distrust it.
    `--env-file PATH` points it at a different one, so a production file can be
    checked from a development machine.
    """
    from dotenv import find_dotenv, load_dotenv

    if "--no-env-file" in argv:
        # In a container the configuration arrives from the orchestrator, and a
        # stray .env in the image would quietly answer for it.
        return "process environment only (--no-env-file)"
    path = ""
    if "--env-file" in argv:
        index = argv.index("--env-file")
        if index + 1 < len(argv):
            path = argv[index + 1]
    path = path or find_dotenv(usecwd=True)
    if not path or not os.path.exists(path):
        return "process environment only (no .env found)"
    load_dotenv(path, override=True)
    return os.path.abspath(path)


def main(argv: list[str] | None = None) -> int:
    # A Windows console defaults to a legacy codepage, so an em dash in the
    # heading or a non-ASCII character in the project path kills the run with a
    # UnicodeEncodeError. The gate that decides whether a deployment may hold
    # somebody's credentials must not be the thing that cannot print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - already unicode-safe
            pass

    argv = sys.argv[1:] if argv is None else argv
    source = load_environment(argv)
    target = target_of(os.environ, argv)
    checks = run_checks(argv=argv)
    if "--live" in argv:
        checks += live_checks()
    # Its own flag, not part of --live. Every other check reads something;
    # this one puts a message in a person's inbox, and a side effect that
    # leaves the building should need saying out loud.
    if "--send-test-email" in argv:
        checks += delivery_check()
    blockers = [c for c in checks if c.blocking]
    warnings = [c for c in checks if c.severity == WARNING and not c.ok]

    print("\nPreflight — fitness to hold a real merchant's credentials")
    print(f"Reading: {source}")
    # This describes the environment of THIS process and no other. Run on a
    # laptop it reports SQLite and no Redis while a Compose stack beside it is
    # happily serving PostgreSQL, because DATABASE_URL and REDIS_URL are set on
    # the containers rather than in .env. Both answers are true of different
    # deployments, and reading one as the other is how somebody concludes a
    # working stack is broken.
    print("Scope:   this process's environment. A containerised deployment has")
    print("         its own — check it there:")
    print("         docker compose exec api python -m app.preflight --live --no-env-file\n")
    print(render(checks))
    print()
    if blockers:
        print(f"NOT READY: {len(blockers)} blocker(s)"
              + (f", {len(warnings)} warning(s)" if warnings else ""))
        print("Do not point a real store at this deployment.")
        return 1
    label = "READY" if target == PRODUCTION else "READY for local work"
    print(label + (f" — with {len(warnings)} warning(s)" if warnings else ""))
    if target == LOCAL:
        print("This says nothing about production. Run with --production for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
