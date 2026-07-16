import unittest

from subtitler.backends.existing_pipeline import transcription_workers


class TranscriptionWorkerTests(unittest.TestCase):
    def test_hosted_default_is_six_workers(self) -> None:
        config = {"backend": {"transcriber": "gemini", "transcription_workers": None}}
        self.assertEqual(transcription_workers(config), 6)

    def test_explicit_value_is_respected_for_hosted_and_local_backends(self) -> None:
        for transcriber in ("openai", "local-gemma"):
            with self.subTest(transcriber=transcriber):
                config = {"backend": {"transcriber": transcriber, "transcription_workers": 2}}
                self.assertEqual(transcription_workers(config), 2)


if __name__ == "__main__":
    unittest.main()
