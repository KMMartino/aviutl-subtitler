import copy
import unittest

from subtitler.errors import SubtitlerError
from subtitler.semantic_utterances import build_semantic_utterances, semantic_cut_rejections, semantic_pause_candidates
from subtitler.transcript_document import TranscriptDocument
from subtitler.transcription_backend import BackendTranscriptResult, RawVadSpeechInterval, TranscriptSegment, TranscriptToken


def document(segments, vad=()):
    return TranscriptDocument("revision", "source.mkv", {}, 0, 1, 20, {},
        BackendTranscriptResult("test", duration_sec=20, segments=segments,
                                raw_vad_speech_intervals=[RawVadSpeechInterval(*row) for row in vad]))


def segment(index, text, tokens, start=0, end=20, fallback=False):
    return TranscriptSegment(index, text, start, end,
                             [TranscriptToken(word, left, right, kind="word") for word, left, right in tokens],
                             timing_kind="word", fallback_timing=fallback)


def unit(left, right, meaning="Complete thought", dependencies=()):
    return {"start_index": left, "end_index": right, "meaning": meaning, "dependency_starts": list(dependencies)}


class SemanticUtterancesTests(unittest.TestCase):
    def test_units_cross_chunk_boundaries_and_long_pauses_with_exact_source_text(self):
        doc = document([segment(0, "If this", [("If", 1, 1.2), ("this", 1.3, 1.5)], 1, 1.5),
                        segment(1, "works, leave.", [("works", 6, 6.5), ("leave", 7, 7.4)], 6, 7.4)])
        calls = []

        def inspect(**request):
            calls.append(request)
            return {"units": [unit(0, 3), unit(3, 4, dependencies=[0])]}

        result = build_semantic_utterances(doc, "source", inspect, {})
        self.assertEqual(len(calls), 1)
        first, second = result["units"]
        self.assertEqual(first["text"], "If this\nworks")
        self.assertEqual(second["text"], ", leave.")
        self.assertEqual((first["start_ms"], first["end_ms"]), (1000, 6500))
        self.assertEqual(second["dependency_unit_ids"], [first["unit_id"]])
        self.assertEqual([t for u in result["units"] for t in u["token_ids"]], [t["token_id"] for t in result["tokens"]])

    def test_batch_tail_is_reconsidered_instead_of_forcing_a_semantic_boundary(self):
        doc = document([segment(0, "a b c d e f", [(word, i, i + .5) for i, word in enumerate("abcdef")])])
        calls = []

        def inspect(**request):
            calls.append([t["index"] for t in request["evidence"]["tokens"]])
            if len(calls) == 1:
                return {"units": [unit(0, 1, dependencies=[1]), unit(1, 3)]}
            return {"units": [unit(1, 5), unit(5, 6, dependencies=[0])]}

        result = build_semantic_utterances(doc, "source", inspect, {}, batch_tokens=3)
        self.assertEqual(calls, [[0, 1, 2], [1, 2, 3, 4, 5]])
        self.assertEqual([(u["token_start_index"], u["token_end_index"]) for u in result["units"]], [(0, 1), (1, 5), (5, 6)])
        self.assertEqual(result["units"][0]["dependency_unit_ids"], [result["units"][1]["unit_id"]])

    def test_missing_alignment_or_uncovered_words_preserve_opaque_segment(self):
        for tokens in [[("Hello", None, None)], [("Hello", 1, 2)]]:
            with self.subTest(tokens=tokens):
                doc = document([segment(0, "Hello world", tokens, 1, 5)])
                result = build_semantic_utterances(doc, "source", lambda **_: {"units": [unit(0, 1)]}, {})
                self.assertEqual(result["tokens"][0]["alignment"], "opaque_segment")
                self.assertEqual(result["units"][0]["text"], "Hello world")
                self.assertEqual((result["units"][0]["start_ms"], result["units"][0]["end_ms"]), (1000, 5000))
                self.assertEqual(semantic_pause_candidates(result), [])
                self.assertEqual(result["alignment_summary"]["opaque_segments"], 1)
                self.assertEqual(result["alignment_limitations"][0]["start_ms"], 1000)

    def test_nonfinal_missing_suffix_is_represented_with_unfinished_tail(self):
        doc = document([segment(0, "a b c d e f", [(word, i, i + .5) for i, word in enumerate("abcdef")])])
        calls = []
        def inspect(**request):
            calls.append([t['index'] for t in request['evidence']['tokens']])
            return {'units': [unit(0, 1), unit(1, 2)] if len(calls) == 1 else [unit(1, 6)]}
        result = build_semantic_utterances(doc, 'source', inspect, {}, batch_tokens=3)
        self.assertEqual(calls, [[0, 1, 2], [1, 2, 3, 4, 5]])
        self.assertEqual(''.join(u['text'] for u in result['units']), 'a b c d e f')
        self.assertEqual([t for u in result['units'] for t in u['token_ids']], [t['token_id'] for t in result['tokens']])
        self.assertEqual(result['deferred_batch_tails'], [{'supplied_end_index': 3,
            'unassigned_start_index': 2, 'reconsider_from_index': 1}])

    def test_zero_duration_punctuation_retains_text_and_ids_without_guessing_word_times(self):
        doc = document([segment(0, "Hello, world.", [("Hello", 1, 2), (",", 2, 2),
                                                    ("world", 3, 4), (".", 4, 4)], 1, 4)])
        result = build_semantic_utterances(doc, "source", lambda **_: {"units": [unit(0, 2)]}, {})
        self.assertEqual(result["units"][0]["text"], "Hello, world.")
        self.assertEqual([(t["start_ms"], t["end_ms"]) for t in result["tokens"]], [(1000, 2000), (3000, 4000)])
        self.assertEqual([identifier for t in result["tokens"] for identifier in t["original_token_ids"]],
                         [f"source-segment-0-token-{i}" for i in range(4)])
        lexical = document([segment(0, "Hello world", [("Hello", 1, 2), ("world", 3, 3)], 1, 4)])
        preserved = build_semantic_utterances(lexical, "source", lambda **_: {"units": [unit(0, 1)]}, {})
        self.assertEqual(preserved["tokens"][0]["alignment"], "opaque_span")
        self.assertEqual((preserved["tokens"][0]["start_ms"], preserved["tokens"][0]["end_ms"]), (1000, 4000))

    def test_local_lexical_uncertainty_preserves_reliable_parts_and_exact_provenance(self):
        doc = document([segment(0, "a b c d e f", [("a", 1, 2), ("b", 3, 4), ("c", 4, 4),
                                                  ("d", 5, 6), ("e", 12, 13), ("f", 18, 19)])],
                       [(1, 2), (3, 6), (12, 13), (18, 19)])
        result = build_semantic_utterances(doc, "source", lambda **_: {"units": [
            unit(0, 1), unit(1, 2), unit(2, 4)]}, {})
        atoms = result["tokens"]
        self.assertEqual([t["alignment"] for t in atoms], ["word", "opaque_span", "word", "word"])
        self.assertEqual("".join(t["text"] for t in atoms), "a b c d e f")
        self.assertEqual([(t["start_ms"], t["end_ms"]) for t in atoms],
                         [(1000, 2000), (3000, 6000), (12000, 13000), (18000, 19000)])
        self.assertEqual([ref for t in atoms for ref in t["original_token_ids"]],
                         [f"source-segment-0-token-{i}" for i in range(6)])
        self.assertEqual(atoms[1]["uncertain_tokens"][0]["start_ms"], 4000)
        self.assertEqual(len(atoms[1]["alignment_anchors"]), 2)
        self.assertEqual(result["alignment_summary"]["opaque_spans"], 1)
        self.assertEqual(result["alignment_summary"]["opaque_segments"], 0)
        self.assertEqual(result["units"][1]["alignment"], "opaque_span")
        self.assertEqual(len(semantic_pause_candidates(result)), 1)
        self.assertIn("bad", semantic_cut_rejections([{"cut_id": "bad", "start_ms": 4300, "end_ms": 4700}], result))

    def test_uncertainty_groups_merge_and_use_only_their_own_segment_edges(self):
        for times, expected in [([(1, 1), (2, 3), (4, 5)], [(500, 3000), (4000, 5000)]),
                                ([(1, 2), (3, 4), (5, 5)], [(1000, 2000), (3000, 6000)]),
                                ([(1, 2), (2, 2), (3, 4), (4, 4), (5, 6)], [(1000, 6000)]),
                                ([(1, 1), (2, 2), (3, 3)], [(500, 6000)])]:
            with self.subTest(times=times):
                words = list("abcde"[:len(times)])
                doc = document([segment(0, " ".join(words), [(w, *t) for w, t in zip(words, times)], .5, 6),
                                segment(1, "next", [("next", 8, 9)], 8, 9)])
                result = build_semantic_utterances(doc, "source", lambda **request: {"units": [
                    unit(t["index"], t["index"] + 1) for t in request["evidence"]["tokens"]]}, {})
                atoms = result["tokens"]
                self.assertEqual([(t["start_ms"], t["end_ms"]) for t in atoms[:-1]], expected)
                self.assertEqual(atoms[-1]["alignment"], "word")
                self.assertEqual("".join(t["text"] for t in atoms), " ".join(words) + "\nnext")

    def test_incomplete_coverage_and_invalid_dependency_fail(self):
        doc = document([segment(0, "a b", [("a", 1, 2), ("b", 3, 4)])])
        for rows in [[unit(0, 1)], [unit(0, 2, dependencies=[1])], [unit(0, 1), unit(0, 2)]]:
            with self.subTest(rows=rows), self.assertRaises(SubtitlerError):
                build_semantic_utterances(doc, "source", lambda **_: {"units": rows}, {})
        normalized = build_semantic_utterances(doc, "source", lambda **_: {
            "units": [unit(0, 1), unit(1, 2, dependencies=[1, 0, 0])]}, {})
        self.assertEqual(normalized['units'][1]['dependency_unit_ids'], [normalized['units'][0]['unit_id']])

    def test_invalid_partition_is_repaired_from_preserved_source_not_filled_in(self):
        doc = document([segment(0, 'a b c', [('a', 1, 2), ('b', 3, 4), ('c', 5, 6)])])
        calls = []
        def inspect(**request):
            calls.append(request)
            if len(calls) == 1:
                return {'units': [unit(0, 1), unit(2, 3)]}
            feedback = request['evidence']['partition_repair']
            self.assertIn('expected start_index=1', feedback['validation_error'])
            self.assertEqual(feedback['previous_response']['units'][1]['start_index'], 2)
            return {'units': [unit(0, 3)]}
        result = build_semantic_utterances(doc, 'source', inspect, {})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['units'][0]['text'], 'a b c')
        self.assertEqual(len(result['protocol_repairs']), 1)

    def test_cut_guard_allows_whole_units_or_verified_internal_pause_only(self):
        doc = document([segment(0, "Wait then go", [("Wait", 1, 2), ("then", 6, 7), ("go", 8, 9)])],
                       [(1, 2), (6, 7), (8, 9)])
        result = build_semantic_utterances(doc, "source", lambda **_: {"units": [unit(0, 3)]}, {})
        gap = semantic_pause_candidates(result)[0]
        self.assertEqual((gap["start_ms"], gap["end_ms"]), (2300, 5800))
        for left, right in [(2300, 5800), (800, 9300), (10000, 11000)]:
            self.assertEqual(semantic_cut_rejections([{"cut_id": "c", "start_ms": left, "end_ms": right}], result), {})
        for left, right in [(1000, 2000), (1000, 9000), (2000, 6000)]:
            self.assertIn("c", semantic_cut_rejections([{"cut_id": "c", "start_ms": left, "end_ms": right}], result))
        active = copy.deepcopy(result)
        active["voice_activity"].append({"start_ms": 3000, "end_ms": 3200})
        self.assertEqual(semantic_pause_candidates(active), [])

    def test_retained_dependencies_survive_and_rejections_propagate(self):
        doc = document([segment(0, "a b c", [("a", 1, 2), ("b", 4, 5), ("c", 7, 8)])])
        result = build_semantic_utterances(doc, "source", lambda **_: {"units": [
            unit(0, 1), unit(1, 2, dependencies=[0]), unit(2, 3, dependencies=[1])]}, {})
        cuts = [{"cut_id": "a", "start_ms": 800, "end_ms": 2300},
                {"cut_id": "b", "start_ms": 3800, "end_ms": 5300}]
        self.assertEqual(set(semantic_cut_rejections(cuts, result)), {"a", "b"})
        self.assertEqual(semantic_cut_rejections([{"cut_id": "all", "start_ms": 800, "end_ms": 8300}], result), {})
