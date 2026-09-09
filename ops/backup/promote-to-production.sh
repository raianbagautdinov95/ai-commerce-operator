#!/bin/sh
# Move a working database into a fresh production deployment, once.
#
# This exists for one crossing: the evidential Shopify cycle — a restock the
# store confirmed, the cost behind it and the days that have been read — lives
# in whichever database has been running while the product was being built, and
# production starts empty. A measurement window does not move with the code.
#
#   ops/backup/promote-to-production.sh /backups/aco-20260910T120000Z.dump
#
# Environment:
#   TARGET_DATABASE_URL   where to restore. The owner connection, not aco_app:
#                         a restore creates objects, and aco_app deliberately
#                         cannot. Required.
#   SOURCE_KEYRING        CREDENTIAL_ENCRYPTION_KEYS the dump was written under.
#   TARGET_KEYRING        CREDENTIAL_ENCRYPTION_KEYS the deployment will read
#                         with. Both required, and compared before anything is
#                         written.
#   CONFIRM               must be exactly "PROMOTE" — see below.
#
# --- why the keyring is checked first ----------------------------------------
#
# Shopify tokens are stored encrypted. Restore a database under a different
# keyring and every row arrives intact and unreadable: the sync stops at "no
# readable credential", the shop is shown as disconnected, and nothing anywhere
# says the cause was a transfer. It is the failure this script exists to refuse,
# so it is checked before the dump is even read.
#
# The keys themselves are never printed. Only their fingerprints are compared,
# and only the fingerprints are shown.
#
# --- why it refuses a database with data in it -------------------------------
#
# A restore into a populated database is a merge nobody designed. If production
# has already taken a customer, this is the wrong tool: export what is needed
# and move it deliberately.
set -eu

[ "$#" -eq 1 ] || { echo "Usage: promote-to-production.sh /backups/aco-*.dump" >&2; exit 2; }
backup="$1"
[ -f "$backup" ] && [ ! -L "$backup" ] || { echo "Backup file is invalid" >&2; exit 2; }

: "${TARGET_DATABASE_URL:?Set TARGET_DATABASE_URL to the target owner connection}"
: "${SOURCE_KEYRING:?Set SOURCE_KEYRING to the keyring the dump was written under}"
: "${TARGET_KEYRING:?Set TARGET_KEYRING to the keyring the deployment will read with}"

fingerprint() { printf '%s' "$1" | sha256sum | cut -c1-16; }
source_print="$(fingerprint "$SOURCE_KEYRING")"
target_print="$(fingerprint "$TARGET_KEYRING")"

echo "Keyring fingerprints"
echo "  source: $source_print"
echo "  target: $target_print"
if [ "$source_print" != "$target_print" ]; then
  cat >&2 <<'REFUSED'

REFUSED: the two deployments do not share a credential keyring.

Every Shopify token in this dump was encrypted with the source keyring. Restored
under a different one they arrive intact and unreadable, the next sync stops at
"no readable credential", and the shop is shown as disconnected with nothing
naming the cause.

Copy CREDENTIAL_ENCRYPTION_KEYS and CREDENTIAL_ACTIVE_KEY_VERSION from the
source deployment to the target byte for byte, then run this again.
REFUSED
  exit 1
fi

# --- the dump is what it claims to be ----------------------------------------
checksum="$backup.sha256"
[ -f "$checksum" ] || { echo "Checksum file is missing beside the dump" >&2; exit 2; }
(cd "$(dirname "$backup")" && sha256sum -c "$(basename "$checksum")")
pg_restore --list "$backup" >/dev/null

# --- the target is empty ------------------------------------------------------
existing="$(psql "$TARGET_DATABASE_URL" -tAc "
  select coalesce(sum(n), 0) from (
    select count(*) as n from information_schema.tables
     where table_schema = 'public' and table_name = 'stores'
  ) t" 2>/dev/null || echo 0)"
if [ "$existing" -gt 0 ]; then
  rows="$(psql "$TARGET_DATABASE_URL" -tAc 'select count(*) from stores' 2>/dev/null || echo 0)"
  if [ "$rows" -gt 0 ]; then
    echo "REFUSED: the target already holds $rows store(s)." >&2
    echo "A restore into a populated database is a merge nobody designed." >&2
    exit 1
  fi
fi

if [ "${CONFIRM:-}" != "PROMOTE" ]; then
  cat >&2 <<REFUSED

Ready. This will restore $backup into the target database.

Nothing has been written yet. To go ahead:

  CONFIRM=PROMOTE $0 $backup

REFUSED
  exit 3
fi

# --- the only step that writes anything --------------------------------------
pg_restore --exit-on-error --no-owner --no-privileges \
           --dbname="$TARGET_DATABASE_URL" "$backup"

# --- what arrived -------------------------------------------------------------
#
# Counts, never identities. This is a transfer log, not a customer record.
echo
echo "Restored:"
psql "$TARGET_DATABASE_URL" -tAc "
  select '  schema version   : ' || version_num from alembic_version;
  select '  stores           : ' || count(*) from stores;
  select '  connected shops  : ' || count(*) from channel_connections
         where provider = 'shopify' and status = 'connected';
  select '  credentials      : ' || count(*) from integration_credentials;
  select '  applied actions  : ' || count(*) from operator_actions where status = 'applied';
  select '  measured actions : ' || count(*) from operator_actions where measured_at is not null;
  select '  product costs    : ' || count(*) from product_costs;
  select '  variant days     : ' || count(*) from variant_daily_metrics;
"

# The application role is created by migration 0016 and owns nothing, so a
# restore does not grant it anything. Without this the deployment starts and
# every query is refused.
role="${APP_DB_ROLE:-aco_app}"
if psql "$TARGET_DATABASE_URL" -tAc \
     "select 1 from pg_roles where rolname = '$role'" | grep -q 1; then
  psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -q <<SQL
GRANT USAGE ON SCHEMA public TO $role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $role;
SQL
  echo "  grants renewed for $role"
else
  echo "  WARNING: role $role does not exist in the target." >&2
  echo "  Run the migrations there first: alembic upgrade head." >&2
fi

cat <<'NEXT'

Done. Two things are worth doing before trusting it:

  1. Prove a token still decrypts, rather than assuming the keyring matched:
       python -m app.preflight --live --no-env-file
     then open the Shopify screen. "No readable credential" means the keyring
     did not match after all, and nothing else will say so.

  2. Let the daily job read the shop once, so any open measurement window has
     the days it needs:
       python -m app.daily --only shopify_sync

NEXT
