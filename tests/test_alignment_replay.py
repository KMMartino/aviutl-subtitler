import json
import tempfile
import unittest
import wave
from pathlib import Path

from tools.alignment_benchmark import benchmark_torch_threads, load_replay, parse_args


class AlignmentReplayTests(unittest.TestCase):
    def test_loads_complete_captured_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            directory = Path(temp_name) / "sample.alignment_replay"
            directory.mkdir()
            wav_path = directory / "job-00000-chunk-7.wav"
            with wave.open(str(wav_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 160)
            manifest = {
                "format_version": 1,
                "workers_at_capture": 4,
                "alignment": {
                    "model_name": "model",
                    "language": "eng",
                    "device": "cpu",
                    "split_size": "word",
                    "sample_rate": 16000,
                    "emission_batch_size": 4,
                    "torch_threads": 2,
                    "max_split_depth": 4,
                },
                "capture_complete": True,
                "job_count": 1,
                "jobs": [
                    {
                        "chunk_index": 7,
                        "start": 1.0,
                        "end": 1.01,
                        "text": "captured text",
                        "wav_file": wav_path.name,
                    }
                ],
            }
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            _, replay_jobs = load_replay(directory)
            self.assertEqual(replay_jobs[0].chunk.wav_path, wav_path)
            self.assertEqual(replay_jobs[0].text, "captured text")

    def test_thread_policy_reproduces_capture_and_preserves_total_budget(self) -> None:
        manifest = {"workers_at_capture": 4, "alignment": {"torch_threads": 4}}
        self.assertEqual(benchmark_torch_threads(manifest, 4), (4, 16))
        self.assertEqual(benchmark_torch_threads(manifest, 2), (8, 16))
        self.assertEqual(benchmark_torch_threads(manifest, 1), (16, 16))

    def test_explicit_thread_override_is_accepted_for_single_run(self) -> None:
        args = parse_args(
            [
                "bundle",
                "--single-worker",
                "1",
                "--torch-threads",
                "12",
                "--batch-size",
                "8",
                "--isolated-processes",
            ]
        )
        self.assertEqual(args.single_worker, 1)
        self.assertEqual(args.torch_threads, 12)
        self.assertEqual(args.batch_size, 8)
        self.assertTrue(args.isolated_processes)


if __name__ == "__main__":
    unittest.main()
