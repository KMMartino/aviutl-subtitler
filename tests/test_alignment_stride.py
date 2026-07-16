import math
import unittest

from subtitler.aligner import _precise_emission_stride_ms


class FakeSizedTensor:
    def __init__(self, size: int) -> None:
        self._size = size

    def size(self, dimension: int) -> int:
        self.assert_dimension_zero(dimension)
        return self._size

    @staticmethod
    def assert_dimension_zero(dimension: int) -> None:
        if dimension != 0:
            raise AssertionError(f"unexpected dimension: {dimension}")


class AlignmentStrideTests(unittest.TestCase):
    def test_precise_emission_stride_uses_actual_frame_count(self) -> None:
        audio_waveform = FakeSizedTensor(16000 * 30 + 1)
        emissions = FakeSizedTensor(1500)

        stride = _precise_emission_stride_ms(audio_waveform, emissions)

        expected = float(16000 * 30 + 1) * 1000.0 / 1500.0 / 16000.0
        self.assertEqual(stride, expected)
        self.assertNotEqual(stride, math.ceil(stride))

if __name__ == "__main__":
    unittest.main()
