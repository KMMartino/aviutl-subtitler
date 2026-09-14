import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.acoustic_edges import refine_gap
from subtitler.api_usage import ApiUsageLedger
from subtitler.editorial_cutting import build_human_information_plan
from subtitler.editorial_recommendations import generate_recommendations, recommendation_budget
from subtitler.editorial_recommendation_view import recommendation_html, marker_labels, write_recommendation_frames
from subtitler.errors import SubtitlerError
from subtitler.speech_gaps import SpeechGap


class RecommendationWorkflowTests(unittest.TestCase):
    def test_acoustic_edges_preserve_tails_and_breaths_without_entering_speech(self):
        energy = {'frame_ms': 10, 'rms_dbfs': [-20.] * 100 + [-35.] * 20 + [-70.] * 250 + [-35.] * 30 + [-20.] * 100}
        gap = SpeechGap(1, 4, 1.1, 3.95)
        start, end, evidence = refine_gap(gap, energy, duration=5)
        self.assertAlmostEqual(start, 1.22)
        self.assertAlmostEqual(end, 3.68)
        self.assertEqual(evidence['start_basis'], 'settled_noise_floor')
        self.assertGreaterEqual(start, gap.silence_start)
        self.assertLessEqual(end, gap.silence_end)
        # A clean gate permits smaller padding; steady noise cannot justify it.
        clean = {'frame_ms': 10, 'rms_dbfs': [-20.] * 100 + [-70.] * 300 + [-20.] * 100}
        self.assertEqual(tuple(round(t, 2) for t in refine_gap(gap, clean, duration=5)[:2]), (1.02, 3.98))
        noisy = {'frame_ms': 10, 'rms_dbfs': [-20.] * 500}
        self.assertEqual(refine_gap(gap, noisy, duration=5)[:2], (1.1, 3.95))
        self.assertEqual(refine_gap(SpeechGap(0, 5, 0, 5), noisy, duration=5)[:2], (0, 5))

    def test_minimum_cut_is_checked_after_acoustic_refinement(self):
        source = {'source_id': 's', 'duration_ms': 5000, 'result': {}}
        energy = {'frame_ms': 10, 'rms_dbfs': [-20.] * 100 + [-35.] * 20 + [-70.] * 250 + [-35.] * 30 + [-20.] * 100}
        plan = build_human_information_plan(project={'sources': [source]}, synthesis={},
            speech_activity={'s': [(0, 1000), (4000, 5000)]},
            settings={'gap_edge_mode': 'acoustic', 'voice_gap_min_ms': 2500}, voice_energy={'s': energy})
        self.assertEqual(plan['confirmed_cuts'], [])

    def test_recommendations_are_cached_referenced_and_cannot_change_cuts(self):
        source = {'source_id': 's', 'duration_ms': 6000, 'result': {
            'speech_segments': [{'start_ms': 1000, 'end_ms': 2000}],
            'utterance_groups': [{'start_ms': 1000, 'end_ms': 2000, 'text': '<exact speech>'}]},
            'stages': {'semantic_spans': {'output': {
                'activity_episodes': [{'episode_id': 'a1', 'level': 1, 'start_ms': 0, 'end_ms': 6000, 'label': 'Attempt', 'summary': 'Retry'}],
                'event_graph': {'nodes': [{'event_id': 'e1', 'start_ms': 0, 'end_ms': 6000, 'observed_label': 'Combat'}]}}}}}
        project = {'sources': [source], 'title_or_game': 'Game', 'objective': 'Casual play'}
        provider = Mock()
        def respond(**kwargs):
            self.assertIn('<exact speech>', kwargs['prompt'])
            ids = kwargs['schema']['properties']['assessments']['items']['properties']['target_id']['enum']
            return {'assessments': [{'target_id': target, 'suggested_treatment': 'possible_omission',
                'observed_content': 'Combat', 'potential_contribution': 'Learning', 'reason_and_tradeoff': 'Compare the next attempt',
                'evidence_limitation': '', 'evidence_ids': ['s-state-0001', 's-speech-0'], 'related_ids': ['s-state-0001']} for target in ids]}
        provider.inspect.side_effect = respond
        baseline = build_human_information_plan(project=project, synthesis={}, speech_activity={'s': []})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = generate_recommendations(project, root, {}, ApiUsageLedger(), provider)
            self.assertEqual(result, generate_recommendations(project, root, {}, ApiUsageLedger(), provider))
            provider.inspect.assert_called_once()
            self.assertEqual([r['target_id'] for r in result['assessments']], ['a1'])
            self.assertFalse(result['executable'])
            self.assertNotIn('confirmed_cuts', result)
            self.assertIn('&lt;exact speech&gt;', recommendation_html(result))
            rendered = recommendation_html(result)
            self.assertNotIn('<details', rendered)
            self.assertNotIn('segment state', rendered)
            self.assertIn('A01.S01', rendered)
            self.assertNotIn('s-state-0001', rendered)
            self.assertNotIn('s-speech-0', rendered)
            self.assertNotIn('00:01', rendered)
            self.assertNotIn('href="#', rendered)
            self.assertIn('Remove — Compare the next attempt', rendered)
            self.assertEqual(marker_labels(result), {'a1': 'A01', 's-state-0001': 'A01.S01'})
            self.assertTrue((root / 'editor-recommendations.json').is_file())
            self.assertEqual(json.loads((root / 'editor-recommendations.json').read_text(encoding='utf8')), result)
            provider.inspect.side_effect = lambda **kwargs: {'assessments': []}
            with self.assertRaises(SubtitlerError):
                generate_recommendations({**project, 'objective': 'Changed'}, root, {}, ApiUsageLedger(), provider)
        self.assertEqual(baseline, build_human_information_plan(project=project, synthesis={}, speech_activity={'s': []}))

    def test_segment_images_reuse_only_matching_frames_and_fill_missing_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / 'source.mp4'
            media.write_bytes(b'media')
            saved = root / 'analysis.jpg'
            saved.write_bytes(b'saved image')
            fingerprint = {'digest': 'source-a'}
            source = {'source_id': 's', 'visual_path': str(media), 'visual_fingerprint': fingerprint,
                'reference_frames': [{'timestamp_ms': 500, 'path': str(saved), 'visual_fingerprint': fingerprint},
                                     {'timestamp_ms': 1500, 'path': str(saved), 'visual_fingerprint': {'digest': 'wrong'}}]}
            catalog = {'source_id': 's', 'activities': [{'activity_id': 'a', 'start_ms': 0, 'end_ms': 2000}],
                'states': [{'state_id': 's1', 'start_ms': 0, 'end_ms': 1000},
                           {'state_id': 's2', 'start_ms': 1000, 'end_ms': 2000}]}
            project = {'sources': [source], 'editorial_map': {'editor_recommendations': {'catalog': [catalog]},
                'narration_briefs': [{'first_state_id': 's1'}, {'first_state_id': 's2'}]}}
            def extract(command, **kwargs):
                Path(command[-1]).write_bytes(b'new frame')
                return Mock(returncode=0)
            with patch('subtitler.editorial_recommendation_view.subprocess.run', side_effect=extract) as ffmpeg:
                images = write_recommendation_frames(root / 'report.html', project)
                self.assertEqual(set(images), {'a', 's1', 's2'})
                self.assertEqual((root / images['s1']).read_bytes(), b'saved image')
                self.assertEqual((root / images['s2']).read_bytes(), b'new frame')
                ffmpeg.assert_called_once()
                self.assertEqual(images, write_recommendation_frames(root / 'report.html', project))
                ffmpeg.assert_called_once()

    def test_twenty_hour_multifile_assessments_are_bounded_and_resume_completed_batches(self):
        catalog = []
        sources = []
        for source_index in range(2):
            sid = f'source-{source_index}'
            sources.append({'source_id': sid, 'duration_ms': 36_000_000})
            activities, states, speech = [], [], []
            for index in range(40):
                aid = f'{sid}-a{index}'
                start = index * 900_000
                children = 72 if index == 0 else 18
                step = 900_000 // children
                activities.append({'activity_id': aid, 'start_ms': start, 'end_ms': start + 900_000,
                    'label': f'Search {index}', 'summary': 'Search for supplies', 'state_ids': []})
                for child in range(children):
                    state = {'state_id': f'{aid}-s{child}', 'activity_id': aid, 'start_ms': start + child * step,
                             'end_ms': start + (child + 1) * step, 'observations': ['Check a room']}
                    states.append(state)
                    speech.extend({'evidence_id': f'{state["state_id"]}-speech{j}',
                        'start_ms': state['start_ms'] + j * 1000, 'end_ms': state['start_ms'] + (j + 1) * 1000,
                        'text': str(child) + 'x' * 700} for j in range(2))
            catalog.append({'source_id': sid, 'name': sid, 'activities': activities, 'states': states, 'speech': speech})
        project = {'sources': sources, 'title_or_game': 'Game', 'objective': 'Casual play'}
        self.assertEqual(recommendation_budget(project, {}), 40)
        self.assertEqual(recommendation_budget(project, {'recommendation_budget_usd': 3}), 3)
        provider = Mock()
        prompts = []
        def respond(**kwargs):
            prompts.append(kwargs['prompt'])
            if len(prompts) == 5:
                raise SubtitlerError('Interrupted')
            evidence = json.loads(kwargs['prompt'].rsplit('\n', 1)[1])
            self.assertLessEqual(len(evidence['related_activity_context']), 23)
            current = evidence['activity']
            absolute = current['start_ms'] + (36_000_000 if current['activity_id'].startswith('source-1') else 0)
            for row in evidence['related_activity_context']:
                end = row['end_ms'] + (36_000_000 if row['source_id'] == 'source-1' else 0)
                self.assertGreater(end, absolute - 3_600_000)
            self.assertLessEqual(len(evidence['earlier_context_digest']), 8)
            self.assertLess(len(json.dumps(evidence['earlier_context_digest'])), 8000)
            if absolute > 4_500_000:
                self.assertTrue(evidence['earlier_context_is_lossy'])
            self.assertLessEqual(len(evidence['activity_state_outline']), 48)
            self.assertLessEqual(sum(len(row['text']) for row in evidence['speech']), 12000)
            self.assertTrue(evidence['speech_evidence_truncated'])
            self.assertTrue(any(row['start_ms'] > current['start_ms'] + 400_000 for row in evidence['speech']))
            props = kwargs['schema']['properties']['assessments']['items']['properties']
            ids = props['target_id']['enum']
            self.assertEqual(ids, [current['activity_id']])
            self.assertLess(len(kwargs['prompt']), 50000)
            return {'assessments': [{'target_id': target, 'suggested_treatment': 'shorten',
                'recommendation': 'Keep the discovery; trim the repeated search.', 'observed_content': 'Search',
                'potential_contribution': 'Supplies', 'reason_and_tradeoff': '', 'evidence_limitation': '',
                'evidence_ids': [], 'related_ids': []} for target in ids]}
        provider.inspect.side_effect = respond
        with tempfile.TemporaryDirectory() as temporary, patch('subtitler.editorial_recommendations.evidence_catalog', return_value=catalog):
            root = Path(temporary)
            with self.assertRaisesRegex(SubtitlerError, 'Interrupted'):
                generate_recommendations(project, root, {}, ApiUsageLedger(), provider)
            result = generate_recommendations(project, root, {}, ApiUsageLedger(), provider)
            expected = sum(len(s['activities']) for s in catalog)
            self.assertEqual(len(result['assessments']), expected)
            self.assertEqual(len({r['target_id'] for r in result['assessments']}), expected)
            self.assertEqual(prompts.count(prompts[0]), 1)
            self.assertEqual(prompts.count(prompts[4]), 2)
            count = provider.inspect.call_count
            self.assertEqual(generate_recommendations(project, root, {}, ApiUsageLedger(), provider), result)
            self.assertEqual(provider.inspect.call_count, count)
            rendered = recommendation_html(result)
            self.assertIn('Shorten — Keep the discovery; trim the repeated search.', rendered)
            self.assertEqual(rendered.count('<article '), expected)

    def test_acoustic_edges_at_late_source_positions_match_the_same_local_audio(self):
        local = [-20.] * 100 + [-35.] * 20 + [-70.] * 250 + [-35.] * 30 + [-20.] * 100
        energy = {'frame_ms': 10, 'rms_dbfs': local}
        expected = refine_gap(SpeechGap(1, 4, 1.1, 3.95), energy, duration=5)
        shift = 36_000
        # A virtual 10-hour prefix verifies the implementation reads just the gap neighborhood.
        class LongAudio:
            def __len__(self):
                return shift * 100 + len(local)
            def __getitem__(self, key):
                self_outer.assertIsInstance(key, slice)
                self_outer.assertLessEqual(key.stop - key.start, len(local))
                return local[key.start - shift * 100:key.stop - shift * 100]
        self_outer = self
        actual = refine_gap(SpeechGap(shift + 1, shift + 4, shift + 1.1, shift + 3.95),
                            {'frame_ms': 10, 'rms_dbfs': LongAudio()}, duration=shift + 5)
        self.assertAlmostEqual(actual[0] - shift, expected[0])
        self.assertAlmostEqual(actual[1] - shift, expected[1])
        self.assertEqual(actual[2]['noise_floor_dbfs'], expected[2]['noise_floor_dbfs'])
