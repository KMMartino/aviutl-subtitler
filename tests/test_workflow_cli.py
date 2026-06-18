import unittest

from aviutl_subtitle import _default_output_path
from pathlib import Path


class WorkflowCliTests(unittest.TestCase):
    def test_default_output_names_match_supported_workflows(self):
        input_path = Path("video.mkv")

        self.assertEqual(_default_output_path(input_path, "local"), Path("video.exo"))
        self.assertEqual(_default_output_path(input_path, "hosted"), Path("video-hosted-gemini35-gpt54mini.exo"))
        self.assertEqual(_default_output_path(input_path, "local-long-stream"), Path("video-long-stream-local.exo"))
        self.assertEqual(_default_output_path(input_path, "hosted-long-stream"), Path("video-long-stream-hosted.exo"))


if __name__ == "__main__":
    unittest.main()
