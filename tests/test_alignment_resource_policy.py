import unittest

from subtitler.backends.existing_pipeline import alignment_execution_plan, transcription_workers
from subtitler.gpu_memory import VideoMemoryBudget
from subtitler.models import AudioChunk


class ProcessingResourcePolicyTests(unittest.TestCase):
    long_jobs = [AudioChunk(index=i, start=i * 300, end=(i + 1) * 300, samples=[]) for i in range(2)]

    def plan(self, **overrides):
        return alignment_execution_plan(**{
            "device": "cpu", "chunks": self.long_jobs,
            "total_memory_bytes": 64 * 1024**3, "available_memory_bytes": 48 * 1024**3,
            "logical_threads": 32, **overrides,
        })

    def test_cpu_resource_and_workload_limits(self):
        cases = [
            ("ample resources", {}, (2, 12, 24)),
            ("total RAM", {"total_memory_bytes": 23 * 1024**3}, (1, 24, 24)),
            ("available RAM", {"available_memory_bytes": 11 * 1024**3}, (1, 24, 24)),
            ("thread budget", {"logical_threads": 20}, (1, 15, 15)),
            ("short job", {"chunks": [AudioChunk(index=0, start=0, end=119.9, samples=[]), self.long_jobs[1]]}, (1, 24, 24)),
            ("single worker", {"requested_workers": 1}, (1, 24, 24)),
            ("configured thread cap", {"configured_torch_threads": 32}, (2, 12, 24)),
            ("small thread allocation", {"configured_torch_threads": 6}, (1, 6, 24)),
        ]
        for reason, overrides, expected in cases:
            with self.subTest(reason=reason):
                plan = self.plan(**overrides)
                self.assertEqual((plan.model_instances, plan.torch_threads, plan.thread_budget), expected)

    def test_gpu_resource_limits_and_isolation(self):
        cuda = self.plan(device="auto", cuda_available=True)
        self.assertEqual((cuda.model_instances, cuda.torch_threads), (1, 24))
        base = {"device": "auto", "cuda_available": False, "directml_is_available": True,
                "dedicated_video_memory_bytes": 16 * 1024**3, "available_video_memory_budget_bytes": 12 * 1024**3}
        directml = self.plan(**base)
        self.assertEqual((directml.model_instances, directml.torch_threads), (2, 12))
        self.assertTrue(directml.isolate_models)
        for field, value in [("dedicated_video_memory_bytes", 8), ("available_video_memory_budget_bytes", 7), ("available_memory_bytes", 5)]:
            with self.subTest(field=field):
                self.assertEqual(self.plan(**{**base, "device": "directml", field: value * 1024**3}).model_instances, 1)
        self.assertEqual(VideoMemoryBudget(16, 12, 4).available_budget_bytes, 8)
        self.assertEqual(VideoMemoryBudget(16, 12, 14).available_budget_bytes, 0)

    def test_transcription_worker_selection(self):
        for transcriber, requested, expected in [("gemini", None, 6), ("openai", 2, 2), ("local-gemma", 2, 2)]:
            with self.subTest(transcriber=transcriber):
                config = {"backend": {"transcriber": transcriber, "transcription_workers": requested}}
                self.assertEqual(transcription_workers(config), expected)
