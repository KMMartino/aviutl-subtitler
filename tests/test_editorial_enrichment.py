import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.editorial_enrichment import (
    _burst_samples,
    _sample_labels,
    _vision_request,
)
from subtitler.media_analysis import VisualSample


class EditorialEnrichmentTests(unittest.TestCase):
    def test_targeted_burst_uses_five_ordered_frames(self) -> None:
        centers = [{"index": 0, "timestamp_ms": 10_000, "burst_radius_ms": 2_000}]
        observed: list[float] = []

        def extract(_media_path, *, timestamps, **_kwargs):
            observed.extend(timestamps)
            return [VisualSample(index, value, Path(f"{index}.jpg")) for index, value in enumerate(timestamps)]

        with patch("subtitler.editorial_enrichment._extract_samples", side_effect=extract):
            result = _burst_samples(
                Path("video.mp4"), centers, 30_000, Path("."), "ffmpeg"
            )

        self.assertEqual(observed, [8.0, 9.0, 10.0, 11.0, 12.0])
        self.assertEqual(len(result), 5)
        self.assertEqual(
            [label.split()[2].rstrip(",") for label in _sample_labels(centers)],
            ["before", "approaching", "at", "leaving", "after"],
        )

    def test_editorial_vision_request_uses_requested_reasoning_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "frame.jpg"
            frame.write_bytes(b"jpeg")
            captured = {}

            def request(_method, _url, payload, *_args, **_kwargs):
                captured.update(payload)
                return {
                    "output": [{"content": [{"text": "{}"}]}],
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }

            with (
                patch("subtitler.editorial_enrichment.require_api_key", return_value="key"),
                patch("subtitler.editorial_enrichment.request_json", side_effect=request),
            ):
                _vision_request(
                    "gpt-5.6-luna",
                    "Review",
                    [VisualSample(0, 1.0, frame)],
                    ["frame"],
                    reasoning_effort="medium",
                )

        self.assertEqual(captured["reasoning"], {"effort": "medium"})


if __name__ == "__main__":
    unittest.main()
