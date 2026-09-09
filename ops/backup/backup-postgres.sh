#!/bin/sh
set -eu
umask 077

backup_dir="${BACKUP_DIR:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
case "$backup_dir" in ""|"/") echo "Unsafe BACKUP_DIR" >&2; exit 2;; esac
mkdir -p "$backup_dir"
[ ! -L "$backup_dir" ] || { echo "BACKUP_DIR must not be a symlink" >&2; exit 2; }
case "$retention_days" in *[!0-9]*|"") echo "Invalid retention" >&2; exit 2;; esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/aco-$stamp.dump"
pg_dump --format=custom --compress=9 --file="$target" "${PGDATABASE:-aco}"
pg_restore --list "$target" >/dev/null
(cd "$backup_dir" && sha256sum "$(basename "$target")" > "$(basename "$target").sha256")
find "$backup_dir" -maxdepth 1 -type f \( -name 'aco-*.dump' -o -name 'aco-*.dump.sha256' \) \
  -mtime "+$retention_days" -delete
echo "$target"
