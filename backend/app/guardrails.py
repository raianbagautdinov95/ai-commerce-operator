"""
Guardrails — the collar that makes delegation safe.

Nothing in this codebase may change a seller's live account until it has passed
through here. The rules are deliberately boring, deterministic and few: a limit a
seller cannot predict is a limit they cannot trust, and an Operator whose blast
radius is unbounded is one no sane owner hands the keys to.

Three verdicts, in strict precedence:

    BLOCKED   -- refused outright; the Operator may not do this
    APPROVAL  -- allowed only with a human saying yes to this specific action
    ALLOWED   -- proceed

The kill switch is checked first and nothing can override it. That is the whole
point of a kill switch: when an owner turns the Operator off, arguments about
thresholds stop mattering.

Every decision carries a `reason` written for the person who will read it, not
for a log parser.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any

# Applied when a store has never configured anything.
#
# A new store starts in OBSERVE -> SUGGEST -> APPROVE: the Operator reads,
# judges and proposes, and a person applies. `auto_apply_below: 0` means no
# amount of money may move unattended, so every proposal waits for a human.
#
# This is the default because trust is earned per store, not assumed at install.
# A seller who has watched the proposals for a fortnight and agrees with them can
# raise the ceiling; nobody should have to discover the limits by having an
# unattended change surprise them on day one. Raising it is one PUT away.
DEFAULT_POLICY = {
    "enabled": True,
    "max_actions_per_day": 20,
    "max_change_pct": 0.20,
    "auto_apply_below": 0.0,      # 0 = nothing unattended; the seller opts in
    "protected_spend_per_day": 50.0,  # touching a bigger spender always needs a human
}

# What a seller who has decided to let the Operator act alone typically moves to.
# Offered by the UI as "let it handle the small stuff", never applied silently.
SUGGESTED_UNATTENDED_POLICY = {**DEFAULT_POLICY, "auto_apply_below": 25.0}


class Verdict(str, Enum):
    ALLOWED = "allowed"
    APPROVAL = "approval_required"
    BLOCKED = "blocked"


@dataclass
class Policy:
    enabled: bool = True
    max_actions_per_day: int = 20
    max_change_pct: float = 0.20
    auto_apply_below: float = 25.0
    protected_spend_per_day: float = 50.0

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Policy":
        data = {**DEFAULT_POLICY, **(raw or {})}
        return cls(
            enabled=bool(data["enabled"]),
            max_actions_per_day=int(data["max_actions_per_day"]),
            max_change_pct=float(data["max_change_pct"]),
            auto_apply_below=float(data["auto_apply_below"]),
            protected_spend_per_day=float(data["protected_spend_per_day"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Request:
    """What the Operator wants to do, in the terms the guardrails judge."""
    actor: str = "operator"                 # "operator" (unattended) or "human"
    money_at_stake: float = 0.0             # size of the move, absolute currency
    change_pct: float | None = None         # relative size, e.g. a bid cut of 0.35
    target_spend_per_day: float = 0.0       # how big the thing being touched is
    applied_today: int = 0                  # actions already applied this UTC day


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    limits_hit: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict.value, "reason": self.reason,
                "limits_hit": self.limits_hit, "allowed": self.allowed}


def evaluate(request: Request, policy: Policy) -> Decision:
    """Judge one proposed change. Deterministic; the LLM has no say here."""
    # 1. The kill switch. First, and unconditional.
    if not policy.enabled:
        return Decision(Verdict.BLOCKED,
                        "The Operator is switched off. Turn it back on to allow changes.",
                        ["enabled"])

    # A human acting for themselves is not the Operator acting unattended; the
    # daily budget and the auto-apply ceiling exist to bound UNATTENDED action.
    unattended = request.actor != "human"

    # 2. A change too large in relative terms is refused outright, for anyone.
    #    A 60% bid cut is not a small mistake to undo.
    if request.change_pct is not None and abs(request.change_pct) > policy.max_change_pct:
        return Decision(
            Verdict.BLOCKED,
            f"Change of {abs(request.change_pct):.0%} exceeds the {policy.max_change_pct:.0%} "
            f"limit for a single step. Make a smaller change, or raise the limit.",
            ["max_change_pct"],
        )

    if not unattended:
        return Decision(Verdict.ALLOWED, "Applied by you, so the unattended limits do not apply.")

    # 3. The daily budget of unattended actions.
    if request.applied_today >= policy.max_actions_per_day:
        return Decision(
            Verdict.BLOCKED,
            f"Already made {request.applied_today} changes today, the daily limit is "
            f"{policy.max_actions_per_day}. Remaining actions wait for tomorrow or for your approval.",
            ["max_actions_per_day"],
        )

    limits: list[str] = []
    # 4. Big spenders are never touched unattended.
    if request.target_spend_per_day > policy.protected_spend_per_day:
        limits.append("protected_spend_per_day")
    # 5. Neither is real money.
    if request.money_at_stake > policy.auto_apply_below:
        limits.append("auto_apply_below")

    if limits:
        why = []
        if "protected_spend_per_day" in limits:
            why.append(f"it spends {request.target_spend_per_day:.2f} a day")
        if "auto_apply_below" in limits:
            why.append(f"{request.money_at_stake:.2f} is at stake")
        return Decision(Verdict.APPROVAL,
                        "Needs your approval because " + " and ".join(why) + ".", limits)

    return Decision(Verdict.ALLOWED, "Within every limit you set.")


def remaining_today(policy: Policy, applied_today: int) -> int:
    return max(policy.max_actions_per_day - applied_today, 0)
