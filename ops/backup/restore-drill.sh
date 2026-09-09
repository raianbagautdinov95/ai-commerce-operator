#!/bin/sh
# Prove a backup can be restored AND that it contains something.
#
# A drill that only checks the dump loads passes just as happily on a backup of
# an empty schema — which is the failure people discover on the day they need it.
# So this also takes a census of the core tables and refuses a restore that came
# back structurally incomplete.
#
#   restore-drill.sh /backups/aco-20260829T080514Z.dump
#
# Optional: EXPECT_MIN_ROWS=1 fails the drill if the census is all zeroes, for
# a deployment that is known to hold data. Leave it unset for a fresh one.
set -eu

[ "$#" -eq 1 ] || { echo "Usage: restore-drill.sh /backups/aco-*.dump" >&2; exit 2; }
backup="$1"
[ -f "$backup" ] && [ ! -L "$backup" ] || { echo "Backup file is invalid" >&2; exit 2; }
checksum="$backup.sha256"
[ -f "$checksum" ] || { echo "Checksum file is missing" >&2; exit 2; }
(cd "$(dirname "$backup")" && sha256sum -c "$(basename "$checksum")")
pg_restore --list "$backup" >/dev/null

# Tables the product cannot function without. A dump missing any of them is not
# a backup of this application, whatever it restored successfully.
required_tables="users stores operator_actions guardrail_policies
integration_credentials audit_events idempotency_records channel_connections"

drill_db="aco_restore_verify_$(date -u +%Y%m%d%H%M%S)_$$"
cleanup() { dropdb --if-exists "$drill_db" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
createdb "$drill_db"
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$drill_db" "$backup"

query() { psql --dbname="$drill_db" --no-psqlrc --tuples-only --no-align --command "$1"; }

version="$(query 'SELECT version_num FROM alembic_version LIMIT 1;')"
[ -n "$version" ] || { echo "Restored database has no schema version" >&2; exit 1; }
echo "Schema version: $version"

echo "Census:"
total=0
missing=""
for table in $required_tables; do
  present="$(query "SELECT to_regclass('public.$table') IS NOT NULL;")"
  if [ "$present" != "t" ]; then
    missing="$missing $table"
    printf '  %-24s MISSING\n' "$table"
    continue
  fi
  count="$(query "SELECT count(*) FROM $table;")"
  printf '  %-24s %s\n' "$table" "$count"
  total=$((total + count))
done

if [ -n "$missing" ]; then
  echo "Restore is not a backup of this application; missing:$missing" >&2
  exit 1
fi

if [ "${EXPECT_MIN_ROWS:-0}" -gt 0 ] && [ "$total" -lt "${EXPECT_MIN_ROWS}" ]; then
  echo "Restored $total rows across core tables, expected at least ${EXPECT_MIN_ROWS}." >&2
  echo "The dump loads but is effectively empty." >&2
  exit 1
fi

echo "Restore drill passed: $version, $total rows across core tables."
