import unittest

from subtitler.editorial_enrichment import _spaced_events


class EditorialEnrichmentTests(unittest.TestCase):
    def test_acoustic_events_keep_first_candidate_within_each_spacing_window(self) -> None:
        events = [
            {"start_ms": 1_000, "score": 0.9},
            {"start_ms": 1_400, "score": 0.6},
            {"start_ms": 5_000, "score": 0.7},
        ]

        result = _spaced_events(events, minimum_gap_ms=1_000, limit=10)

        self.assertEqual(
            [(item["start_ms"], item["score"]) for item in result],
            [(1_000, 0.9), (5_000, 0.7)],
        )

    def test_acoustic_event_limit_keeps_first_candidates(self) -> None:
        events = [
            {"start_ms": 1_000, "score": 0.8},
            {"start_ms": 3_000, "score": 0.6},
            {"start_ms": 5_000, "score": 0.4},
        ]

        result = _spaced_events(events, minimum_gap_ms=500, limit=2)

        self.assertEqual([item["start_ms"] for item in result], [1_000, 3_000])


if __name__ == "__main__":
    unittest.main()
