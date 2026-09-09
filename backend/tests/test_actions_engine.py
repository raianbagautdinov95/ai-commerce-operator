"""
Tests for the Operator payroll engine. Runs with pytest OR standalone
(`python test_actions_engine.py`).

The worked case mirrors a real PPC negation:
  a keyword burning 120.50 over 14 days while returning 12.00 of sales is negated;
  over the next 14 days it costs 8.00 and returns 6.00.
  cost avoided = 112.50, revenue lost = 6.00 -> net 106.50 saved.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.actions_engine import (ActionStatus, MIN_MEASURABLE_DAYS, build_payroll,
                                measure)

BASELINE = {"days": 14, "spend": 120.50, "revenue": 12.00}
OUTCOME = {"days": 14, "spend": 8.00, "revenue": 6.00}


def test_impact_nets_lost_revenue_against_saved_spend():
    impact = measure(BASELINE, OUTCOME, requested_days=14)
    assert impact.cost_avoided == 112.50
    assert impact.revenue_gained == -6.00      # sales fell; the engine says so
    assert impact.net == 106.50                # not the flattering 112.50
    assert impact.provisional is False


def test_windows_of_different_length_are_compared_as_daily_rates():
    """Half the window, half the money: the same daily rates mean zero impact."""
    impact = measure({"days": 28, "spend": 280.0, "revenue": 56.0},
                     {"days": 14, "spend": 140.0, "revenue": 28.0}, requested_days=14)
    assert impact.net == 0.0


def test_a_short_window_is_measured_but_flagged_provisional():
    impact = measure(BASELINE, {"days": 5, "spend": 2.0, "revenue": 1.0}, requested_days=14)
    assert impact.provisional is True
    assert impact.net > 0
    assert "not yet complete" in impact.basis


def test_a_window_too_short_to_prove_anything_is_refused():
    assert measure(BASELINE, {"days": MIN_MEASURABLE_DAYS - 1, "spend": 0.0}) is None
    assert measure(BASELINE, None) is None
    assert measure(None, OUTCOME) is None
    assert measure(BASELINE, {"days": 0, "spend": 0.0}) is None


def test_a_harmful_action_reports_a_loss():
    """Raising spend without raising sales must come back negative."""
    impact = measure({"days": 14, "spend": 50.0, "revenue": 100.0},
                     {"days": 14, "spend": 90.0, "revenue": 100.0}, requested_days=14)
    assert impact.net == -40.00


def test_payroll_counts_only_settled_impact():
    actions = [
        {"status": "measured", "impact": {"net": 106.50, "provisional": False}},
        {"status": "measured", "impact": {"net": 40.00, "provisional": False}},
        {"status": "measured", "impact": {"net": 500.00, "provisional": True}},   # excluded
        {"status": "applied"},                                                    # awaiting
        {"status": "applied"},
        {"status": "dismissed"},
    ]
    payroll = build_payroll(actions, operator_cost=49.00, currency="EUR")
    assert payroll.settled_impact == 146.50
    assert payroll.provisional_impact == 500.00      # visible, not counted
    assert payroll.net == 97.50
    assert payroll.paid_for_itself is True
    assert payroll.awaiting_measurement == 2
    assert payroll.counts == {"measured": 3, "applied": 2, "dismissed": 1}
    assert payroll.currency == "EUR"


def test_payroll_does_not_claim_to_have_paid_for_itself_on_provisional_evidence():
    actions = [{"status": "measured", "impact": {"net": 900.00, "provisional": True}}]
    payroll = build_payroll(actions, operator_cost=49.00)
    assert payroll.settled_impact == 0.0
    assert payroll.paid_for_itself is False
    assert payroll.net == -49.00


def test_payroll_with_no_actions_is_honest_about_it():
    payroll = build_payroll([], operator_cost=49.00)
    assert payroll.settled_impact == 0.0
    assert payroll.paid_for_itself is False
    assert payroll.net == -49.00
    assert payroll.counts == {}


def _run_standalone():
    impact = measure(BASELINE, OUTCOME, requested_days=14)
    print(f"negate keyword: avoided ${impact.cost_avoided:7.2f}  "
          f"revenue {impact.revenue_gained:+7.2f}  -> net ${impact.net:7.2f}")
    payroll = build_payroll(
        [{"status": ActionStatus.MEASURED.value, "impact": impact.to_dict()}],
        operator_cost=49.00, currency="EUR")
    print(f"payroll: settled EUR{payroll.settled_impact:.2f} vs cost "
          f"EUR{payroll.operator_cost:.2f} -> net EUR{payroll.net:.2f} "
          f"({'paid for itself' if payroll.paid_for_itself else 'did NOT pay for itself'})")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    _run_standalone()


# --- Proposals from PPC findings ---------------------------------------------

from app.actions_engine import proposals_from_ppc  # noqa: E402

ROWS = [
    {"search_term": "cheap dog bowl", "campaign_id": "111", "ad_group_id": "222"},
    {"search_term": "stainless dog bowl", "campaign_id": "111", "ad_group_id": "222"},
    {"search_term": "orphan term", "campaign_id": "", "ad_group_id": "222"},
    {"search_term": "tiny spender", "campaign_id": "111", "ad_group_id": "222"},
]
FINDINGS = [
    {"keyword": "cheap dog bowl", "action": "NEGATE", "spend": 120.50, "sales": 0.0,
     "savings": 120.50, "reason": "96 clicks, no orders"},
    {"keyword": "stainless dog bowl", "action": "KEEP", "spend": 48.0, "sales": 216.0},
    {"keyword": "orphan term", "action": "NEGATE", "spend": 30.0, "savings": 30.0},
    {"keyword": "tiny spender", "action": "NEGATE", "spend": 0.40, "savings": 0.40},
    {"keyword": "not in the report", "action": "NEGATE", "spend": 90.0, "savings": 90.0},
]


def test_only_negatable_findings_with_ids_and_real_spend_become_proposals():
    proposals = proposals_from_ppc(FINDINGS, ROWS, window_days=14)
    assert [p.target for p in proposals] == ["cheap dog bowl"]

    only = proposals[0]
    assert only.action_type == "NEGATE_KEYWORD"
    assert only.projected_impact == 120.50
    assert only.baseline == {"days": 14, "spend": 120.50, "revenue": 0.0}
    assert only.revert_to["remove_negative_keyword"] == "cheap dog bowl"
    assert only.revert_to["campaign_id"] == "111"
    assert only.note == "96 clicks, no orders"


def test_a_finding_with_no_matching_row_is_skipped_not_guessed():
    """Without campaign and ad group ids there is nothing to attach a negative to."""
    proposals = proposals_from_ppc(
        [{"keyword": "not in the report", "action": "NEGATE", "spend": 90.0}], ROWS,
        window_days=14)
    assert proposals == []


def test_proposals_are_ordered_by_the_money_they_save():
    rows = [{"search_term": t, "campaign_id": "1", "ad_group_id": "2"}
            for t in ("small", "huge", "middling")]
    findings = [{"keyword": "small", "action": "NEGATE", "spend": 5.0, "savings": 5.0},
                {"keyword": "huge", "action": "NEGATE", "spend": 900.0, "savings": 900.0},
                {"keyword": "middling", "action": "NEGATE", "spend": 50.0, "savings": 50.0}]
    assert [p.target for p in proposals_from_ppc(findings, rows, window_days=14)] == \
        ["huge", "middling", "small"]


def test_matching_ignores_case_and_padding():
    proposals = proposals_from_ppc(
        [{"keyword": "  Cheap Dog Bowl ", "action": "NEGATE", "spend": 10.0, "savings": 10.0}],
        ROWS, window_days=7)
    assert len(proposals) == 1
    assert proposals[0].baseline["days"] == 7
