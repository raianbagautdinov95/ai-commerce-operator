# PostgreSQL backup runbook

## Daily offsite service (prepared, not yet deployed)

Build `ops/backup/Dockerfile` with build context/root directory `ops/backup`.
The container runs once and exits. Create a separate Railway service named
`backup`, set cron to `0 3 * * *` (03:00 UTC daily), restart policy Never, and
no HTTP healthcheck or public domain. Configure its PG* variables from the
production PostgreSQL service and the BACKUP_S3_* / AWS_* variables from
`.env.example`. Keep these credentials out of the API and frontend.

`offsite.py` validates the archive with pg_restore, uploads under a unique key,
downloads it fully to compare size and SHA-256, then publishes checksum and
`.verified.json` records. A failed upload or mismatch exits nonzero without a
success record. The job does not claim that read-back verification proves a
restore; keep the weekly isolated restore drill.

Provision a private bucket outside Railway before deploying. For Cloudflare R2,
use Standard storage, disable public access, configure a 14-day bucket lock and
a 30-day expiration rule for `aco/production/`, and use an object-access token
scoped to this bucket (without bucket-administration rights). R2 encrypts stored
objects; use its HTTPS S3 endpoint. Administrative access can change lock rules,
so protect the separate Cloudflare account as well. The job never deletes or
changes retention rules itself.

Before enabling the schedule, run once and restore the downloaded object in
an isolated PostgreSQL 18 instance. Configure failure alerts and an independent
freshness check for the absence of a verified object newer than 26 hours;
neither monitoring nor the external bucket is provisioned by this code.

Tests: `python -m unittest discover -s ops/backup -p test_offsite.py -v`.

References:
- https://docs.railway.com/cron-jobs
- https://developers.cloudflare.com/r2/buckets/bucket-locks/
- https://developers.cloudflare.com/r2/reference/data-security/

## Windows / Railway SSH export

`python ops/backup/railway-backup.py` streams a custom-format dump from the
production `postgres` service to a new file in the git-ignored `backups/`
directory. It uses the dedicated `%USERPROFILE%/.ssh/aco_railway_backup` key
registered with Railway; enter its passphrase at the SSH prompt. The script
checks the command exit status and dump signature, and writes a SHA-256 file.
It does not delete older copies. These checks do not replace `pg_restore --list`
or the isolated restore drill below. Failed partial files are retained and must
not be treated as backups.

On 2026-09-13, an SSH export of production PostgreSQL 18 completed:

- File: `aco-20260913T125151Z.dump`, 125274 bytes.
- SHA-256: `5b524ea214ff4a684fdc3d2a3164411b7b6821309c180d2d78d946fd014b2534`.
- Stored locally outside Railway; not committed to Git.
- Restore validated on 2026-09-13 with PostgreSQL 18 in a local container with
  network mode `none`, read-only backup mount and temporary in-memory database
  storage. SHA-256 and table-of-contents checks passed. `restore-drill.sh` passed
  with `EXPECT_MIN_ROWS=1`: schema `0024_product_costs`, all eight required tables,
  18 rows across those core tables. The temporary restored database was removed.
- No claim is made that this local copy has immutable retention or verified
  storage encryption. Those requirements still apply before disaster recovery
  can be considered complete.

Use a PostgreSQL 18 (or compatible newer) client for this dump. Restore only to
an isolated non-production database. Never pass production connection variables
to `restore-drill.sh`.

Run `backup-postgres.sh` from a trusted PostgreSQL client container or host with
`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, and `PGDATABASE` supplied by the
secret manager. The script creates a compressed custom-format dump, validates
its table of contents, writes a SHA-256 checksum, and applies retention only to
files matching `aco-*.dump` in the exact backup directory.

At least weekly, run `restore-drill.sh <dump>` against a non-production
PostgreSQL server. It verifies the checksum, restores into a uniquely named
temporary database, reads the Alembic revision, and removes only that temporary
database on exit. Alert if either script exits non-zero.

Backups must be encrypted by the storage platform, replicated to a separate
account or region, and protected with immutable retention. A dump stored only
on the production server is not a disaster-recovery backup.


## The minimum for a first customer

**Daily**, not weekly. A day is the most data anyone should lose; a week of a
customer's orders is not something to apologise for. Schedule
`backup-postgres.sh` once a day from something that is *not* the API or the
worker — a Railway cron service, a GitHub Action, or any host with the client
tools and the credentials.

**Retention 14 days** (`BACKUP_RETENTION_DAYS`, default 14). Long enough that
corruption noticed on a Monday can be escaped from; short enough that the
storage bill is not a reason to switch it off. Raise it before you need it, not
after.

**Success is a checked artefact, not an exit code.** The script writes a
SHA-256 next to each dump and validates the table of contents, so "the job ran"
and "there is a restorable dump" are different claims. Alert on a non-zero exit
*and* on the absence of a dump newer than 26 hours — a cron that silently stopped
firing produces no failures at all, which is what makes it dangerous.

**Weekly drill**, `restore-drill.sh`, against a non-production server, with
`EXPECT_MIN_ROWS=1`. It restores into a uniquely named throwaway database and
drops only that. It never touches the production database, and it must never be
pointed at one.

### Railway's own backups are not the copy that saves you

If the database is a Railway PostgreSQL plugin, Railway's snapshots live in the
same project, under the same account, behind the same credentials. They protect
you from a bad migration and a dropped table. They do not protect you from a
deleted project, a suspended account, a compromised login, or a billing failure
— and those are the scenarios where a backup is the only thing left.

At least one copy must leave the platform: another provider's object storage,
another account, ideally another region, with immutable retention so a
compromised credential cannot delete history. Until that exists, write down that
the deployment has no disaster recovery. It is a defensible position for a pilot;
believing otherwise is not.

## The drill, and what it is actually for

A backup you have never restored is a hope, not a backup. Worse, a drill that
only checks the dump *loads* passes just as happily on a backup of an empty
schema — which is precisely the failure people discover on the day they need it.

So `restore-drill.sh` restores into a throwaway database, then takes a census of
the core tables and refuses a restore that came back structurally incomplete.
Set `EXPECT_MIN_ROWS=1` on a deployment known to hold data, and it also refuses
one that loads but is hollow.

    docker compose exec db sh /tmp/backup-postgres.sh
    EXPECT_MIN_ROWS=1 docker compose exec db sh /tmp/restore-drill.sh /backups/aco-<stamp>.dump

### Verified, not assumed

Run against the live stack on 2026-08-29:

- The backup script produced a dump and its checksum; `sha256sum -c` verified it.
- Restoring it into a scratch database returned schema `0015_action_evidence`
  and 11 rows across the core tables, including two canary rows written
  immediately before the backup. The data made the round trip, not just the
  schema.
- Given a dump containing the schema and its version but no rows, the previous
  drill printed "Restore drill completed"; this one exits 1 with "The dump loads
  but is effectively empty."

### What this does not yet prove

The dump lives inside the database container. A backup on the same host as the
thing it protects survives a bad migration, not a lost machine. Shipping it
somewhere else is the next step, and until that exists this is a consistency
drill rather than a disaster-recovery one.
