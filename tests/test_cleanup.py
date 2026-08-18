import tempfile
import unittest
from pathlib import Path

from cleanup_json import cleanup_json_files, parse_timestamp_from_name


class CleanupTests(unittest.TestCase):
    def test_filename_timestamp_is_interpreted_as_kst(self):
        parsed = parse_timestamp_from_name("2026-08-18-0900.json")
        self.assertEqual(parsed.isoformat(), "2026-08-18T00:00:00+00:00")

    def test_deletes_old_snapshot_but_preserves_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            old = data_dir / "2000-01-01-0900.json"
            latest = data_dir / "latest.json"
            old.write_text("{}", encoding="utf-8")
            latest.write_text("{}", encoding="utf-8")

            deleted, skipped, failed = cleanup_json_files(data_dir, retention_days=30)

            self.assertEqual((deleted, skipped, failed), (1, 1, 0))
            self.assertFalse(old.exists())
            self.assertTrue(latest.exists())


if __name__ == "__main__":
    unittest.main()
