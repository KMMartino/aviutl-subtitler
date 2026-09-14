import unittest

from subtitler.editorial_passages import build_work_packets, semantic_boundary_candidates
from subtitler.errors import SubtitlerError


def passage(start, end, identifier):
    return {"start_ms": start, "end_ms": end, "passage_id": identifier, "purpose": "A complete event"}


class EditorialPassagesTests(unittest.TestCase):
    def test_semantic_passage_crosses_old_five_minute_boundary_without_splitting(self):
        passages = [passage(0, 200000, "a"), passage(200000, 360000, "b"),
                    passage(360000, 500000, "c"), passage(500000, 720000, "d")]
        packets = build_work_packets(passages, 720000, [])
        self.assertEqual([(p["start_ms"], p["end_ms"]) for p in packets], [(0, 360000), (360000, 720000)])
        self.assertEqual(packets[0]["passage_ids"], ["a", "b"])
        self.assertFalse(any(p["split_parent"] for p in packets))

    def test_candidates_move_fact_edges_out_of_overlapping_speech(self):
        speech = [{"start_ms": 1000, "end_ms": 3000}, {"start_ms": 2500, "end_ms": 4000}]
        facts = [{"start_ms": 1500, "end_ms": 3500}, {"start_ms": -100, "end_ms": 9000}]
        self.assertEqual(semantic_boundary_candidates(10000, facts, speech), [1000, 4000, 9000, 10000])
        self.assertEqual(semantic_boundary_candidates(0, [], []), [0])

    def test_overlong_passage_splits_without_bisecting_utterances(self):
        speech = [{"start_ms": 250000, "end_ms": 700000}]
        packets = build_work_packets([passage(0, 1000000, "long")], 1000000, speech)
        self.assertEqual([(p["start_ms"], p["end_ms"]) for p in packets],
                         [(0, 250000), (250000, 700000), (700000, 1000000)])
        self.assertTrue(all(p["split_parent"] for p in packets))
        self.assertTrue(all(p["passage_ids"] == ["long"] for p in packets))
        uninterrupted = build_work_packets([passage(0, 900000, "long")], 900000,
                                           [{"start_ms": 0, "end_ms": 900000}])
        self.assertEqual([(p["start_ms"], p["end_ms"]) for p in uninterrupted], [(0, 900000)])

    def test_tiny_adjacent_passages_pack_into_few_calls_and_cover_every_millisecond(self):
        passages = [passage(i, i + 10000, str(i)) for i in range(0, 1200000, 10000)]
        packets = build_work_packets(passages, 1200000, [])
        self.assertEqual(len(packets), 4)
        self.assertEqual(packets[0]["start_ms"], 0)
        self.assertEqual(packets[-1]["end_ms"], 1200000)
        self.assertTrue(all(a["end_ms"] == b["start_ms"] for a, b in zip(packets, packets[1:])))
        self.assertEqual(sum(p["end_ms"] - p["start_ms"] for p in packets), 1200000)

    def test_invalid_maps_and_parameters_fail_explicitly(self):
        invalid = [[], [passage(0, 9000, "a")], [passage(1, 10000, "a")],
                   [passage(0, 6000, "a"), passage(5000, 10000, "b")],
                   [passage(0, 5000, "a"), passage(5000, 10000, "a")],
                   [passage(0, 5000, "a"), passage(5001, 10000, "b")]]
        for passages in invalid:
            with self.subTest(passages=passages), self.assertRaises(SubtitlerError):
                build_work_packets(passages, 10000, [])
        with self.assertRaises(SubtitlerError):
            build_work_packets([passage(0, 5000, "a"), passage(5000, 10000, "b")], 10000,
                               [{"start_ms": 4900, "end_ms": 5100}])
        with self.assertRaises(SubtitlerError):
            build_work_packets([], 0, [], target_ms=0)
        self.assertEqual(build_work_packets([], 0, []), [])
