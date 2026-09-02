import unittest

from subtitler.gpu_memory import VideoMemoryBudget


class VideoMemoryBudgetTests(unittest.TestCase):
    def test_available_budget_is_clamped_at_zero(self) -> None:
        self.assertEqual(VideoMemoryBudget(16, 12, 4).available_budget_bytes, 8)
        self.assertEqual(VideoMemoryBudget(16, 12, 14).available_budget_bytes, 0)


if __name__ == "__main__":
    unittest.main()
