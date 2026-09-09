"""Deliver the one email this application sends: a sign-in code.

There is exactly one rule worth stating here. In development, an unconfigured
mailer writes the code to the log, because a solo developer without an email
provider still needs to sign in. In staging or production it refuses instead —
writing sign-in codes into a production log turns log access into account
access, and "it worked on my machine" is precisely how that ships.
"""
from __future__ import annotations

import os

import httpx

from .auth_email import CODE_TTL_MINUTES
from .runtime import log

RESEND_ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0


class MailerNotConfigured(RuntimeError):
    """No way to deliver mail, in an environment where logging it is not acceptable."""


def _api_key() -> str:
    return (os.getenv("RESEND_API_KEY") or "").strip()


def _sender() -> str:
    return (os.getenv("LOGIN_EMAIL_FROM") or "").strip()


def email_delivery_configured() -> bool:
    return bool(_api_key() and _sender())


def _may_log_codes() -> bool:
    return os.getenv("APP_ENV", "development").lower() in {"development", "test"}


def login_email_possible() -> bool:
    """Can this server get a code to somebody, one way or another?

    True when a mailer is configured, and also in development, where the code
    goes to the log instead. Callers use this to refuse *before* issuing a code
    rather than after — a code nobody can receive still spends the address's
    hourly allowance, which locks the person out of the door that does work.
    """
    return email_delivery_configured() or _may_log_codes()


def _body(code: str) -> tuple[str, str]:
    subject = f"{code} is your sign-in code"
    text = (
        f"Your sign-in code is {code}.\n\n"
        f"It expires in {CODE_TTL_MINUTES} minutes and can be used once.\n\n"
        "If you did not ask to sign in, ignore this email — somebody typed your "
        "address by mistake, and nothing has happened to your account.\n"
    )
    return subject, text


def send_login_code(email: str, code: str) -> None:
    """Deliver `code` to `email`, or raise."""
    if not email_delivery_configured():
        if _may_log_codes():
            log.warning("Email delivery is not configured. Sign-in code for %s: %s",
                        email, code)
            return
        raise MailerNotConfigured(
            "Email sign-in is unavailable: set RESEND_API_KEY and LOGIN_EMAIL_FROM.")

    subject, text = _body(code)
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={"from": _sender(), "to": [email], "subject": subject, "text": text},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Say what the provider said. The first time this is configured the
        # failure is almost always "domain is not verified" or a bad key, and
        # an exception class name identifies neither — somebody then goes
        # looking at the code instead of at their DNS.
        #
        # The code itself is only ever in the request we sent, never in the
        # reply, and Resend's errors do not echo the body. The recipient is
        # already in the log a line earlier when a sign-in succeeds, so the
        # only thing deliberately kept out is the code.
        log.error("Could not deliver a sign-in code: %s", _delivery_failure(exc))
        raise MailerNotConfigured("Could not send the email. Try again in a moment.") from exc


#: What went wrong, in the terms the person fixing it would use. The four are
#: separated because they are fixed in four different places: the API key page,
#: the DNS records, the plan, and the network.
DELIVERY_OK = "ok"
DELIVERY_BAD_KEY = "bad_key"
DELIVERY_SENDER_REJECTED = "sender_rejected"
DELIVERY_RATE_LIMITED = "rate_limited"
DELIVERY_UNREACHABLE = "unreachable"
DELIVERY_REFUSED = "refused"

#: Advice per outcome, so the check does not have to know about Resend.
DELIVERY_FIX = {
    DELIVERY_BAD_KEY:
        "RESEND_API_KEY is not accepted. Create one at resend.com under API "
        "Keys and set it; a key from another account or a revoked one fails "
        "exactly like this.",
    DELIVERY_SENDER_REJECTED:
        "Resend will not send as LOGIN_EMAIL_FROM. The domain has to be added "
        "under Domains and its DNS records published — SPF and DKIM at least. "
        "Until then nothing is delivered, and once they exist mail still needs "
        "them to stay published.",
    DELIVERY_RATE_LIMITED:
        "Resend is rate limiting this account. Wait, or raise the plan; the "
        "key and the domain are both fine.",
    DELIVERY_UNREACHABLE:
        "The request never reached Resend. Check outbound HTTPS from this "
        "host, and whether api.resend.com resolves from it.",
    DELIVERY_REFUSED:
        "Resend refused the message. The reason it gave is in the detail above.",
}


def classify_delivery_error(exc: httpx.HTTPError) -> tuple[str, str]:
    """Turn a failed send into (outcome, a description safe to print).

    The description never contains the API key or the recipient: a preflight
    result is pasted into issues and chat far more casually than a log is read.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return DELIVERY_UNREACHABLE, f"{type(exc).__name__} — the request never completed"

    status = response.status_code
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("name") or "")
    except ValueError:
        message = ""

    lowered = message.lower()
    if status in (401, 403) and ("api key" in lowered or "unauthor" in lowered or not message):
        outcome = DELIVERY_BAD_KEY
    elif "domain" in lowered or "from" in lowered or "verif" in lowered:
        outcome = DELIVERY_SENDER_REJECTED
    elif status == 429 or "rate" in lowered:
        outcome = DELIVERY_RATE_LIMITED
    elif status in (401, 403):
        outcome = DELIVERY_BAD_KEY
    else:
        outcome = DELIVERY_REFUSED
    return outcome, f"HTTP {status}{' — ' + message if message else ''}"


class MailerRejected(RuntimeError):
    """The provider refused it and will refuse it again — a bad address, a
    sender the domain does not authorise. Retrying spends attempts on an answer
    that will not change."""


def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> None:
    """One transactional email. Raises `MailerNotConfigured` for something worth
    retrying and `MailerRejected` for something that will fail the same way
    every time.

    That distinction is the whole reason this is separate from
    `send_login_code`: a queued job needs to know whether trying again is
    sensible, and an exception class is how it finds out.
    """
    if not email_delivery_configured():
        raise MailerNotConfigured(
            "Email is not configured: set RESEND_API_KEY and LOGIN_EMAIL_FROM.")
    payload = {"from": _sender(), "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json=payload, timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        outcome, _ = classify_delivery_error(exc)
        # The recipient is not logged: this runs for every customer, and a log
        # of who was emailed about a failed payment is a log worth not having.
        log.error("Transactional email failed (%s): %s", outcome,
                  _delivery_failure(exc))
        if outcome in {DELIVERY_BAD_KEY, DELIVERY_SENDER_REJECTED, DELIVERY_REFUSED}:
            raise MailerRejected(outcome) from exc
        raise MailerNotConfigured(outcome) from exc


def send_probe(recipient: str) -> tuple[str, str]:
    """Actually send one message, to prove the whole path works.

    A configured key is not a working mailer: the domain can be unverified, the
    key revoked, the plan exhausted. That gap is the same one the channels
    screen exists for — configured and answering are different claims — and the
    only way to close it is to send something.

    Never called by the application. Only by `preflight --send-test-email`,
    which requires the flag and a PREFLIGHT_EMAIL to send to.
    """
    if not email_delivery_configured():
        return DELIVERY_REFUSED, "RESEND_API_KEY or LOGIN_EMAIL_FROM is not set"

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={
                "from": _sender(),
                "to": [recipient],
                "subject": "Operator delivery check",
                "text": ("This message exists to prove that sign-in codes can "
                         "reach an inbox from this deployment.\n\n"
                         "Nobody asked to sign in and no code is in it.\n"),
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return classify_delivery_error(exc)
    return DELIVERY_OK, f"accepted by Resend (HTTP {response.status_code})"


def _delivery_failure(exc: httpx.HTTPError) -> str:
    """The shortest description that would let somebody fix it."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__} — the request never completed"

    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            # Resend answers {"statusCode":…, "name":…, "message":…}
            detail = str(payload.get("message") or payload.get("name") or "")
    except ValueError:
        detail = ""
    return f"HTTP {response.status_code}{' — ' + detail if detail else ''}"
