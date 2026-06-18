import sys
import unittest
from unittest.mock import patch

from aviutl_subtitle import parse_args


class SidecarCliTests(unittest.TestCase):
    def test_sidecars_are_enabled_by_default(self):
        with patch.object(sys, "argv", ["aviutl_subtitle.py", "input.mkv", "--workflow", "local"]):
            self.assertFalse(parse_args().no_sidecars)

    def test_no_sidecars_flag_is_accepted(self):
        with patch.object(sys, "argv", ["aviutl_subtitle.py", "input.mkv", "--workflow", "local", "--no-sidecars"]):
            self.assertTrue(parse_args().no_sidecars)


if __name__ == "__main__":
    unittest.main()
