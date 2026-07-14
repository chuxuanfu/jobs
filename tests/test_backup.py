from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from job_monitor.backup import backup_project


class BackupTests(unittest.TestCase):
    def test_backup_uses_consistent_sqlite_copy(self):
        with tempfile.TemporaryDirectory() as root_directory, tempfile.TemporaryDirectory() as backup_directory:
            root = Path(root_directory)
            database = root / "data" / "databases" / "sample.sqlite"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE values_table(value TEXT)")
                connection.execute("INSERT INTO values_table VALUES ('ok')")
            (root / "results").mkdir()
            (root / "results" / "sample.json").write_text("{}", encoding="utf-8")
            result = backup_project(root, backup_directory)
            copied_database = Path(result["destination"]) / "data" / "databases" / "sample.sqlite"
            with sqlite3.connect(copied_database) as connection:
                self.assertEqual("ok", connection.execute("SELECT value FROM values_table").fetchone()[0])
            self.assertTrue((Path(result["destination"]) / "results" / "sample.json").exists())


if __name__ == "__main__":
    unittest.main()
