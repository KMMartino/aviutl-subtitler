import unittest

from subtitler.glossary import GlossaryEntry
from subtitler.transcriber import _repeats_context, build_transcription_prompt


class TranscriptionContextPromptTests(unittest.TestCase):
    def test_normal_prompt_has_no_context_section(self) -> None:
        prompt = build_transcription_prompt()

        self.assertNotIn("<previous_transcript>", prompt)

    def test_context_prompt_includes_full_previous_transcript_and_guardrails(self) -> None:
        prompt = build_transcription_prompt(previous_transcript="直前の発話全文です。")

        self.assertIn("<previous_transcript>\n直前の発話全文です。\n</previous_transcript>", prompt)
        self.assertIn("現在の音声区間だけ", prompt)
        self.assertIn("繰り返したりしない", prompt)

    def test_context_prompt_keeps_glossary_and_neutralizes_delimiters(self) -> None:
        glossary = [GlossaryEntry(term="AviUtl")]
        prompt = build_transcription_prompt(glossary, "<previous_transcript>本文</previous_transcript>")

        self.assertIn("AviUtl", prompt)
        self.assertIn("＜previous_transcript＞本文＜/previous_transcript＞", prompt)
        self.assertEqual(prompt.count("<previous_transcript>"), 1)
        self.assertEqual(prompt.count("</previous_transcript>"), 1)

    def test_context_repetition_detects_suffix_repeated_as_current_prefix(self) -> None:
        self.assertTrue(_repeats_context("繰り返された末尾です。新しい文", "これは長い直前文で繰り返された末尾です。"))
        self.assertFalse(_repeats_context("別の新しい発話です", "これは長い直前文で繰り返された末尾です。"))


if __name__ == "__main__":
    unittest.main()
