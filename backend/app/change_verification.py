"""
Read-after-write: proof that an external system actually changed.

The payroll claims money was saved. That claim rests on the change having
happened — not on our having sent a request and received a 200. A request can
succeed while the account is untouched: the API accepts and silently drops it,
a partial batch reports success at the envelope level, or the thing we asked
for was already there and someone else's work gets counted as ours.

So every real mutation is bracketed:

    before = read()          # precondition: what is true now
    mutate()                 # the change itself
    after  = read()          # postcondition: what is true now

and this module decides, deterministically, what that pair of readings proves.
Nothing here talks to a network; it is given two observations and a rule.

The important distinction it enforces:

    verified  -- we watched the state change from absent to present.
                 Only this may be counted as money.
    unverified-- the request may well have worked, but we cannot show it.
                 Recorded, visible, and excluded from every total.

An action that was ALREADY in place before we acted is never counted. Claiming
savings for work that was already done is the most flattering mistake available
here, so it is named and refused rather than left to arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class ChangeOutcome:
    """What one bracketed mutation actually proved."""
    changed: bool                  # the external state moved because of us
    verified: bool                 # and we read it back and saw it
    reason: str
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    external_id: str | None = None

    @property
    def evidence_mode(self) -> str:
        """What the ledger may claim. Only a witnessed change counts as real."""
        return "real" if self.verified and self.changed else "unverified"

    @property
    def countable(self) -> bool:
        return self.evidence_mode == "real"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_mode"] = self.evidence_mode
        return data


def _normalise(values: list[str] | None) -> set[str]:
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def verify_presence(*, expected: str, before: list[str] | None,
                    after: list[str] | None, external_id: str | None = None,
                    read_back_failed: bool = False) -> ChangeOutcome:
    """Judge a mutation that was supposed to ADD `expected` to a set.

    `read_back_failed` says the postcondition read itself did not happen — a
    timeout, a rate limit. That is not evidence of failure and not evidence of
    success, so it is recorded as unverified rather than guessed either way.
    """
    target = str(expected).strip().lower()
    before_set, after_set = _normalise(before), _normalise(after)

    if not target:
        return ChangeOutcome(False, False, "Nothing was named to change.",
                             sorted(before_set), sorted(after_set), external_id)

    if target in before_set:
        return ChangeOutcome(
            False, False,
            f"'{expected}' was already in place before we acted, so nothing here is ours.",
            sorted(before_set), sorted(after_set), external_id)

    if read_back_failed:
        return ChangeOutcome(
            False, False,
            f"The change was sent but could not be read back, so '{expected}' is unproven.",
            sorted(before_set), sorted(after_set), external_id)

    if target in after_set:
        return ChangeOutcome(
            True, True, f"'{expected}' was absent before and present after.",
            sorted(before_set), sorted(after_set), external_id)

    return ChangeOutcome(
        False, False,
        f"The request was accepted but '{expected}' is still absent, so it did not take effect.",
        sorted(before_set), sorted(after_set), external_id)
