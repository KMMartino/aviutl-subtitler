import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.alignment_pool import AlignmentConfig, AlignmentPool, _InProcessAlignmentPool
from subtitler.models import AlignedChunk, AudioChunk, TranscriptChunk
from subtitler.profiling import PipelineProfiler


class AlignmentPoolDedicatedModelTests(unittest.TestCase):
    def test_longest_processing_time_balances_isolated_process_lanes(self) -> None:
        pool = AlignmentPool.__new__(AlignmentPool)
        pool._pending = [
            TranscriptChunk(AudioChunk(0, 0.0, 15.0, []), "short"),
            TranscriptChunk(AudioChunk(1, 15.0, 503.0, []), "long"),
            TranscriptChunk(AudioChunk(2, 503.0, 697.0, []), "medium"),
            TranscriptChunk(AudioChunk(3, 697.0, 1275.0, []), "longest"),
        ]

        lanes = pool._balanced_lanes(2)

        self.assertEqual([[item.chunk.index for item in lane] for lane in lanes], [[3, 0], [1, 2]])

    def test_isolated_model_layout_starts_one_worker_per_process(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.messages: list[tuple[str, object]] = []

            def send(self, message: tuple[str, object]) -> None:
                self.messages.append(message)

            def recv(self) -> tuple[str, object]:
                return "ok", ([], [])

            def close(self) -> None:
                pass

        class FakeProcess:
            exitcode = 0

            def start(self) -> None:
                pass

            def join(self, timeout: int) -> None:
                pass

            def is_alive(self) -> bool:
                return False

            def terminate(self) -> None:
                raise AssertionError("clean child must not be terminated")

        class FakeContext:
            def __init__(self) -> None:
                self.child_worker_counts: list[int] = []

            def Pipe(self) -> tuple[FakeConnection, FakeConnection]:
                return FakeConnection(), FakeConnection()

            def Process(self, **kwargs: object) -> FakeProcess:
                args = kwargs["args"]
                assert isinstance(args, tuple)
                self.child_worker_counts.append(args[1])
                return FakeProcess()

        config = AlignmentConfig(
            model_name="unused",
            language="eng",
            device="directml",
            split_size="word",
            temp_dir=Path("."),
            sample_rate=16000,
            emission_batch_size=1,
            torch_threads=12,
            isolate_models=True,
        )
        context = FakeContext()
        with mock.patch("subtitler.alignment_pool.get_context", return_value=context):
            pool = AlignmentPool(2, config, PipelineProfiler(False, None))
            self.assertEqual(pool.close_and_collect(), [])

        self.assertEqual(context.child_worker_counts, [1, 1])

    def test_each_worker_uses_one_dedicated_aligner(self) -> None:
        worker_count = 2
        constructor_calls = 0
        active_calls = 0
        peak_active_calls = 0
        state_lock = threading.Lock()
        all_workers_active = threading.Event()
        release_workers = threading.Event()

        class ObservableAligner:
            def __init__(self, **_kwargs: object) -> None:
                nonlocal constructor_calls
                with state_lock:
                    constructor_calls += 1

            def align(self, item: TranscriptChunk) -> AlignedChunk:
                nonlocal active_calls, peak_active_calls
                with state_lock:
                    active_calls += 1
                    peak_active_calls = max(peak_active_calls, active_calls)
                    if active_calls == worker_count:
                        all_workers_active.set()
                release_workers.wait(timeout=5)
                with state_lock:
                    active_calls -= 1
                return AlignedChunk(chunk=item.chunk, text=item.text, tokens=[])

        config = AlignmentConfig(
            model_name="unused",
            language="eng",
            device="cpu",
            split_size="word",
            temp_dir=Path("."),
            sample_rate=16000,
            emission_batch_size=4,
            torch_threads=1,
        )
        profiler = PipelineProfiler(enabled=False, output_path=None)

        with mock.patch("subtitler.alignment_pool.ForcedAligner", ObservableAligner):
            pool = _InProcessAlignmentPool(worker_count, config, profiler)
            for index in range(worker_count):
                chunk = AudioChunk(index=index, start=float(index), end=float(index + 1), samples=[])
                pool.submit(TranscriptChunk(chunk=chunk, text=f"chunk {index}"))

            self.assertTrue(all_workers_active.wait(timeout=5))
            release_workers.set()
            results = pool.close_and_collect()

        self.assertEqual(constructor_calls, worker_count)
        self.assertEqual(peak_active_calls, worker_count)
        self.assertEqual([result.chunk.index for result in results], list(range(worker_count)))

    def test_subprocess_proxy_sends_path_metadata_and_restores_original_chunk(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.messages: list[tuple[str, object]] = []

            def send(self, message: tuple[str, object]) -> None:
                self.messages.append(message)

            def recv(self) -> tuple[str, object]:
                transcript = self.messages[0][1]
                assert isinstance(transcript, TranscriptChunk)
                aligned = AlignedChunk(chunk=transcript.chunk, text=transcript.text, tokens=[])
                return ("ok", ([aligned], []))

            def close(self) -> None:
                pass

        class FakeProcess:
            exitcode = 0

            def start(self) -> None:
                pass

            def join(self, timeout: int) -> None:
                pass

            def is_alive(self) -> bool:
                return False

            def terminate(self) -> None:
                raise AssertionError("clean child must not be terminated")

        parent_connection = FakeConnection()
        child_connection = FakeConnection()

        class FakeContext:
            def Pipe(self) -> tuple[FakeConnection, FakeConnection]:
                return parent_connection, child_connection

            def Process(self, **_kwargs: object) -> FakeProcess:
                return FakeProcess()

        with tempfile.TemporaryDirectory() as directory:
            config = AlignmentConfig(
                model_name="unused",
                language="eng",
                device="cpu",
                split_size="word",
                temp_dir=Path(directory),
                sample_rate=16000,
                emission_batch_size=4,
                torch_threads=1,
            )
            original = AudioChunk(index=7, start=1.0, end=1.01, samples=[0.0] * 160)
            profiler = PipelineProfiler(enabled=False, output_path=None)
            with mock.patch("subtitler.alignment_pool.get_context", return_value=FakeContext()):
                pool = AlignmentPool(1, config, profiler)
                pool.submit(TranscriptChunk(chunk=original, text="hello"))
                results = pool.close_and_collect()

            message, payload = parent_connection.messages[0]
            self.assertEqual(message, "submit")
            self.assertIsInstance(payload, TranscriptChunk)
            assert isinstance(payload, TranscriptChunk)
            self.assertEqual(payload.chunk.samples, [])
            self.assertTrue(payload.chunk.wav_path and payload.chunk.wav_path.exists())
            self.assertIs(results[0].chunk, original)


if __name__ == "__main__":
    unittest.main()
