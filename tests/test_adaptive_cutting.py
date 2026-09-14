import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from subtitler.adaptive_cutting import canonical_visual_facts, collected_interval, compile_cuts, edit_boundary_choices, evidence_context, join_frame_times, retained_sequence, run_adaptive_cutting, selection_outcomes, speech_facts, strategy_schema, transcript_rows, unexplained_voice
from subtitler.hosted_inspection import _validate
from subtitler.errors import SubtitlerError
from subtitler.editorial_guidance import project_brief
from subtitler.api_usage import ApiUsageLedger
from subtitler.transcript_document import create_transcript_document
from subtitler.transcription_backend import BackendTranscriptResult, RawVadSpeechInterval, SpeechRegion, TranscriptSegment, TranscriptToken


def cut(start, end, *, evidence=("fact",), retained=()):
    return {"start_ms": start, "end_ms": end, "reason": "Repeated complete instruction",
            "evidence_ids": list(evidence), "requires_retained_ids": list(retained)}


class AdaptiveCuttingTests(unittest.TestCase):
    def test_structured_compilation_removes_touching_independent_meaning_but_keeps_dependencies(self):
        units = [{'unit_id': str(i), 'start_ms': i * 1000, 'end_ms': (i + 1) * 1000,
                  'dependency_unit_ids': []} for i in range(3)]
        artifact = {'units': units, 'duration_ms': 3000, 'raw_vad_available': False}
        facts = [{'id': 'fact', 'start_ms': 0, 'end_ms': 3000}]
        self.assertFalse(compile_cuts([cut(1000, 2000)], facts, units, [], 3000)[0])
        accepted, rejected = compile_cuts([cut(1000, 2000)], facts, units, [], 3000,
                                          semantic_artifact=artifact)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        units[2]['dependency_unit_ids'] = ['1']
        accepted, rejected = compile_cuts([cut(1000, 2000)], facts, units, [], 3000,
                                          semantic_artifact=artifact)
        self.assertFalse(accepted)
        self.assertIn('dependency', rejected[0]['rejection'])

    def test_project_intent_is_isolated_and_dense_observations_survive_compaction(self):
        first = {"objective": "A quiet first look", "must_keep_notes": ["Long exploration"]}
        second = {"objective": "An instructional comparison"}
        brief = project_brief(first)
        brief["must_keep_notes"].append("Mutated copy")
        self.assertEqual(first["must_keep_notes"], ["Long exploration"])
        self.assertEqual(project_brief(second)["objective"], second["objective"])
        self.assertIsNone(project_brief(second)["target_duration_max_ms"])
        source = {"source_id": "source", "duration_ms": 10000, "stages": {"visual_learning": {"output": {
            "model": "test", "segments": [{"start_ms": 2000, "end_ms": 2200,
            "description": "A brief readable result screen", "observed_label": "Result"}]}}}}
        facts = canonical_visual_facts(source)
        self.assertEqual(facts[0]["observation"], "A brief readable result screen")
        self.assertEqual(facts[0]["end_ms"], 2200)
        self.assertEqual(facts[0]["provenance"]["segment_index"], 0)
        point = {"start_ms": 390000, "end_ms": 390000, "frame_times_ms": [390000], "observation": "A readable save entry"}
        normalized = collected_interval(point, 300000, 600000, [390000])
        self.assertEqual(normalized["end_ms"], 390001)
        self.assertEqual(normalized["temporal_scope"], "sampled_instant")
        self.assertEqual(point["end_ms"], 390000)
        with self.assertRaisesRegex(SubtitlerError, "supported frame instant"):
            collected_interval({**point, "frame_times_ms": []}, 300000, 600000, [390000])


    def test_compilation_preserves_utterances_and_unresolved_voice(self):
        facts = [{"id": "fact", "start_ms": 0, "end_ms": 10000}]
        speech = [{"start_ms": 1000, "end_ms": 3000, "text": "A complete thought"}]
        voice = [{"start_ms": 5000, "end_ms": 6000}]
        for proposal, rejection in [
            (cut(1500, 3500), "splits an aligned utterance"),
            (cut(0, 2000), "splits an aligned utterance"),
            (cut(3000, 4000), "retained speech handles"),
            (cut(4000, 5500), "Unexplained vocal activity"),
        ]:
            with self.subTest(proposal=proposal):
                accepted, rejected = compile_cuts([proposal], facts, speech, voice, 10000)
                self.assertEqual(accepted, [])
                self.assertIn(rejection, rejected[0]["rejection"])
        accepted, rejected = compile_cuts([cut(1000, 3000), cut(3100, 5000)], facts, speech, voice, 10000)
        self.assertEqual([(c["start_ms"], c["end_ms"]) for c in accepted], [(1000, 3000)])
        self.assertIn("retained fragment", rejected[0]["rejection"])
        for proposals in ([cut(50, 9000)], [cut(1000, 9900)], [cut(1000, 2000), cut(2200, 4000)]):
            accepted, rejected = compile_cuts(proposals, facts, [], [], 10000)
            self.assertTrue(rejected)
            self.assertTrue(all(span["source_end_ms"] - span["source_start_ms"] >= 500
                                for span in retained_sequence("source", 10000, accepted, facts)))

    def test_boundary_catalog_offers_padding_without_changing_editorial_intent(self):
        speech = [{"start_ms": 600000, "end_ms": 624424}, {"start_ms": 694936, "end_ms": 709000}]
        facts = [{"id": "frozen", "start_ms": 606000, "end_ms": 714000}]
        choices = edit_boundary_choices((600000, 900000), facts, speech)
        self.assertNotIn(624424, choices["start_ms"])
        self.assertIn(624524, choices["start_ms"])
        self.assertIn(694886, choices["end_ms"])
        self.assertNotIn(606000, choices["start_ms"])
        accepted, rejected = compile_cuts([cut(624524, 694886, evidence=("frozen",))], facts, speech, [], 900000)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_assembly_keeps_source_and_project_time_with_retrievable_evidence(self):
        facts = [{"id": "discovery", "start_ms": 2500, "end_ms": 3000},
                 {"id": "removed", "start_ms": 4200, "end_ms": 4500}]
        rows = retained_sequence("second-source", 10000,
            [cut(0, 2000), cut(4000, 6000), cut(8000, 10000)], facts, 12000)
        self.assertEqual([(r["source_start_ms"], r["source_end_ms"], r["timeline_start_ms"], r["timeline_end_ms"])
                          for r in rows], [(2000, 4000, 12000, 14000), (6000, 8000, 14000, 16000)])
        self.assertEqual(rows[0]["evidence_ids"], ["discovery"])
        self.assertEqual(rows[1]["evidence_ids"], [])
        self.assertEqual(retained_sequence("empty", 10000, [cut(0, 10000)], facts), [])
        close_cuts = [cut(1000, 3000), cut(3500, 5000)]
        close_assembly = retained_sequence("source", 10000, close_cuts, [])
        # Both cuts share the actual retained 500ms bridge, rather than sampling inside each other.
        self.assertEqual(join_frame_times(close_cuts, close_assembly, []), [500, 2000, 3250, 4250, 6500])

    def test_local_evidence_preserves_explicit_distant_context_and_reports_unrealized_selections(self):
        facts = [{"id": "local", "start_ms": 1000, "end_ms": 2000},
                 {"id": "payoff", "start_ms": 90000, "end_ms": 95000},
                 {"id": "unrelated", "start_ms": 70000, "end_ms": 71000}]
        selected = evidence_context(facts, [{"start_ms": 0, "end_ms": 10000}], {"payoff"})
        self.assertEqual([f["id"] for f in selected], ["local", "payoff"])
        selections = [{"passage_id": "scene", "start_ms": 0, "end_ms": 10000, "treatment": "condense"}]
        decisions = [{"selection_decisions": [{"passage_id": "scene", "decision": "follow",
            "reason": "Keep the payoff", "retained_beats": [{"start_ms": 7000, "end_ms": 8000, "reason": "Payoff"}]}]}]
        self.assertTrue(selection_outcomes(selections, decisions, [])[0]["unrealized_compression"])
        result = selection_outcomes(selections, decisions, [cut(1000, 3000)])[0]
        self.assertFalse(result["unrealized_compression"])
        self.assertEqual(result["removed_ms"], 2000)
        self.assertEqual(result["removed_retained_beats"], [])
        self.assertEqual(len(selection_outcomes(selections, decisions, [cut(7000, 8000)])[0]["removed_retained_beats"]), 1)

    def test_compilation_requires_relevant_existing_evidence(self):
        facts = [{"id": "fact", "start_ms": 0, "end_ms": 2000}]
        for proposal, rejection in [
            (cut(0, 1000, evidence=()), "No concrete reason or evidence"),
            (cut(0, 1000, evidence=("missing",)), "Unknown evidence reference"),
            (cut(0, 1000, retained=("missing",)), "Unknown evidence reference"),
            (cut(3000, 4000), "Evidence does not cover"),
            (cut(1500, 9000), "Evidence does not cover"),
            (cut(0, 499), "sub-half-second"),
            (cut(9000, 11000), "Invalid"),
        ]:
            with self.subTest(proposal=proposal):
                accepted, rejected = compile_cuts([proposal], facts, [], [], 10000)
                self.assertEqual(accepted, [])
                self.assertIn(rejection, rejected[0]["rejection"])

    def test_conflicting_retained_dependencies_are_rejected_in_either_order(self):
        facts = [{"id": "first", "start_ms": 0, "end_ms": 1000},
                 {"id": "second", "start_ms": 2000, "end_ms": 3000}]
        first = cut(0, 1000, evidence=("first",), retained=("second",))
        second = cut(2000, 3000, evidence=("second",), retained=("first",))
        for proposals in ([first, second], [second, first]):
            with self.subTest(proposals=proposals):
                accepted, rejected = compile_cuts(proposals, facts, [], [], 4000)
                self.assertEqual(accepted, [])
                self.assertEqual(len(rejected), 2)
                self.assertTrue(all("Required retained evidence" in c["rejection"] for c in rejected))
        accepted, rejected = compile_cuts([first], facts, [], [], 4000)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_aligned_speech_does_not_hide_untranscribed_vocal_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            document = create_transcript_document(
                source_path=source, audio_track=0, duration_sec=10, settings={},
                backend=BackendTranscriptResult("test", duration_sec=10,
                    segments=[TranscriptSegment(0, "Hello", 1, 2, tokens=[TranscriptToken("Hello", 1, 2)])],
                    raw_vad_speech_intervals=[RawVadSpeechInterval(1, 2), RawVadSpeechInterval(4, 6)]),
            )
            self.assertEqual(transcript_rows(document), [{"start_ms": 1000, "end_ms": 2000, "text": "Hello"}])
            facts = speech_facts("source", transcript_rows(document))
            self.assertEqual(facts[0]["text"], "Hello")
            self.assertEqual(facts[0]["evidence_kind"], "aligned_speech")
            self.assertEqual(retained_sequence("source", 10000, [], facts)[0]["evidence_ids"], [facts[0]["id"]])
            accepted, rejected = compile_cuts([cut(1000, 2000, evidence=(facts[0]["id"],))],
                facts, transcript_rows(document), [], 10000)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(rejected, [])
            schema = strategy_schema(facts, 10000)
            reference = schema["properties"]["passage_groups"]["items"]["properties"]["passages"]["items"]["properties"]["evidence_ids"]
            self.assertTrue(_validate([facts[0]["id"]], reference))
            self.assertFalse(_validate(["source-speech-1000"], reference))
            self.assertEqual(unexplained_voice(document), [{"start_ms": 4000, "end_ms": 6000}])
            fallback = replace(document, backend=replace(document.backend, raw_vad_speech_intervals=[],
                speech_regions=[SpeechRegion(0, 1, 2), SpeechRegion(1, 4, 6, selected_for_transcription=False)]))
            self.assertEqual(unexplained_voice(fallback), [{"start_ms": 4000, "end_ms": 6000}])

    def test_resume_reuses_paid_operations_and_keeps_baseline_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"immutable media")
            document = create_transcript_document(source_path=source, audio_track=0, duration_sec=10,
                settings={}, backend=BackendTranscriptResult("test", duration_sec=10,
                    segments=[TranscriptSegment(0, "The important explanation", 7, 8,
                        tokens=[TranscriptToken("The important explanation", 7, 8)])]))
            project = {"project_id": "project", "sources": [{"source_id": "source", "order": 0,
                       "duration_ms": 10000, "visual_path": str(source), "stages": {"visual_learning": {"output": {
                           "segments": [{"start_ms": 2000, "end_ms": 2200, "description": "A short transition"},
                                        {"start_ms": 0, "end_ms": 1000, "description": "Orientation"},
                                        {"start_ms": 1000, "end_ms": 2000, "description": "Waiting"},
                                        {"start_ms": 2200, "end_ms": 3000, "description": "Waiting continues"},
                                        {"start_ms": 3000, "end_ms": 10000, "description": "Explanation"}]}}}}]}
            baseline = {"confirmed_cuts": [{"cut_id": "baseline", "start_ms": 7000, "end_ms": 9000}],
                        "removed_ms": 2000, "metadata": {"keep": [1, 2]},
                        "final_actions": [{"action_id": "draft", "source_id": "source", "start_ms": 0,
                            "end_ms": 4000, "instruction": "Explain if needed", "narration_guidance": {}}]}
            original = copy.deepcopy(baseline)
            usage = ApiUsageLedger()
            def inspect(**request):
                operation = request["operation"]
                usage.add(provider="test", model="fake", operation=operation, cost_usd=0.01)
                evidence = json.loads(request["prompt"].rsplit("\n", 1)[-1])
                if operation == "collect_passage":
                    self.assertEqual(evidence['speech'], [{'start_ms': 7000, 'end_ms': 8000, 'text': 'The important explanation'}])
                    outside = {'summary': '', 'facts': [{'start_ms': 0, 'end_ms': 11000,
                        'observation': '', 'change': '', 'uncertainty': '', 'frame_times_ms': []}]}
                    self.assertFalse(_validate(outside, request['schema']))
                    return {"summary": "Waiting then progress", "facts": [{"start_ms": 0, "end_ms": 10000,
                            "observation": "Waiting then progress", "change": "Progress", "uncertainty": "", "frame_times_ms": []},
                            {"start_ms": 1000, "end_ms": 3000, "observation": "Waiting", "change": "", "uncertainty": "", "frame_times_ms": []}]}
                if operation == "semantic_utterances":
                    self.assertEqual(evidence["tokens"][0]["text"], "The important explanation")
                    response = {"units": [{"start_index": 0, "end_index": 1,
                                          "meaning": "Explains the discovery", "dependency_starts": []}]}
                    self.assertTrue(_validate(response, request["schema"]))
                    return response
                if operation == "activity_direction":
                    self.assertEqual(evidence["utterance_meanings"][0]["unit_id"], "source-utterance-00000")
                    return {"direction": "A quiet first look", "activities": [{
                        "activity_id": activity["activity_id"], "treatment": "preserve",
                        "contribution": "Retain the explanation and orientation", "omission_cost": "Loses context",
                        "edit_instruction": "Remove only interchangeable waiting"}
                        for activity in evidence["activity_structure"]["activities"]]}
                if operation == "select_states":
                    response = {"summary": "Remove waiting states", "question": "", "states": [{
                        "state_id": state["state_id"],
                        "decision": "omit" if 1000 <= state["start_ms"] < state["end_ms"] <= 3000 else "keep",
                        "contribution": "Waiting adds no distinct experience", "transition_reason": "Continuation remains clear"}
                        for state in evidence["assigned_states"]],
                        "utterances": [{"unit_id": unit["unit_id"], "decision": "keep", "reason": "Important explanation",
                                        "supporting_state_ids": []} for unit in evidence["utterances"]], "trim_pause_ids": []}
                    self.assertTrue(_validate(response, request["schema"]))
                    self.assertNotIn("cuts", request["schema"]["properties"])
                    return response
                if operation == "review_presentation":
                    return {"decisions": [{"action_id": "draft", "keep": False,
                        "reason": "Retained source audio explains this", "editor_instruction": "",
                        "narrator_direction": "", "related_cut_ids": [],
                        "placement_start_ms": 0, "placement_end_ms": 0}]}
                if operation == "review_joins":
                    self.assertIn("500.jpg", [path.name for path in request["images"]])
                    self.assertNotIn("2000.jpg", [path.name for path in request["images"]])
                    self.assertIn("2099.jpg", [path.name for path in request["images"]])
                    return {"reject_cut_ids": [], "reason": "The continuation is clear"}
                self.fail(f"Unexpected operation: {operation}")

            def frames(media, stamps, folder):
                self.assertEqual(media, source)
                folder.mkdir(parents=True, exist_ok=True)
                paths = [folder / f"{stamp}.jpg" for stamp in stamps]
                for path in paths:
                    if not path.exists():
                        path.write_bytes(b"stable fake frame")
                return paths

            provider = Mock()
            provider.inspect.side_effect = inspect
            kwargs = dict(project=project, documents={"source": document}, baseline=baseline,
                          workspace=root, settings={}, frame_extractor=frames, artifact_workspace=root / "paid",
                          motion_analyzer=lambda images, stamps: [{"start_ms": 0, "end_ms": 10000,
                              "observation": "sampled_frame_unchanged"}])
            result = run_adaptive_cutting(**kwargs, usage=usage, provider=provider)
            self.assertEqual(provider.inspect.call_count, 6)
            self.assertEqual(result["final_actions"], [])
            self.assertEqual(baseline, original)
            self.assertEqual(result["baseline_confirmed_cuts"], original["confirmed_cuts"])
            self.assertEqual([(c["start_ms"], c["end_ms"]) for c in result["confirmed_cuts"]], [(1000, 3000)])
            self.assertEqual(result["removed_ms"], 2000)
            self.assertEqual(result["estimated_final_ms"], 8000)
            self.assertTrue(Path(result["adaptive_report_path"]).is_file())
            resumed_usage = ApiUsageLedger()
            forbidden_provider = Mock()
            forbidden_provider.inspect.side_effect = AssertionError("Repeated paid request")
            resumed = run_adaptive_cutting(**kwargs, usage=resumed_usage, provider=forbidden_provider)
            forbidden_provider.inspect.assert_not_called()
            self.assertEqual(resumed, result)
            self.assertAlmostEqual(resumed_usage.total_cost_usd, 0.06)
            self.assertEqual(len(resumed_usage.rows), 6)
            recomposed = run_adaptive_cutting(**{**kwargs, "workspace": root / "recomposed"},
                usage=ApiUsageLedger(), provider=forbidden_provider)
            forbidden_provider.inspect.assert_not_called()
            self.assertEqual(recomposed["confirmed_cuts"], result["confirmed_cuts"])
            self.assertEqual(source.read_bytes(), b"immutable media")
            report = json.loads(Path(result["adaptive_artifact_path"]).read_text(encoding="utf-8"))
            self.assertEqual(report["baseline_cuts"], original["confirmed_cuts"])
            self.assertEqual(report["confirmed_cuts"], result["confirmed_cuts"])
            focused = report["sources"][0]["focused_evidence_packets"][0]
            self.assertEqual(focused["role"], "join")
            self.assertEqual(focused["frames"][0]["timestamp_ms"], 2099)
            self.assertTrue(all(Path(path).is_file() for path in focused["frame_paths"]))
            measured = report["sources"][0]["facts"][-1]
            self.assertEqual(measured["evidence_kind"], "sampled_motion")
            self.assertEqual((measured["start_ms"], measured["end_ms"]), (0, 10000))

            def uncertain_inspection(**request):
                response = inspect(**request)
                if request["operation"] == "select_states":
                    response["question"] = "Does this quiet scene establish a discovery?"
                return response

            uncertain_provider = Mock()
            uncertain_provider.inspect.side_effect = uncertain_inspection
            uncertain = run_adaptive_cutting(**{**kwargs, "workspace": root / "uncertain", "artifact_workspace": root / "uncertain-paid"},
                usage=ApiUsageLedger(), provider=uncertain_provider)
            self.assertEqual(uncertain["confirmed_cuts"], [])
            self.assertEqual(uncertain["removed_ms"], 0)
            self.assertEqual(uncertain["estimated_final_ms"], 10000)
            self.assertEqual([call.kwargs["operation"] for call in uncertain_provider.inspect.call_args_list],
                             ["semantic_utterances", "collect_passage", "activity_direction", "select_states", "review_presentation"])
            report = json.loads(Path(uncertain["adaptive_artifact_path"]).read_text(encoding="utf-8"))
            self.assertEqual(report["sources"][0]["decisions"][0]["status"], "unresolved_preserved")

            project["editorial_map"] = {"narration_briefs": [{"id": "brief", "start_ms": 0, "end_ms": 4000}]}
            baseline["final_actions"][0]["narration_brief_ids"] = ["brief"]

            def placed_inspection(**request):
                if request["operation"] == "review_presentation":
                    return {"decisions": [{"action_id": "draft", "keep": True, "reason": "Adds the premise",
                        "editor_instruction": "Place over the retained opening", "narrator_direction": "Here is the premise.",
                        "related_cut_ids": [], "placement_start_ms": 100, "placement_end_ms": 900}]}
                return inspect(**request)

            provider.inspect.side_effect = placed_inspection
            placed = run_adaptive_cutting(**{**kwargs, "workspace": root / "placed", "artifact_workspace": root / "placed-paid"},
                usage=ApiUsageLedger(), provider=provider)
            for row in [placed["final_actions"][0], placed["narration_briefs"][0]]:
                self.assertEqual((row["start_ms"], row["end_ms"]), (100, 900))

            def crossing_inspection(**request):
                response = placed_inspection(**request)
                if request["operation"] == "review_presentation":
                    response["decisions"][0]["placement_end_ms"] = 4000
                return response

            provider.inspect.side_effect = crossing_inspection
            with self.assertRaisesRegex(SubtitlerError, "within one retained source range"):
                run_adaptive_cutting(**{**kwargs, "workspace": root / "crossing", "artifact_workspace": root / "crossing-paid"},
                    usage=ApiUsageLedger(), provider=provider)
