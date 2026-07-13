import unittest

import json
import tempfile
import threading
from pathlib import Path
from unittest import mock

from subtitler.text_refiner import (
    LlamaServerTextRefiner,
    _cleanup_max_tokens,
    _cleanup_rejection_reason,
    _apply_exact_glossary_normalization,
    _parse_indexed_cleanup_response,
)
from subtitler.glossary import GlossaryEntry


def _refiner(response: str) -> tuple[LlamaServerTextRefiner, list[str]]:
    refiner = object.__new__(LlamaServerTextRefiner)
    refiner.mode = "light"
    refiner.glossary = []
    calls: list[str] = []

    def chat(prompt: str, max_tokens: int = 512) -> str:
        calls.append(prompt)
        return response

    refiner._chat = chat  # type: ignore[method-assign]
    return refiner, calls


class CleanupFailClosedTests(unittest.TestCase):
    def test_cleanup_token_allowance_grows_only_for_large_batches(self) -> None:
        self.assertEqual(_cleanup_max_tokens(1, 4096), 512)
        self.assertEqual(_cleanup_max_tokens(16, 4096), 512)
        self.assertEqual(_cleanup_max_tokens(37, 4096), 848)
        self.assertEqual(_cleanup_max_tokens(200, 2048), 1024)

    def test_large_refine_batch_passes_scaled_cleanup_token_allowance(self) -> None:
        lines = [f"字幕{index}" for index in range(1, 38)]
        response = "\n".join(f"{index}\t{text}" for index, text in enumerate(lines, start=1))
        refiner = object.__new__(LlamaServerTextRefiner)
        refiner.mode = "light"
        refiner.glossary = []
        refiner.ctx_size = 4096
        max_tokens_seen: list[int] = []

        def chat(prompt: str, max_tokens: int = 512) -> str:
            max_tokens_seen.append(max_tokens)
            return response

        refiner._chat = chat  # type: ignore[method-assign]

        self.assertEqual(refiner.refine(lines), lines)
        self.assertEqual(max_tokens_seen, [848])

    def test_indexed_cleanup_preserves_positions_and_explicitly_deletes_filler(self) -> None:
        originals = ["最初です", "えー、", "最後です"]
        cleaned, reason = _parse_indexed_cleanup_response(
            "1\t最初です\n2\t<DELETE>\n3\t最後です",
            originals,
        )

        self.assertIsNone(reason)
        self.assertEqual(cleaned, ["最初です", "", "最後です"])

    def test_indexed_cleanup_rejects_invalid_index_structures(self) -> None:
        originals = ["一つ目", "二つ目"]
        cases = {
            "1\t一つ目": "missing_index",
            "1\t一つ目\n1\t二つ目": "duplicate_index",
            "1\t一つ目\n3\t二つ目": "out_of_range_index",
            "2\t二つ目\n1\t一つ目": "out_of_order_index",
            "1. 一つ目\n2\t二つ目": "malformed_indexed_line",
            "1\t一つ目\n2\t": "malformed_indexed_line",
        }
        for response, expected_reason in cases.items():
            with self.subTest(response=response):
                cleaned, reason = _parse_indexed_cleanup_response(response, originals)
                self.assertIsNone(cleaned)
                self.assertEqual(reason, expected_reason)

    def test_indexed_cleanup_rejects_deletion_of_speech(self) -> None:
        cleaned, reason = _parse_indexed_cleanup_response("1\t<DELETE>", ["大事な内容"])

        self.assertIsNone(cleaned)
        self.assertEqual(reason, "line_1_delete_non_filler")

    def test_refine_returns_explicit_filler_deletion_for_timing_application(self) -> None:
        refiner, calls = _refiner("1\tそのまま\n2\t<DELETE>")

        self.assertEqual(refiner.refine(["そのまま", "えー、"]), ["そのまま", ""])
        self.assertIn("index<TAB>cleaned subtitle text", calls[0])
        self.assertIn("<DELETE>", calls[0])

    def test_cleanup_server_explicitly_disables_reasoning_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "cleanup.gguf"
            model.touch()
            server = mock.Mock()
            server.process = mock.Mock()
            with mock.patch("subtitler.text_refiner.LlamaServerProcess", return_value=server) as process:
                LlamaServerTextRefiner(model, None, [], "full")

        extra_args = process.call_args.kwargs["extra_args"]
        self.assertIn(["--reasoning", "off"], [extra_args[index : index + 2] for index in range(len(extra_args) - 1)])
        self.assertIn(["--reasoning-budget", "0"], [extra_args[index : index + 2] for index in range(len(extra_args) - 1)])

    def test_empty_filler_is_accepted_but_empty_speech_is_rejected(self) -> None:
        self.assertIsNone(_cleanup_rejection_reason("", "えー、"))
        self.assertIsNone(_cleanup_rejection_reason("", "、"))
        self.assertIsNone(_cleanup_rejection_reason("", "  "))
        self.assertEqual(_cleanup_rejection_reason("", "大事な内容"), "empty_non_filler_line")

    def test_severe_contraction_is_rejected(self) -> None:
        original = "え、ドラクエの配信が、え、5月の27日にあるということで"
        self.assertEqual(_cleanup_rejection_reason("ドラクエの配信が、5月の", original), "severe_contraction")

    def test_cleanup_rejects_polarity_reversal(self) -> None:
        self.assertEqual(
            _cleanup_rejection_reason(
                "ゲームプレイ映像を60分以上にお届けします",
                "ゲームプレイ映像を60分以上にお届けしません",
            ),
            "semantic_content_changed",
        )

    def test_cleanup_rejects_glossary_driven_title_substitution(self) -> None:
        self.assertEqual(
            _cleanup_rejection_reason(
                "State of Decayについてはうちのチャンネルでも配信しますので",
                "StateofPlayについてはうちのチャンネルでも配信しますので",
            ),
            "semantic_content_changed",
        )

    def test_cleanup_accepts_fillers_punctuation_and_safe_title_spacing(self) -> None:
        self.assertIsNone(
            _cleanup_rejection_reason(
                "State of Playについて話します",
                "えー、StateofPlayについて話します。",
            )
        )

    def test_cleanup_accepts_observed_short_boundary_fillers(self) -> None:
        cases = [
            ("え、本日は軽く話します", "本日は軽く話します"),
            ("ま、冗談はさておき、え、こちらです", "冗談はさておき、こちらです"),
            ("調べたら、えっと、開発陣が漏らしたんだっけ?", "調べたら、開発陣が漏らしたんだっけ?"),
            ("日本時間だと3日ですね、にあります", "日本時間だと3日にあります"),
        ]
        for original, cleaned in cases:
            with self.subTest(original=original):
                self.assertIsNone(_cleanup_rejection_reason(cleaned, original))

    def test_bare_e_is_not_treated_as_filler_inside_a_word(self) -> None:
        self.assertEqual(
            _cleanup_rejection_reason("考ます", "考えます"),
            "semantic_content_changed",
        )

    def test_cleanup_prompt_does_not_expose_glossary_for_model_inference(self) -> None:
        refiner, _ = _refiner("1\tState of Playについて話します")
        refiner.glossary = [GlossaryEntry("State of Play"), GlossaryEntry("State of Decay")]

        prompt = refiner._prompt_one("StateofPlayについて話します")

        self.assertNotIn("Glossary:", prompt)
        self.assertNotIn("State of Decay", prompt)

    def test_cleanup_applies_only_exact_glossary_presentation_normalization(self) -> None:
        glossary = [GlossaryEntry("State of Play"), GlossaryEntry("State of Decay")]

        self.assertEqual(
            _apply_exact_glossary_normalization("StateofPlayについて", glossary),
            "State of Playについて",
        )
        self.assertEqual(
            _apply_exact_glossary_normalization("ステートオブプレイについて", glossary),
            "ステートオブプレイについて",
        )

    def test_indexed_cleanup_normalizes_exact_glossary_term_after_validation(self) -> None:
        cleaned, reason = _parse_indexed_cleanup_response(
            "1\tStateofPlayについて",
            ["StateofPlayについて"],
            [GlossaryEntry("State of Play"), GlossaryEntry("State of Decay")],
        )

        self.assertIsNone(reason)
        self.assertEqual(cleaned, ["State of Playについて"])

    def test_reasoning_is_rejected_for_single_subtitle(self) -> None:
        refiner, _ = _refiner("**Thinking Process:**\nまず文脈を分析します。\n**出力:**\n修正文")

        self.assertEqual(refiner.refine(["元の字幕"]), ["元の字幕"])

    def test_failed_batch_retains_originals_without_per_line_fanout(self) -> None:
        refiner, calls = _refiner("分析:\n1. 修正文\n2. 修正文")
        originals = ["一つ目", "二つ目", "三つ目"]

        self.assertEqual(refiner.refine(originals), originals)
        self.assertEqual(len(calls), 1)

    def test_rejected_batch_records_local_diagnostics_without_returning_raw_text(self) -> None:
        raw = "**Thinking Process:**\n分析します\n**出力:**\n修正文"
        refiner, _ = _refiner(raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cleanup_rejections.jsonl"
            refiner.cleanup_diagnostics_path = path
            refiner._cleanup_diagnostics_lock = threading.Lock()
            refiner._cleanup_diagnostics_sequence = 0
            def chat_with_metadata(prompt: str, max_tokens: int = 512) -> str:
                refiner._chat_context.metadata = {
                    "finish_reason": "length",
                    "usage": {"completion_tokens": 512},
                    "max_tokens": 512,
                    "appears_token_limited": True,
                }
                return raw

            refiner._chat = chat_with_metadata  # type: ignore[method-assign]

            originals = ["元の一", "元の二"]
            self.assertEqual(refiner.refine(originals), originals)

            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["reason"], "malformed_indexed_line")
            self.assertEqual(entry["input_lines"], originals)
            self.assertEqual(entry["raw_response"], raw)
            self.assertEqual(entry["raw_nonblank_line_count"], 4)
            self.assertTrue(entry["appears_token_limited"])
            self.assertEqual(entry["finish_reason"], "length")


if __name__ == "__main__":
    unittest.main()
