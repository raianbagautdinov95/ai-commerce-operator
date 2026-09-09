"""The once-a-day run: warn, read, price.

    python -m app.daily
    python -m app.daily --dry-run          # counts; claims nothing, sends nothing
    python -m app.daily --only measurement # one stage, for a targeted rerun

Three stages in one cron entry, and three separate jobs underneath it. That
distinction is the whole design:

* One process, because a shop has to be read before the results that depend on
  those days can be priced, and running them in the wrong order every night
  would delay every measurement by a day for no reason.
* Three claims, because they fail for unrelated reasons. Resend being down must
  not stop a sync; a revoked Shopify token must not stop somebody's trial
  warning; and a measurement that throws must leave the other two recorded as
  the successes they were.
* Three freshness records, because "the daily job ran" is not an answer when
  only two thirds of it did. `scheduler_runs` holds a row per job per day, so a
  stage that has quietly stopped is visible on its own.

Each stage claims its day before doing anything. A second firing — a retry, a
second region, somebody running it by hand — loses the insert and stops. The
work each stage does is idempotent underneath that as well: neither layer is
trusted alone.

Nothing here decides anything about money or entitlements. It warns, it queues
reads, and it hands closed windows to the measurement rules. A scheduler outage
must never expire a trial, and a scheduler bug must never invent a result.
"""
from __future__ import annotations

import sys

from . import observability, scheduler
from .db.session import SessionLocal
from .runtime import log


def _trial_warnings(*, dry_run: bool) -> dict:
    from . import tasks

    result = tasks.warn_about_ending_trials(dry_run=dry_run)
    return {"found": result["considered"], "queued": result["queued"],
            "skipped": result["already_warned"],
            "line": (f"trials: considered {result['considered']}, "
                     f"queued {result['queued']}, "
                     f"already warned {result['already_warned']}")}


def _shopify_sync(*, dry_run: bool) -> dict:
    from . import tasks

    result = tasks.sync_connected_shopify_stores(dry_run=dry_run)
    return {"found": result["considered"], "queued": result["queued"],
            "skipped": result["skipped"],
            "line": (f"sync: {result['considered']} connected shop(s), "
                     f"queued {result['queued']}, skipped {result['skipped']}, "
                     f"failed {result['failed']}"
                     + (f" ({result['reasons']})" if result["reasons"] else ""))}


def _measurement(*, dry_run: bool) -> dict:
    from . import tasks

    result = tasks.measure_due_actions(dry_run=dry_run)
    return {"found": result["considered"], "queued": result["measured"],
            "skipped": result["waiting"],
            "line": (f"measurement: {result['considered']} window(s) closed, "
                     f"priced {result['measured']}, waiting {result['waiting']}, "
                     f"failed {result['failed']}"
                     + (f" ({result['reasons']})" if result["reasons"] else ""))}


#: Order matters once: a shop is read before the windows that depend on those
#: days are priced, so a result is never delayed a day for want of a sync that
#: ran minutes later.
STAGES = (
    (scheduler.TRIAL_WARNINGS, _trial_warnings),
    (scheduler.SHOPIFY_SYNC, _shopify_sync),
    (scheduler.MEASUREMENT, _measurement),
)


def run_stage(job: str, work, *, run_key: str, dry_run: bool) -> bool:
    """Claim the day for one job, do it, record what happened. True if it ran."""
    if dry_run:
        # Claims nothing: a dry run must leave the real one free to take the day.
        try:
            print(work(dry_run=True)["line"] + "  [dry run]")
            return True
        except Exception:  # noqa: BLE001 - a stage failure is not a run failure
            log.exception("Dry run of %s failed", job)
            print(f"{job}: failed")
            return False

    db = SessionLocal()
    try:
        run = scheduler.claim(db, job=job, run_key=run_key)
        if run is None:
            print(f"{job}: already run for {run_key}.")
            return True
        db.commit()
    except Exception:  # noqa: BLE001
        db.close()
        log.exception("Could not claim %s", job)
        print(f"{job}: could not claim the day")
        return False

    try:
        result = work(dry_run=False)
    except Exception as exc:  # noqa: BLE001 - the run must be recorded either way
        scheduler.finish(db, run, error_code=type(exc).__name__[:64])
        log.exception("Stage %s failed", job)
        print(f"{job}: failed ({type(exc).__name__})")
        return False
    else:
        scheduler.finish(db, run, found=result["found"], queued=result["queued"],
                         skipped=result["skipped"])
        print(result["line"])
        return True
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv
    only = None
    if "--only" in argv:
        index = argv.index("--only")
        if index + 1 < len(argv):
            only = argv[index + 1]
        if only not in scheduler.JOBS:
            print(f"--only takes one of: {', '.join(scheduler.JOBS)}")
            return 2

    # A cron service is its own process: nothing else has initialised this for
    # it, and a job that fails silently once a day is the worst kind.
    observability.initialize()

    run_key = scheduler.run_key_for()
    failures = 0
    for job, work in STAGES:
        if only is not None and job != only:
            continue
        # Deliberately not short-circuiting. A stage that fails says so and the
        # next one still runs: they share a process, not a fate.
        if not run_stage(job, work, run_key=run_key, dry_run=dry_run):
            failures += 1

    if failures:
        print(f"{failures} stage(s) failed. The rest are recorded as done.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
