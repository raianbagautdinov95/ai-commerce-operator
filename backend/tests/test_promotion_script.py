"""The one refusal that has to work in a script nobody runs twice.

Moving a database between deployments is a thing done once, under pressure,
usually late. The failure it invites is silent: restore under a different
credential keyring and every Shopify token arrives intact and unreadable. The
next sync stops at "no readable credential", the shop is shown as disconnected,
and nothing anywhere names the transfer as the cause.

So the script refuses before it writes anything — and these tests are here
because a guard in a script that is run once is a guard nobody would notice had
stopped working.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "backup" / "promote-to-production.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None,
                                reason="no POSIX shell on this machine")

KEY_A = '{"v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
KEY_B = '{"v1":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="}'


def _run(dump: Path, *, source: str, target: str, confirm: str | None = None):
    env = {
        **os.environ,
        "TARGET_DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none",
        "SOURCE_KEYRING": source,
        "TARGET_KEYRING": target,
    }
    if confirm is not None:
        env["CONFIRM"] = confirm
    return subprocess.run(["sh", str(SCRIPT), str(dump)],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def dump(tmp_path) -> Path:
    path = tmp_path / "aco-test.dump"
    path.write_text("not a real dump", encoding="utf-8")
    return path


def test_it_exists_and_is_a_shell_script():
    assert SCRIPT.exists()
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_a_mismatched_keyring_is_refused(dump):
    """The failure the whole script exists to prevent."""
    result = _run(dump, source=KEY_A, target=KEY_B)
    assert result.returncode != 0
    assert "REFUSED" in result.stderr
    assert "keyring" in result.stderr.lower()


def test_the_keys_themselves_are_never_printed(dump):
    """A transfer log is not a place to leak a keyring. Only fingerprints."""
    result = _run(dump, source=KEY_A, target=KEY_B)
    everything = result.stdout + result.stderr
    assert "AAAAAAAA" not in everything
    assert "BBBBBBBB" not in everything
    assert "fingerprint" in everything.lower()


def test_a_matching_keyring_gets_past_the_first_gate(dump):
    """The other half: a guard that refuses everything is not a guard."""
    result = _run(dump, source=KEY_A, target=KEY_A)
    assert "REFUSED: the two deployments do not share" not in result.stderr
    # It stops at the next check instead, which is the dump's own integrity.
    assert "Checksum" in result.stderr or "checksum" in result.stderr


def test_nothing_is_written_without_an_explicit_confirmation(dump):
    """Reading the script is not the same as having decided to run it."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'CONFIRM:-' in body and '"PROMOTE"' in body
    # The restore is the only line that writes, and it is below the confirmation.
    assert body.index('CONFIRM:-') < body.index("pg_restore --exit-on-error")


def test_it_refuses_a_target_that_already_has_stores():
    """A restore into a populated database is a merge nobody designed."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "already holds" in body


def test_it_reports_counts_and_never_identities():
    """A transfer log, not a customer record."""
    body = SCRIPT.read_text(encoding="utf-8")
    reporting = body.split("Restored:", 1)[-1]
    for leak in ("email", "external_account_id", "access_token", "shop"):
        assert f"select {leak}" not in reporting.lower()
