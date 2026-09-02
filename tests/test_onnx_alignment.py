import unittest

import numpy as np

from tools.onnx_alignment import compare_arrays


class OnnxAlignmentTests(unittest.TestCase):
    def test_compare_arrays_reports_numeric_difference(self) -> None:
        reference = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        candidate = np.array([[1.0, 2.5, 2.5]], dtype=np.float32)

        comparison = compare_arrays(reference, candidate)

        self.assertEqual(comparison.shape, (1, 3))
        self.assertEqual(comparison.max_abs_error, 0.5)
        self.assertAlmostEqual(comparison.mean_abs_error, 1.0 / 3.0)
        self.assertLess(comparison.cosine_similarity, 1.0)

    def test_compare_arrays_rejects_shape_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Shape mismatch"):
            compare_arrays(np.zeros((1, 2)), np.zeros((2, 1)))


if __name__ == "__main__":
    unittest.main()
