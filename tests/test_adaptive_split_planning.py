import json
import tempfile
import unittest
from pathlib import Path

from subtitler.models import AlignedChunk, AlignedToken, AudioChunk, SplitPlanResult
from subtitler.splitter import BoundaryOption, _choose_returned_boundary_options, split_token_chain
from subtitler.subtitle_planner import build_grouped_subtitles


def _tokens(text: str, *, pause_after: int | None = None) -> list[AlignedToken]:
    result = []
    cursor = 0.0
    for index, char in enumerate(text):
        result.append(AlignedToken(char, cursor, cursor + 0.08, "char"))
        cursor += 0.08
        if pause_after is not None and index + 1 == pause_after:
            cursor += 0.5
    return result


class _LocalSingleSplitter:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def supports_multi_split(self) -> bool:
        return False

    def split_input_capacity(self, max_chars: int) -> int:
        return 120

    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        self.inputs.append(text)
        return SplitPlanResult(
            selected_ids=[candidate_ids[-1]],
            candidate_ids=candidate_ids,
            accepted=True,
            reject_reason="none",
            input_text=text,
        )


class _HostedMultiSplitter:
    def __init__(self) -> None:
        self.calls = 0

    def supports_multi_split(self) -> bool:
        return True

    def split_input_capacity(self, max_chars: int) -> int:
        return 4000

    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        self.calls += 1
        selected: dict[str, str] = {}
        for candidate_id in candidate_ids:
            selected[candidate_id[:-1]] = candidate_id
        return SplitPlanResult(
            selected_ids=list(selected.values()),
            candidate_ids=candidate_ids,
            accepted=True,
            reject_reason="none",
            input_text=text,
        )


class _HostedDuplicateSplitter:
    def __init__(self) -> None:
        self.calls = 0

    def supports_multi_split(self) -> bool:
        return True

    def split_input_capacity(self, max_chars: int) -> int:
        return 4000

    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        self.calls += 1
        first_by_zone: dict[str, str] = {}
        for candidate_id in candidate_ids:
            first_by_zone.setdefault(candidate_id[:-1], candidate_id)
        return SplitPlanResult(
            selected_ids=list(first_by_zone.values()),
            parsed_ids=candidate_ids[:],
            candidate_ids=candidate_ids,
            accepted=True,
            reject_reason="none",
            input_text=text,
        )


class _HostedDiagnosticSplitter(_HostedMultiSplitter):
    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        result = super().select_split_boundaries(
            text,
            annotated_text,
            candidate_ids,
            max_chars,
            multiple=multiple,
        )
        result.request_attempts = [
            {
                "provider": "openai",
                "model": "split-model",
                "operation": "split",
                "source_chars": len(text),
                "annotated_chars": len(annotated_text),
                "prompt_chars": len(annotated_text) + 200,
                "candidate_count": len(candidate_ids),
                "zone_count": len({candidate_id[:-1] for candidate_id in candidate_ids}),
                "max_chars": max_chars,
                "max_tokens": 64,
                "request_attempt": 1,
                "request_attempts_allowed": 3,
                "started_at": "2026-08-04T00:00:00+00:00",
                "elapsed_sec": 61.0,
                "timeout_sec": 90.0,
                "outcome": "success",
                "prompt": text,
                "authorization": "Bearer secret-key",
            }
        ]
        return result


class AdaptiveSplitPlanningTests(unittest.TestCase):
    def test_enabled_split_diagnostics_write_safe_request_sidecar(self) -> None:
        text = "あ漢" * 30
        tokens = _tokens(text)
        aligned = AlignedChunk(AudioChunk(0, 0.0, 5.0, []), text, tokens)

        with tempfile.TemporaryDirectory() as temp_name:
            profile_path = Path(temp_name) / "video.llm_split.csv"
            subtitles = build_grouped_subtitles(
                [aligned],
                max_chars=40,
                min_duration=0.2,
                max_duration=60.0,
                gap_threshold=0.3,
                regroup_gap_sec=1.2,
                llm_splitter=_HostedDiagnosticSplitter(),
                llm_split_profile_path=profile_path,
            )
            request_path = Path(temp_name) / "video.llm_split_requests.jsonl"
            request_rows = [
                json.loads(line)
                for line in request_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual("".join(subtitle.text for subtitle in subtitles), text)
        self.assertTrue(request_rows)
        self.assertEqual(request_rows[0]["outcome"], "success")
        self.assertIn("elapsed_sec", request_rows[0])
        self.assertIn("timeout_sec", request_rows[0])
        serialized = json.dumps(request_rows, ensure_ascii=False)
        self.assertNotIn(text, serialized)
        self.assertNotIn("secret-key", serialized)
        self.assertNotIn("prompt", request_rows[0])
        self.assertNotIn("authorization", request_rows[0])

    def test_duplicate_zone_answers_are_scored_as_complete_paths(self) -> None:
        tokens = _tokens("あ漢" * 40)
        options = [
            BoundaryOption("Z1A", 1, 25),
            BoundaryOption("Z1B", 1, 33),
            BoundaryOption("Z2A", 2, 57),
            BoundaryOption("Z2B", 2, 65),
        ]

        selected = _choose_returned_boundary_options(
            tokens,
            options,
            ["Z1A", "Z1B", "Z2A", "Z2B"],
            40,
            60.0,
            multiple=True,
        )

        self.assertEqual([option.id for option in selected], ["Z1B", "Z2B"])

    def test_hosted_duplicate_answers_use_scored_path_not_first_ids(self) -> None:
        text = "あ漢" * 62 + "あ"
        splitter = _HostedDuplicateSplitter()

        subtitles = split_token_chain(
            _tokens(text),
            max_chars=40,
            max_duration=60.0,
            llm_splitter=splitter,
        )

        self.assertEqual(splitter.calls, 1)
        self.assertEqual([len(subtitle.text) for subtitle in subtitles], [33, 32, 32, 28])
        self.assertEqual("".join(subtitle.text for subtitle in subtitles), text)

    def test_local_single_split_uses_rolling_context_instead_of_skipping_long_input(self) -> None:
        text = "あ" * 500
        splitter = _LocalSingleSplitter()

        subtitles = split_token_chain(
            _tokens(text),
            max_chars=40,
            max_duration=60.0,
            llm_splitter=splitter,
        )

        self.assertEqual("".join(subtitle.text for subtitle in subtitles), text)
        self.assertTrue(splitter.inputs)
        self.assertTrue(all(len(item) <= 120 for item in splitter.inputs))
        self.assertTrue(all(len(subtitle.text) <= 40 for subtitle in subtitles))

    def test_hosted_splitter_can_plan_all_boundaries_in_one_request(self) -> None:
        text = "あ" * 125
        splitter = _HostedMultiSplitter()

        subtitles = split_token_chain(
            _tokens(text),
            max_chars=40,
            max_duration=60.0,
            llm_splitter=splitter,
        )

        self.assertEqual(splitter.calls, 1)
        self.assertEqual("".join(subtitle.text for subtitle in subtitles), text)
        self.assertTrue(all(len(subtitle.text) <= 40 for subtitle in subtitles))
        self.assertLessEqual(len(subtitles), 4)

    def test_hosted_splitter_is_not_called_for_an_in_limit_chain(self) -> None:
        splitter = _HostedMultiSplitter()

        subtitles = split_token_chain(
            _tokens("短い字幕です"),
            max_chars=40,
            max_duration=6.0,
            llm_splitter=splitter,
        )

        self.assertEqual(splitter.calls, 0)
        self.assertEqual([subtitle.text for subtitle in subtitles], ["短い字幕です"])

    def test_acoustic_pause_is_used_without_punctuation(self) -> None:
        text = "あ" * 35 + "い" * 35

        subtitles = split_token_chain(
            _tokens(text, pause_after=35),
            max_chars=40,
            max_duration=60.0,
        )

        self.assertEqual([len(subtitle.text) for subtitle in subtitles], [35, 35])
        self.assertIn("acoustic_pause", subtitles[0].split_source)

    def test_duration_limit_participates_in_boundary_selection(self) -> None:
        text = "これは短い文です"

        subtitles = split_token_chain(
            [
                AlignedToken(char, index * 1.0, index * 1.0 + 0.1, "char")
                for index, char in enumerate(text)
            ],
            max_chars=40,
            max_duration=3.0,
        )

        self.assertGreater(len(subtitles), 1)
        self.assertTrue(all(subtitle.end_time - subtitle.start_time <= 3.0 for subtitle in subtitles))

    def test_duration_only_fallback_gives_local_llm_first_choice(self) -> None:
        splitter = _LocalSingleSplitter()

        subtitles = split_token_chain(
            [
                AlignedToken(char, index * 0.8, index * 0.8 + 0.8, "char")
                for index, char in enumerate("これはかなり長い発話です")
            ],
            max_chars=40,
            max_duration=6.0,
            llm_splitter=splitter,
        )

        self.assertTrue(splitter.inputs)
        self.assertGreater(len(subtitles), 1)


if __name__ == "__main__":
    unittest.main()
