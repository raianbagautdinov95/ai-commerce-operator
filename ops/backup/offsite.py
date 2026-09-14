"""One-shot PostgreSQL backup to a private HTTPS S3-compatible bucket.

Success is recorded only after reading the uploaded archive back in full.
No bucket policies or existing objects are changed by this program.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4


def settings(env):
    required = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
                "BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    missing = [name for name in required if not env.get(name, "").strip()]
    if missing:
        raise ValueError("Missing settings: " + ", ".join(missing))
    endpoint = urlsplit(env["BACKUP_S3_ENDPOINT"])
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError("Storage endpoint must be HTTPS without embedded credentials")
    prefix = env.get("BACKUP_S3_PREFIX", "aco/production").strip("/")
    if not prefix or any(p in ("", ".", "..") for p in prefix.split("/")):
        raise ValueError("Invalid backup prefix")
    return env["BACKUP_S3_BUCKET"], prefix


def digest_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def publish(client, bucket, prefix, archive):
    with archive.open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Invalid PostgreSQL archive")
    digest = digest_file(archive)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{prefix}/{stamp}-{uuid4().hex}.dump"
    client.upload_file(str(archive), bucket, key,
                       ExtraArgs={"Metadata": {"sha256": digest}})
    remote = client.get_object(Bucket=bucket, Key=key)["Body"]
    actual = hashlib.sha256()
    size = 0
    try:
        while chunk := remote.read(1024 * 1024):
            actual.update(chunk)
            size += len(chunk)
    finally:
        remote.close()
    if actual.hexdigest() != digest or size != archive.stat().st_size:
        raise ValueError("Uploaded archive failed read-back verification")
    client.put_object(Bucket=bucket, Key=key + ".sha256",
                      Body=f"{digest}  {key.rsplit('/', 1)[1]}\n".encode("ascii"),
                      ContentType="text/plain")
    report = {"archive": key, "sha256": digest, "bytes": size,
              "verified_at": datetime.now(timezone.utc).isoformat(),
              "read_back_verified": True, "restore_verified": False}
    client.put_object(Bucket=bucket, Key=key + ".verified.json",
                      Body=json.dumps(report).encode(), ContentType="application/json")
    return report


def main():
    os.umask(0o077)
    bucket, prefix = settings(os.environ)
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
                          region_name=os.environ.get("AWS_DEFAULT_REGION", "auto"),
                          config=Config(connect_timeout=15, read_timeout=60,
                                        retries={"max_attempts": 3, "mode": "standard"}))
    with tempfile.TemporaryDirectory(prefix="aco-offsite-") as directory:
        archive = Path(directory) / "backup.dump"
        # libpq reads PG* from the process environment; secrets never enter argv.
        subprocess.run(["pg_dump", "--format=custom", "--compress=9",
                        "--lock-wait-timeout=30s", "--file", str(archive)],
                       check=True, timeout=1800, capture_output=True)
        subprocess.run(["pg_restore", "--list", str(archive)],
                       check=True, timeout=120, capture_output=True)
        print(json.dumps(publish(client, bucket, prefix, archive)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider errors and subprocess stderr can contain credentials or URLs.
        print("OFFSITE BACKUP FAILED: " + type(exc).__name__, flush=True)
        raise SystemExit(1)
