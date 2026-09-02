import unittest

from subtitler.backends.existing_pipeline import alignment_execution_plan
from subtitler.models import AudioChunk


class AlignmentResourcePolicyTests(unittest.TestCase):
    long_jobs = [
        AudioChunk(index=0, start=0.0, end=300.0, samples=[]),
        AudioChunk(index=1, start=300.0, end=600.0, samples=[]),
    ]

    def plan(self, **overrides):
        values = {
            "device": "cpu",
            "chunks": self.long_jobs,
            "total_memory_bytes": 64 * 1024**3,
            "available_memory_bytes": 48 * 1024**3,
            "logical_threads": 32,
        }
        values.update(overrides)
        return alignment_execution_plan(**values)

    def test_7950x3d_class_machine_selects_two_models_at_twelve_threads(self) -> None:
        plan = self.plan()
        self.assertEqual((plan.model_instances, plan.torch_threads, plan.thread_budget), (2, 12, 24))

    def test_total_memory_below_24_gib_selects_one_model(self) -> None:
        plan = self.plan(total_memory_bytes=23 * 1024**3)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 24))

    def test_available_memory_below_12_gib_selects_one_model(self) -> None:
        plan = self.plan(available_memory_bytes=11 * 1024**3)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 24))

    def test_dual_model_requires_at_least_eight_threads_each(self) -> None:
        plan = self.plan(logical_threads=20)
        self.assertEqual((plan.model_instances, plan.torch_threads, plan.thread_budget), (1, 15, 15))

    def test_dual_model_requires_two_substantial_jobs(self) -> None:
        short_jobs = [AudioChunk(index=0, start=0.0, end=119.9, samples=[]), self.long_jobs[1]]
        plan = self.plan(chunks=short_jobs)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 24))

    def test_explicit_single_worker_disables_dual_model_plan(self) -> None:
        plan = self.plan(requested_workers=1)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 24))

    def test_configured_threads_are_capped_at_75_percent(self) -> None:
        plan = self.plan(configured_torch_threads=32)
        self.assertEqual((plan.model_instances, plan.torch_threads), (2, 12))

    def test_configured_threads_below_eight_disable_dual_model_plan(self) -> None:
        plan = self.plan(configured_torch_threads=6)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 6))

    def test_gpu_uses_one_model_lane(self) -> None:
        plan = self.plan(device="auto", cuda_available=True)
        self.assertEqual((plan.model_instances, plan.torch_threads), (1, 24))

    def test_directml_uses_two_isolated_models_with_live_memory_headroom(self) -> None:
        plan = self.plan(
            device="auto",
            cuda_available=False,
            directml_is_available=True,
            dedicated_video_memory_bytes=16 * 1024**3,
            available_video_memory_budget_bytes=12 * 1024**3,
        )
        self.assertEqual((plan.model_instances, plan.torch_threads), (2, 12))
        self.assertTrue(plan.isolate_models)

    def test_directml_requires_twelve_gib_dedicated_vram(self) -> None:
        plan = self.plan(
            device="directml",
            dedicated_video_memory_bytes=8 * 1024**3,
            available_video_memory_budget_bytes=8 * 1024**3,
        )
        self.assertEqual(plan.model_instances, 1)

    def test_directml_requires_eight_gib_available_vram_budget(self) -> None:
        plan = self.plan(
            device="directml",
            dedicated_video_memory_bytes=16 * 1024**3,
            available_video_memory_budget_bytes=7 * 1024**3,
        )
        self.assertEqual(plan.model_instances, 1)

    def test_directml_requires_six_gib_available_system_ram(self) -> None:
        plan = self.plan(
            device="directml",
            available_memory_bytes=5 * 1024**3,
            dedicated_video_memory_bytes=16 * 1024**3,
            available_video_memory_budget_bytes=12 * 1024**3,
        )
        self.assertEqual(plan.model_instances, 1)


if __name__ == "__main__":
    unittest.main()
