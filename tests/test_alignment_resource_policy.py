import os
import unittest
from unittest.mock import patch

from subtitler.backends.existing_pipeline import default_align_workers


class AlignmentResourcePolicyTests(unittest.TestCase):
    def test_gpu_defaults_to_one_model_replica(self) -> None:
        with patch.object(os, "cpu_count", return_value=32):
            self.assertEqual(default_align_workers("cuda", 64 * 1024**3), 1)

    def test_auto_with_cuda_defaults_to_one_model_replica(self) -> None:
        with patch.object(os, "cpu_count", return_value=32):
            self.assertEqual(default_align_workers("auto", 64 * 1024**3, cuda_available=True), 1)

    def test_auto_without_cuda_uses_cpu_resource_policy(self) -> None:
        with patch.object(os, "cpu_count", return_value=32):
            self.assertEqual(default_align_workers("auto", 5 * 1024**3, cuda_available=False), 2)

    def test_cpu_workers_are_limited_by_available_memory(self) -> None:
        with patch.object(os, "cpu_count", return_value=32):
            self.assertEqual(default_align_workers("cpu", 5 * 1024**3), 2)

    def test_cpu_workers_are_limited_by_core_count(self) -> None:
        with patch.object(os, "cpu_count", return_value=8):
            self.assertEqual(default_align_workers("cpu", 64 * 1024**3), 2)

    def test_cpu_always_has_one_worker(self) -> None:
        with patch.object(os, "cpu_count", return_value=1):
            self.assertEqual(default_align_workers("cpu", 256 * 1024**2), 1)


if __name__ == "__main__":
    unittest.main()
