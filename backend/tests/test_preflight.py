"""
Tests for the preflight check. Runs with pytest OR standalone
(`python test_preflight.py`).

The check exists so "are we ready?" stops being a matter of opinion, so what it
must never do is say READY on a configuration that would expose a merchant.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import base64
import json

from app import preflight
from app.preflight import BLOCKER, WARNING, main, render, run_checks

READY = {
    "APP_ENV": "production",
    "AUTH_ENABLED": "true",
    "JWT_SECRET": "s" * 48,
    "DATABASE_URL": "postgresql+psycopg://aco:pw@db:5432/aco",
    "QUEUE_ENABLED": "true",
    "REDIS_URL": "redis://redis:6379/0",
    "CREDENTIAL_ENCRYPTION_KEYS": json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}),
    "CREDENTIAL_ACTIVE_KEY_VERSION": "v1",
    "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    "SHOPIFY_REDIRECT_URI": "https://app.example.com/api/integrations/shopify/callback",
    "SENTRY_DSN": "https://key@sentry.example/1",
    "LEGAL_ENTITY": "Northwind Commerce OU",
    "LEGAL_ADDRESS": "Tartu mnt 67, 10115 Tallinn, Estonia",
    "PRIVACY_CONTACT": "privacy@northwind.example",
    "ADS_WRITE_ENABLED": "false",
}


# Every helper judges as production unless a test says otherwise. A localhost
# origin is correct on a laptop and disqualifying on Railway, so the check now
# asks which it is looking at; these tests are about the strict reading.
_AS_PRODUCTION = ["--production"]


def _by_name(env, argv=_AS_PRODUCTION):
    return {c.name: c for c in run_checks(env, argv=argv)}


def _blockers(env, argv=_AS_PRODUCTION):
    return [c.name for c in run_checks(env, argv=argv) if c.blocking]


def test_a_fully_configured_deployment_passes():
    assert _blockers(READY) == []


def test_the_development_default_fails_on_everything_that_matters():
    """This is today's .env. It must never read as ready for production."""
    failed = _blockers({})
    for name in ("Environment", "Authentication", "Token secret", "Database",
                 "Background queue", "Redis", "Credential encryption", "Browser origins",
                 "Privacy policy"):
        assert name in failed, name


def test_authentication_off_is_a_blocker_and_says_why():
    check = _by_name({**READY, "AUTH_ENABLED": "false"})["Authentication"]
    assert check.blocking
    assert "same tenant" in check.detail
    assert "identity proxy" in check.fix        # the shortcut for one pilot user


def test_a_short_token_secret_is_refused():
    assert "Token secret" in _blockers({**READY, "JWT_SECRET": "short"})
    assert "Token secret" not in _blockers({**READY, "JWT_SECRET": "x" * 32})


def test_sqlite_is_refused_and_named():
    check = _by_name({**READY, "DATABASE_URL": "sqlite:///./aco_dev.db"})["Database"]
    assert check.blocking and check.detail == "SQLite"
    assert "locked" in check.fix


def test_a_missing_database_url_does_not_silently_pass():
    check = _by_name({**READY, "DATABASE_URL": ""})["Database"]
    assert check.blocking
    assert "SQLite" in check.detail


def test_a_credential_keyring_must_be_complete_and_the_right_size():
    short = json.dumps({"v1": base64.b64encode(b"tooshort").decode()})
    assert "Credential encryption" in _blockers({**READY, "CREDENTIAL_ENCRYPTION_KEYS": short})

    orphan = {**READY, "CREDENTIAL_ACTIVE_KEY_VERSION": "v9"}
    check = _by_name(orphan)["Credential encryption"]
    assert check.blocking and "not in the keyring" in check.detail

    assert "Credential encryption" in _blockers({**READY, "CREDENTIAL_ENCRYPTION_KEYS": "{oops"})


def test_cors_must_be_explicit_https():
    assert "Browser origins" in _blockers({**READY, "CORS_ALLOWED_ORIGINS": ""})
    assert "Browser origins" in _blockers(
        {**READY, "CORS_ALLOWED_ORIGINS": "http://app.example.com"})
    assert "Browser origins" not in _blockers(
        {**READY, "CORS_ALLOWED_ORIGINS": "https://a.example.com, https://b.example.com"})


def test_a_throwaway_tunnel_is_caught_before_it_orphans_webhooks():
    env = {**READY,
           "SHOPIFY_WEBHOOK_URI": "https://ipod-remembered-piano-epa.trycloudflare.com/api/webhooks/shopify"}
    check = _by_name(env)["Callback: SHOPIFY_WEBHOOK_URI"]
    assert check.blocking
    assert "throwaway tunnel" in check.detail
    assert "changes on every restart" in check.fix


def test_a_plain_http_callback_is_caught():
    check = _by_name({**READY, "SP_API_REDIRECT_URI": "http://example.com/cb"})[
        "Callback: SP_API_REDIRECT_URI"]
    assert check.blocking and "not https" in check.detail


def test_an_unset_callback_is_not_invented():
    """Only channels that are actually configured are checked."""
    names = _by_name({**READY, "SHOPIFY_REDIRECT_URI": ""})
    assert "Callback: SHOPIFY_REDIRECT_URI" not in names


def test_missing_error_reporting_warns_without_blocking():
    check = _by_name({**READY, "SENTRY_DSN": ""})["Error reporting"]
    assert check.severity == WARNING
    assert check.blocking is False
    assert _blockers({**READY, "SENTRY_DSN": ""}) == []


def test_unattended_writes_are_flagged_but_do_not_block():
    check = _by_name({**READY, "ADS_WRITE_ENABLED": "true"})["Unattended writes"]
    assert check.severity == WARNING and check.ok is False
    assert "proposes and a person applies" in check.fix


def test_the_report_prints_the_fix_next_to_the_failure():
    text = render(run_checks({**READY, "DATABASE_URL": "sqlite:///x.db"}))
    assert "[FAIL] Database" in text
    assert "locked" in text
    assert "[OK  ] Authentication" in text


def test_the_exit_code_gates_a_deploy(monkeypatch, capsys):
    monkeypatch.setattr(os, "environ", dict(READY))
    assert main(["--no-env-file"]) == 0
    assert "READY" in capsys.readouterr().out

    monkeypatch.setattr(os, "environ", {**READY, "AUTH_ENABLED": "false"})
    assert main(["--no-env-file"]) == 1
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "Do not point a real store at this deployment." in out


def test_the_check_reads_the_same_env_file_the_app_would(tmp_path, monkeypatch):
    """A check that reports configured settings as missing teaches you to distrust it."""
    from app.preflight import load_environment

    env_file = tmp_path / ".env"
    env_file.write_text("JWT_SECRET=" + "z" * 48 + "\nAPP_ENV=production\n", encoding="utf-8")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    source = load_environment(["--env-file", str(env_file)])
    assert source == os.path.abspath(str(env_file))
    assert os.environ["JWT_SECRET"] == "z" * 48
    assert os.environ["APP_ENV"] == "production"      # the file wins, so it is checked


def test_a_missing_env_file_is_named_rather_than_silently_ignored(tmp_path, monkeypatch):
    from app.preflight import load_environment
    monkeypatch.chdir(tmp_path)
    source = load_environment(["--env-file", str(tmp_path / "nope.env")])
    assert "no .env found" in source


def test_a_container_environment_can_refuse_to_read_any_file(tmp_path, monkeypatch):
    """In a container the orchestrator supplies the config; a stray .env must not answer."""
    from app.preflight import load_environment

    (tmp_path / ".env").write_text("JWT_SECRET=from-the-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    source = load_environment(["--no-env-file"])
    assert "--no-env-file" in source
    assert "JWT_SECRET" not in os.environ


def _run_standalone():
    print("development default:")
    print(render(run_checks({})))
    print("\nfully configured:")
    print(render(run_checks(READY)))
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        if fn.__code__.co_argcount == 0:
            fn()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    _run_standalone()


# --- the delivery check must not go off by itself ---------------------------
#
# Every other check reads something. This one puts a message in a mailbox
# somebody owns, so the thing worth testing hardest is that it stays still.


def test_no_email_is_sent_without_the_flag(monkeypatch):
    """Not on a plain run, and not on --live either."""
    sent = []
    monkeypatch.setattr(preflight, "delivery_check",
                        lambda *a, **k: sent.append(1) or [])
    monkeypatch.setattr(preflight, "live_checks", lambda *a, **k: [])
    monkeypatch.setattr(preflight, "run_checks", lambda *a, **k: [])
    monkeypatch.setattr(preflight, "load_environment", lambda argv: "test")

    preflight.main([])
    preflight.main(["--live"])
    assert sent == [], "a delivery check ran without being asked for"


def test_the_flag_is_what_sends_it(monkeypatch):
    sent = []
    monkeypatch.setattr(preflight, "delivery_check",
                        lambda *a, **k: sent.append(1) or [])
    monkeypatch.setattr(preflight, "live_checks", lambda *a, **k: [])
    monkeypatch.setattr(preflight, "run_checks", lambda *a, **k: [])
    monkeypatch.setattr(preflight, "load_environment", lambda argv: "test")

    preflight.main(["--send-test-email"])
    assert sent == [1]


def test_it_refuses_to_guess_a_recipient():
    """The obvious guess is a customer's address."""
    checks = preflight.delivery_check({})
    assert len(checks) == 1
    assert not checks[0].ok
    assert "PREFLIGHT_EMAIL" in checks[0].detail


def test_the_recipient_is_masked_in_the_result(monkeypatch):
    """A preflight result is pasted around far more casually than a log."""
    from app import mailer
    monkeypatch.setattr(mailer, "send_probe",
                        lambda to: (mailer.DELIVERY_OK, "accepted by Resend (HTTP 200)"))
    checks = preflight.delivery_check({"PREFLIGHT_EMAIL": "raian@eucompliancehq.com"})

    rendered = checks[0].detail
    assert "raian@eucompliancehq.com" not in rendered
    assert "eucompliancehq.com" in rendered, "enough to recognise which mailbox"
    assert checks[0].ok


def test_a_rejected_sender_domain_says_what_to_publish(monkeypatch):
    from app import mailer
    monkeypatch.setattr(mailer, "send_probe",
                        lambda to: (mailer.DELIVERY_SENDER_REJECTED,
                                    "HTTP 403 — domain is not verified"))
    check = preflight.delivery_check({"PREFLIGHT_EMAIL": "a@b.com"})[0]
    assert not check.ok
    assert check.blocking
    assert "DKIM" in check.fix


# --- local and production are different questions ---------------------------

def _local_env():
    """A working laptop stack: real Postgres and Redis, localhost browser, tunnel."""
    return {**READY, "APP_ENV": "development",
            "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
            "SHOPIFY_REDIRECT_URI": "https://abc.trycloudflare.com/cb",
            "SHOPIFY_WEBHOOK_URI": "https://abc.trycloudflare.com/wh"}


def test_a_working_local_stack_is_not_reported_as_broken():
    """http://localhost is what a local browser sends. Calling that a blocker
    reports a correct setting as a fault, and a gate that cries wolf at a
    developer four times a day stops being read before it ever sees production."""
    assert _blockers(_local_env(), argv=[]) == []


def test_a_throwaway_tunnel_is_still_mentioned_locally():
    """It really does orphan the webhooks registered against it — worth knowing,
    not worth stopping for on a laptop."""
    checks = {c.name: c for c in run_checks(_local_env(), argv=[])}
    tunnel = checks["Callback: SHOPIFY_REDIRECT_URI"]
    assert not tunnel.ok and tunnel.severity == WARNING


def test_the_same_settings_are_blockers_for_a_deployment():
    """The whole point: what is fine locally must stop a release."""
    failed = _blockers(_local_env())
    for name in ("Environment", "Browser origins",
                 "Callback: SHOPIFY_REDIRECT_URI", "Callback: SHOPIFY_WEBHOOK_URI"):
        assert name in failed, name


def test_production_settings_are_judged_strictly_without_the_flag():
    """APP_ENV says what it is; the flag is only for checking a production file
    from a laptop."""
    env = {**READY, "CORS_ALLOWED_ORIGINS": "http://app.example.com"}
    assert "Browser origins" in _blockers(env, argv=[])


# --- a report made to be pasted somewhere ------------------------------------

def test_the_redis_url_is_reported_without_its_password():
    """Found by running this against production, where it printed the whole URL.

    A managed Redis URL carries its password in the userinfo, and this report is
    written to be read out loud, pasted into an issue and kept in CI output. The
    host is what somebody is checking; the password must not travel with it.
    """
    from app import preflight

    env = {"QUEUE_ENABLED": "true",
           "REDIS_URL": "redis://default:hunter2secret@redis.railway.internal:6379"}
    detail = " ".join(check.detail for check in preflight._check_queue(env))

    assert "hunter2secret" not in detail
    assert "default:" not in detail
    assert "redis.railway.internal" in detail, "the host is the useful part"


def test_a_plain_redis_url_still_reads_normally():
    """A redactor that mangles the ordinary case is one somebody works around."""
    from app import preflight

    checks = preflight._check_queue({"QUEUE_ENABLED": "true",
                                     "REDIS_URL": "redis://redis:6379/0"})
    detail = " ".join(check.detail for check in checks)
    assert "redis://redis:6379/0" in detail


def test_an_unparseable_url_degrades_to_silence_not_to_the_secret():
    """The failure mode of a redactor has to be saying less, never more."""
    from app import preflight

    detail = preflight._without_credentials("://:@@@not a url")
    assert "@" not in detail and detail == "set"


def test_no_other_check_prints_a_whole_credential():
    """The database URL carries a password too; it is reported only as a kind."""
    from app import preflight

    check = preflight._check_database(
        {"DATABASE_URL": "postgresql://aco_app:hunter2secret@db:5432/aco"})
    assert "hunter2secret" not in check.detail
    assert check.detail == "PostgreSQL"
