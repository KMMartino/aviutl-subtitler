import unittest
from unittest import mock
from pathlib import Path

from subtitler.backends.existing_pipeline import transcribe_and_align_hosted
from subtitler.external_transcribers import DeadTranscriptionRequest, FallbackTranscriber, MalformedTranscriptionResponse
from subtitler.models import AudioChunk, TranscriptChunk
from subtitler.profiling import PipelineProfiler


def _chunk(index: int) -> AudioChunk:
    return AudioChunk(index=index, start=float(index), end=float(index + 1), samples=[])


class _Primary:
    provider = "gemini"
    model = "primary"

    def __init__(self, normal_failures: dict[int, Exception]) -> None:
        self.normal_failures = normal_failures
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        self.calls.append((chunk.index, previous_transcript))
        if previous_transcript is None and chunk.index in self.normal_failures:
            raise self.normal_failures[chunk.index]
        return TranscriptChunk(chunk, f"復帰{chunk.index}" if previous_transcript else f"通常{chunk.index}")


class _Backup:
    provider = "gemini"
    model = "backup"

    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        self.calls.append((chunk.index, previous_transcript))
        return TranscriptChunk(chunk, f"バックアップ{chunk.index}")


class HostedContextRecoveryTests(unittest.TestCase):
    def _run(self, primary: _Primary, backup: _Backup, chunks: list[AudioChunk]):
        pool = mock.Mock()
        pool.close_and_collect.return_value = []
        with mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", return_value=pool):
            aligned, failed = transcribe_and_align_hosted(
                chunks,
                FallbackTranscriber(primary, backup),
                mock.Mock(),
                PipelineProfiler(False, None),
                workers=2,
                align_workers=1,
            )
        return pool, aligned, failed

    def test_quality_failure_retries_primary_with_immediate_previous_text(self) -> None:
        primary = _Primary({1: MalformedTranscriptionResponse("suspect")})
        backup = _Backup()

        pool, _, failed = self._run(primary, backup, [_chunk(0), _chunk(1)])

        self.assertEqual(primary.calls.count((1, None)), 1)
        self.assertIn((1, "通常0"), primary.calls)
        self.assertEqual(backup.calls, [])
        self.assertEqual(failed, [])
        self.assertEqual([call.args[0].text for call in pool.submit.call_args_list], ["通常0", "復帰1"])

    def test_transport_failure_bypasses_context_and_uses_context_free_backup(self) -> None:
        primary = _Primary({1: DeadTranscriptionRequest("timeout")})
        backup = _Backup()

        pool, _, failed = self._run(primary, backup, [_chunk(0), _chunk(1)])

        self.assertNotIn((1, "通常0"), primary.calls)
        self.assertEqual(backup.calls, [(1, None)])
        self.assertEqual(failed, [])
        self.assertEqual(pool.submit.call_args_list[-1].args[0].text, "バックアップ1")

    def test_first_chunk_quality_failure_skips_context_and_uses_backup(self) -> None:
        primary = _Primary({0: MalformedTranscriptionResponse("empty")})
        backup = _Backup()

        _, _, failed = self._run(primary, backup, [_chunk(0)])

        self.assertEqual(primary.calls, [(0, None)])
        self.assertEqual(backup.calls, [(0, None)])
        self.assertEqual(failed, [])

    def test_aligners_start_before_transcription_and_are_capped_by_segment_count(self) -> None:
        events: list[str] = []
        pool = mock.Mock()
        pool.close_and_collect.return_value = []
        primary = _Primary({})
        original_transcribe = primary.transcribe

        def transcribe(chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
            events.append("transcribe")
            return original_transcribe(chunk, previous_transcript)

        primary.transcribe = transcribe  # type: ignore[method-assign]

        def build_pool(*args):
            events.append("aligners")
            return pool

        with mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", side_effect=build_pool) as factory:
            transcribe_and_align_hosted(
                [_chunk(0), _chunk(1)],
                FallbackTranscriber(primary, _Backup()),
                mock.Mock(),
                PipelineProfiler(False, None),
                workers=2,
                align_workers=8,
            )

        self.assertEqual(events[0], "aligners")
        self.assertEqual(factory.call_args.args[0], 2)

    def test_failed_hosted_group_recovers_by_transcribing_smaller_segments(self) -> None:
        class DurationSensitivePrimary:
            provider = "openai"
            model = "primary"

            def transcribe(self, chunk, previous_transcript=None):
                if chunk.end - chunk.start > 0.6:
                    raise MalformedTranscriptionResponse("empty")
                return TranscriptChunk(chunk, "前半" if chunk.start < 0.5 else "後半")

        parent = AudioChunk(index=0, start=0.0, end=1.0, samples=[])
        halves = [
            AudioChunk(index=0, start=0.0, end=0.5, samples=[]),
            AudioChunk(index=0, start=0.5, end=1.0, samples=[]),
        ]
        pool = mock.Mock()
        pool.close_and_collect.return_value = []
        with (
            mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", return_value=pool),
            mock.patch("subtitler.backends.existing_pipeline.split_chunk_with_tighter_vad", return_value=halves),
        ):
            _, failed = transcribe_and_align_hosted(
                [parent],
                FallbackTranscriber(DurationSensitivePrimary(), None),
                mock.Mock(),
                PipelineProfiler(False, None),
                workers=1,
                align_workers=1,
                recovery_max_split_depth=1,
                recovery_temp_dir=Path("recovery"),
            )

        self.assertEqual(failed, [])
        self.assertEqual(pool.submit.call_args.args[0].text, "前半後半")

    def test_split_recovery_retains_success_when_one_smaller_segment_still_fails(self) -> None:
        class PartiallyRecoverablePrimary:
            provider = "openai"
            model = "primary"

            def transcribe(self, chunk, previous_transcript=None):
                if chunk.start >= 0.5 or chunk.end - chunk.start > 0.6:
                    raise MalformedTranscriptionResponse("empty")
                return TranscriptChunk(chunk, "保存する前半")

        parent = AudioChunk(index=0, start=0.0, end=1.0, samples=[])
        halves = [
            AudioChunk(index=0, start=0.0, end=0.5, samples=[]),
            AudioChunk(index=0, start=0.5, end=1.0, samples=[]),
        ]
        pool = mock.Mock()
        pool.close_and_collect.return_value = []
        with (
            mock.patch("subtitler.backends.existing_pipeline.AlignmentPool", return_value=pool),
            mock.patch(
                "subtitler.backends.existing_pipeline.split_chunk_with_tighter_vad",
                return_value=halves,
            ) as split,
        ):
            _, failed = transcribe_and_align_hosted(
                [parent],
                FallbackTranscriber(PartiallyRecoverablePrimary(), None),
                mock.Mock(),
                PipelineProfiler(False, None),
                workers=1,
                align_workers=1,
                recovery_max_split_depth=1,
                recovery_temp_dir=Path("recovery"),
            )

        self.assertEqual(pool.submit.call_args.args[0].text, "保存する前半")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].chunk.start, 0.5)
        self.assertIn("empty", failed[0].error or "")
        self.assertEqual(split.call_args.kwargs["max_pieces"], 2)
        self.assertEqual(split.call_args.kwargs["recovery_min_silence_ms"], 400)
        self.assertEqual(split.call_args.kwargs["recovery_speech_pad_ms"], 200)


if __name__ == "__main__":
    unittest.main()
