import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
