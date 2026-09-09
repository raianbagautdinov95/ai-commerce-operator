"""
Tests for read-after-write verification. Runs with pytest OR standalone
(`python test_change_verification.py`).

Every case here is a way a mutation can appear to succeed while the money claim
behind it is false.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.change_verification import ChangeOutcome, verify_presence

TERM = "cheap dog bowl"


def test_a_witnessed_change_is_the_only_thing_counted_as_real():
    outcome = verify_presence(expected=TERM, before=["free bowl"],
                              after=["free bowl", TERM], external_id="neg-1")
    assert outcome.changed and outcome.verified
    assert outcome.evidence_mode == "real"
    assert outcome.countable is True
    assert outcome.external_id == "neg-1"
    assert "absent before and present after" in outcome.reason


def test_an_accepted_request_that_did_nothing_is_not_counted():
    """The API said yes and the account is unchanged. This is the quiet failure."""
    outcome = verify_presence(expected=TERM, before=["free bowl"], after=["free bowl"])
    assert outcome.changed is False and outcome.verified is False
    assert outcome.evidence_mode == "unverified"
    assert "did not take effect" in outcome.reason


def test_work_that_was_already_done_is_never_claimed_as_ours():
    """The most flattering mistake available: counting someone else's negation."""
    outcome = verify_presence(expected=TERM, before=[TERM], after=[TERM])
    assert outcome.countable is False
    assert "already in place before we acted" in outcome.reason


def test_a_failed_read_back_is_unproven_not_assumed_either_way():
    outcome = verify_presence(expected=TERM, before=["free bowl"], after=[],
                              read_back_failed=True)
    assert outcome.changed is False and outcome.verified is False
    assert "could not be read back" in outcome.reason


def test_matching_ignores_case_and_padding():
    outcome = verify_presence(expected="  Cheap Dog Bowl ",
                              before=[], after=["CHEAP DOG BOWL"])
    assert outcome.countable is True


def test_an_unnamed_target_proves_nothing():
    assert verify_presence(expected="   ", before=[], after=["x"]).countable is False


def test_an_empty_before_is_a_normal_first_negation():
    outcome = verify_presence(expected=TERM, before=None, after=[TERM])
    assert outcome.countable is True
    assert outcome.before == []


def test_the_outcome_carries_both_readings_for_the_audit_trail():
    outcome = verify_presence(expected=TERM, before=["b"], after=["b", TERM])
    data = outcome.to_dict()
    assert data["before"] == ["b"]
    assert sorted(data["after"]) == sorted(["b", TERM])
    assert data["evidence_mode"] == "real"


def _run_standalone():
    cases = [
        ("witnessed change", ["free bowl"], ["free bowl", TERM], False),
        ("accepted, did nothing", ["free bowl"], ["free bowl"], False),
        ("already negated", [TERM], [TERM], False),
        ("read-back failed", ["free bowl"], [], True),
    ]
    for label, before, after, failed in cases:
        o = verify_presence(expected=TERM, before=before, after=after, read_back_failed=failed)
        print(f"{label:24} -> {o.evidence_mode:11} countable={str(o.countable):5} {o.reason}")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    _run_standalone()
