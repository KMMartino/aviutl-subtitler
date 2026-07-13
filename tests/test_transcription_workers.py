import unittest

from subtitler.backends.existing_pipeline import transcription_workers


class TranscriptionWorkerTests(unittest.TestCase):
    def test_hosted_default_is_six_workers(self) -> None:
        config = {"backend": {"transcriber": "gemini", "transcription_workers": None}}
        self.assertEqual(transcription_workers(config), 6)

    def test_hosted_explicit_value_below_six_is_respected(self) -> None:
        config = {"backend": {"transcriber": "openai", "transcription_workers": 2}}
        self.assertEqual(transcription_workers(config), 2)

    def test_local_explicit_value_is_not_raised(self) -> None:
        config = {"backend": {"transcriber": "local-gemma", "transcription_workers": 2}}
        self.assertEqual(transcription_workers(config), 2)


if __name__ == "__main__":
    unittest.main()
