import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from offsite import publish, settings


class OffsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "test.dump"
        self.archive.write_bytes(b"PGDMP-test-archive")
        self.client = Mock()

    def test_success_requires_matching_download(self):
        self.client.get_object.return_value = {"Body": io.BytesIO(self.archive.read_bytes())}
        result = publish(self.client, "bucket", "production", self.archive)
        self.assertTrue(result["read_back_verified"])
        self.assertFalse(result["restore_verified"])
        self.assertEqual(self.client.put_object.call_count, 2)

    def test_corrupt_download_never_publishes_success(self):
        self.client.get_object.return_value = {"Body": io.BytesIO(b"corrupt")}
        with self.assertRaises(ValueError):
            publish(self.client, "bucket", "production", self.archive)
        self.client.put_object.assert_not_called()

    def test_upload_failure_never_publishes_success(self):
        self.client.upload_file.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            publish(self.client, "bucket", "production", self.archive)
        self.client.get_object.assert_not_called()
        self.client.put_object.assert_not_called()

    def test_invalid_dump_never_uploads(self):
        self.archive.write_bytes(b"error message")
        with self.assertRaises(ValueError):
            publish(self.client, "bucket", "production", self.archive)
        self.client.upload_file.assert_not_called()

    def test_keys_never_overwrite_previous_run(self):
        self.client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(self.archive.read_bytes())}
        first = publish(self.client, "bucket", "production", self.archive)
        second = publish(self.client, "bucket", "production", self.archive)
        self.assertNotEqual(first["archive"], second["archive"])

    def test_requires_https_and_explicit_credentials(self):
        with self.assertRaises(ValueError):
            settings({})
        env = dict.fromkeys(("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
                             "BACKUP_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"), "set")
        for endpoint in ("http://storage.example", "https://user:password@storage.example"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                settings(dict(env, BACKUP_S3_ENDPOINT=endpoint))
        self.assertEqual(settings(dict(env, BACKUP_S3_ENDPOINT="https://storage.example")),
                         ("set", "aco/production"))


if __name__ == "__main__":
    unittest.main()
