import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opentspkg import files


class ReplaceTests(unittest.TestCase):
    def test_a_transient_windows_sharing_violation_is_retried(self):
        locked = PermissionError("scanner holds the file")
        locked.winerror = 32
        with patch.object(Path, "replace", side_effect=[locked, None]) as replace, patch.object(files.time, "sleep"):
            files.replace(Path("staged"), Path("output"))
        self.assertEqual(replace.call_count, 2)

    def test_non_transient_errors_are_reported_immediately(self):
        operation = Mock(side_effect=FileNotFoundError("missing source"))
        with self.assertRaises(FileNotFoundError), patch.object(files.time, "sleep") as sleep:
            files._retry_windows(operation)
        operation.assert_called_once()
        sleep.assert_not_called()
