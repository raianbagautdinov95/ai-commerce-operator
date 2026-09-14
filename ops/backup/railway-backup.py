"""Stream a Railway PostgreSQL dump to a new local file, without logging data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone


def main():
    root = Path(__file__).resolve().parents[2]
    directory = root / "backups"
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or directory.is_junction() or directory.resolve() != directory:
        raise SystemExit("Backup directory must not be a link")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"aco-{stamp}.dump"
    key = Path(os.environ["USERPROFILE"]) / ".ssh" / "aco_railway_backup"
    args = ["npx.cmd", "@railway/cli", "ssh", "--project",
            "71f55321-40ea-4db4-af43-d893b465f4bc", "--service", "postgres",
            "--environment", "production", "--identity-file", str(key),
            "--", "pg_dump", "--format=custom", "--compress=9"]
    with target.open("xb") as output:
        result = subprocess.run(args, stdout=output, timeout=600)
    with target.open("rb") as source:
        magic = source.read(5)
    if result.returncode or magic != b"PGDMP":
        raise SystemExit("Backup failed or format invalid; partial artifact retained: " + str(target))
    with target.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with Path(str(target) + ".sha256").open("x", encoding="ascii", newline="\n") as checksum:
        checksum.write(f"{digest}  {target.name}\n")
    print(json.dumps({"path": str(target), "bytes": target.stat().st_size,
                      "sha256": digest, "restore_verified": False}))


if __name__ == "__main__":
    main()
