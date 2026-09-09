# PostgreSQL backup runbook

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
