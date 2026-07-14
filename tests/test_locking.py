from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_monitor.locking import exclusive_run_lock


class LockingTests(unittest.TestCase):
    def test_lock_file_is_created_and_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.lock"
            with exclusive_run_lock(path):
                self.assertTrue(path.exists())
            with exclusive_run_lock(path):
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
