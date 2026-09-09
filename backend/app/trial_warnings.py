"""The daily job: warn anybody whose trial is about to end.

Run by a Railway cron service, once a day:

    python -m app.trial_warnings
    python -m app.trial_warnings --dry-run     # counts, sends nothing, claims nothing

It warns and does nothing else. It does not expire a trial, change an
entitlement or touch a subscription — a scheduler outage would otherwise
silently extend everybody's trial, and a scheduler bug would end them early.
Whether somebody may use the product is decided by `entitlement`, from the
subscription row, every time it is asked.

The emails are queued rather than sent here: this process should finish in
seconds and the worker owns delivery. A deployment with no mail provider still
runs it — the receipts are reserved, the jobs are queued, and nothing about
Stripe or billing breaks because Resend is missing.
"""
from __future__ import annotations

import sys

from . import observability, scheduler
from .db.session import SessionLocal
from .runtime import log


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv

    # A cron service is its own process: nothing else has initialised this for
    # it, and a job that fails silently once a day is the worst kind.
    observability.initialize()

    from . import tasks

    db = SessionLocal()
    try:
        if dry_run:
            result = tasks.warn_about_ending_trials(dry_run=True)
            print(f"Would warn {result['queued']} trial(s); "
                  f"{result['already_warned']} already warned.")
            return 0

        run_key = scheduler.run_key_for()
        run = scheduler.claim(db, job=scheduler.TRIAL_WARNINGS, run_key=run_key)
        if run is None:
            # Not an error. Another instance has today, which is exactly what
            # the claim is for.
            print(f"Already run for {run_key}. Nothing to do.")
            return 0
        db.commit()

        try:
            result = tasks.warn_about_ending_trials()
        except Exception as exc:  # noqa: BLE001 - the run must be recorded either way
            scheduler.finish(db, run, error_code=type(exc).__name__[:64])
            log.exception("Trial warnings failed")
            return 1

        scheduler.finish(db, run, found=result["considered"], queued=result["queued"],
                         skipped=result["already_warned"])
        print(f"Considered {result['considered']}, queued {result['queued']}, "
              f"already warned {result['already_warned']}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
