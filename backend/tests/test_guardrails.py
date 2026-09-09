"""
Tests for the guardrails. Runs with pytest OR standalone
(`python test_guardrails.py`).

These encode the promise made to a seller who hands over their ad account:
nothing huge, nothing unattended above a threshold, nothing at all when off.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.guardrails import (DEFAULT_POLICY, SUGGESTED_UNATTENDED_POLICY, Decision,
                            Policy, Request, Verdict, evaluate, remaining_today)

# What a store gets before it configures anything: nothing runs unattended.
POLICY = Policy.from_dict(DEFAULT_POLICY)
# What a seller moves to once they have watched it and opted in.
OPTED_IN = Policy.from_dict(SUGGESTED_UNATTENDED_POLICY)


def test_a_new_store_lets_nothing_happen_unattended():
    """The default is observe -> suggest -> approve. Trust is earned per store."""
    assert POLICY.auto_apply_below == 0.0
    d = evaluate(Request(money_at_stake=0.01, change_pct=0.01,
                         target_spend_per_day=1.0, applied_today=0), POLICY)
    assert d.verdict is Verdict.APPROVAL
    assert d.limits_hit == ["auto_apply_below"]


def test_a_small_unattended_change_is_allowed_once_the_seller_opts_in():
    d = evaluate(Request(money_at_stake=8.0, change_pct=0.10,
                         target_spend_per_day=12.0, applied_today=3), OPTED_IN)
    assert d.verdict is Verdict.ALLOWED
    assert d.allowed is True


def test_the_kill_switch_beats_everything_else():
    off = Policy.from_dict({**DEFAULT_POLICY, "enabled": False})
    # Even a change that would otherwise sail through.
    d = evaluate(Request(actor="human", money_at_stake=0.0, change_pct=0.0), off)
    assert d.verdict is Verdict.BLOCKED
    assert d.limits_hit == ["enabled"]
    assert "switched off" in d.reason


def test_an_oversized_change_is_blocked_even_for_a_human():
    d = evaluate(Request(actor="human", change_pct=-0.60), POLICY)
    assert d.verdict is Verdict.BLOCKED
    assert d.limits_hit == ["max_change_pct"]
    assert "60%" in d.reason and "20%" in d.reason


def test_direction_of_the_change_does_not_matter():
    up = evaluate(Request(change_pct=0.45), POLICY)
    down = evaluate(Request(change_pct=-0.45), POLICY)
    assert up.verdict is down.verdict is Verdict.BLOCKED


def test_a_human_is_not_bound_by_the_unattended_limits():
    """The daily budget bounds the Operator acting alone, not the owner acting."""
    d = evaluate(Request(actor="human", money_at_stake=5000.0,
                         target_spend_per_day=900.0, applied_today=999), POLICY)
    assert d.verdict is Verdict.ALLOWED


def test_the_daily_budget_stops_unattended_work():
    d = evaluate(Request(applied_today=20), POLICY)
    assert d.verdict is Verdict.BLOCKED
    assert d.limits_hit == ["max_actions_per_day"]
    assert remaining_today(POLICY, 20) == 0
    assert remaining_today(POLICY, 25) == 0        # never negative
    assert remaining_today(POLICY, 6) == 14


def test_real_money_needs_a_human():
    d = evaluate(Request(money_at_stake=120.0, target_spend_per_day=5.0), OPTED_IN)
    assert d.verdict is Verdict.APPROVAL
    assert d.limits_hit == ["auto_apply_below"]
    assert "120.00 is at stake" in d.reason


def test_a_big_spender_is_never_touched_unattended():
    d = evaluate(Request(money_at_stake=1.0, target_spend_per_day=400.0), OPTED_IN)
    assert d.verdict is Verdict.APPROVAL
    assert d.limits_hit == ["protected_spend_per_day"]


def test_both_reasons_are_reported_together():
    d = evaluate(Request(money_at_stake=300.0, target_spend_per_day=400.0), OPTED_IN)
    assert d.verdict is Verdict.APPROVAL
    assert d.limits_hit == ["protected_spend_per_day", "auto_apply_below"]
    assert "spends 400.00 a day" in d.reason and "300.00 is at stake" in d.reason


def test_a_block_outranks_an_approval():
    """An oversized change is refused, not merely escalated."""
    d = evaluate(Request(money_at_stake=5000.0, change_pct=0.9,
                         target_spend_per_day=5000.0), OPTED_IN)
    assert d.verdict is Verdict.BLOCKED


def test_an_unconfigured_store_gets_the_safe_defaults():
    p = Policy.from_dict(None)
    assert p.enabled is True and p.max_actions_per_day == 20
    assert p.max_change_pct == 0.20
    assert p.auto_apply_below == 0.0        # nothing unattended until asked for


def test_a_partial_policy_keeps_the_remaining_defaults():
    p = Policy.from_dict({"max_actions_per_day": 5})
    assert p.max_actions_per_day == 5
    assert p.max_change_pct == DEFAULT_POLICY["max_change_pct"]


def _run_standalone():
    cases = [
        ("tiny change, new store", Request(money_at_stake=0.5, change_pct=0.1,
                                           target_spend_per_day=2.0)),
        ("120 EUR at stake", Request(money_at_stake=120.0)),
        ("huge bid cut", Request(change_pct=-0.6)),
        ("daily budget spent", Request(applied_today=20)),
    ]
    for label, req in cases:
        d = evaluate(req, POLICY)
        print(f"{label:26} -> {d.verdict.value:18} {d.reason}")
    off = evaluate(Request(actor="human"), Policy.from_dict({**DEFAULT_POLICY, "enabled": False}))
    print(f"{'operator switched off':26} -> {off.verdict.value:18} {off.reason}")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    _run_standalone()
