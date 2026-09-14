import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.editorial_narration import apply_narration, generate_narration
from subtitler.errors import SubtitlerError


class NarrationTests(unittest.TestCase):
    def project(self):
        return {'title_or_game': 'Game', 'objective': 'First playthrough', 'processing_locale': 'en',
                'editorial_map': {'confirmed_cuts': [{'source_id': 's', 'start_ms': 100, 'end_ms': 500}],
                    'emphasized_phrases': [{'text': 'Original'}], 'final_actions': [],
                    'editor_recommendations': {'assessments': [], 'catalog': [{'name': 'Game', 'source_id': 's',
                        'speech': [], 'activities': [{'activity_id': 'a', 'label': 'Retries', 'start_ms': 0, 'end_ms': 10000}],
                        'states': [{'activity_id': 'a', 'state_id': 's1', 'start_ms': 0, 'end_ms': 5000},
                                   {'activity_id': 'a', 'state_id': 's2', 'start_ms': 5000, 'end_ms': 10000}]}]}}}

    def test_default_narration_is_cached_and_preserves_existing_work(self):
        project = self.project()
        original = copy.deepcopy(project)
        provider = Mock()
        provider.inspect.return_value = {'suggestions': [{'first_state_id': 's1', 'last_state_id': 's2',
            'purpose': 'Explain the change between attempts.', 'memory_jog': 'A failed attempt preceded the changed approach.',
            'talking_points': ['The approach changed after the failure.'], 'representative_visuals': ['The failed attempt and changed approach.'], 'kind': 'summary'}]}
        with tempfile.TemporaryDirectory() as directory, patch('subtitler.editorial_narration.HostedInspectionProvider', return_value=provider):
            first = generate_narration(project, Path(directory), {})
            second = generate_narration(project, Path(directory), {})
            provider.inspect.assert_called_once()
            self.assertEqual(first, second)
            # Changing paid editorial advice must not invalidate factual narration evidence.
            project['editorial_map']['editor_recommendations']['assessments'] = [{'target_id': 'a'}]
            self.assertEqual(first, generate_narration(project, Path(directory), {}))
            provider.inspect.assert_called_once()
            project['editorial_map']['editor_recommendations']['assessments'] = []
            apply_narration(project, second)
        for key in ('confirmed_cuts', 'emphasized_phrases', 'editor_recommendations'):
            self.assertEqual(project['editorial_map'][key], original['editorial_map'][key])
        action = project['editorial_map']['final_actions'][0]
        self.assertEqual((action['start_ms'], action['end_ms']), (0, 10000))
        self.assertEqual(action['narration_guidance']['talking_points'], ['The approach changed after the failure.'])
        self.assertNotIn('narrator_direction', action['narration_guidance'])
        self.assertEqual(generate_narration(project, Path('unused'), {'narration_enabled': False})['narration_briefs'], [])

    def test_saved_narration_survives_loading_the_older_action_checkpoint(self):
        from subtitler.editorial_project import (create_editorial_project, EditorialProjectOptions,
            EditorialSourceInput, load_editorial_checkpoint, write_editorial_checkpoint)
        from subtitler.editorial_narration import narration_inputs
        from subtitler.operation_store import content_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'game.mp4'
            media.write_bytes(b'media')
            project = create_editorial_project([EditorialSourceInput(media, 10000, frame_rate=30)],
                                               EditorialProjectOptions('Game', 'First playthrough', 1000, 9000))
            project['editorial_map'].update(self.project()['editorial_map'])
            stage = project['editorial_map']['action_planning']
            stage.update(status='complete', output={'narration_briefs': [], 'final_actions': []})
            artifact = {'input_revision': content_digest(narration_inputs(project)), 'narration_briefs': [
                {'id': 'n1', 'source_id': project['sources'][0]['source_id'], 'start_ms': 0, 'end_ms': 1000,
                 'purpose': 'Connect the attempts', 'memory_jog': 'The approach changed.'}]}
            apply_narration(project, artifact)
            checkpoint = root / 'guide.json'
            write_editorial_checkpoint(checkpoint, project)
            restored = load_editorial_checkpoint(checkpoint)
            self.assertEqual(restored['editorial_map']['narration_briefs'], artifact['narration_briefs'])
            self.assertEqual(len(restored['editorial_map']['final_actions']), 1)
            self.assertEqual(restored['editorial_map']['action_planning']['output']['final_actions'], [])

    def test_brief_report_references_states_without_scripts_or_state_recommendation_cards(self):
        from subtitler.editorial_recommendation_view import render_recommendation_page
        project = self.project()
        project['editorial_map']['editor_recommendations']['catalog'][0]['states'][1]['display_id'] = '5.A09.S64'
        project['editorial_map']['narration_briefs'] = [{'activity_id': 'a', 'first_state_id': 's1',
            'purpose': 'Explain what changed in s2.', 'memory_jog': 'A failed approach led to a change.',
            'talking_points': ['What changed after the failure.'],
            'representative_visuals': ['Show the changed approach in 5.A09.S64.']}]
        rendered = render_recommendation_page(project, {}, 'en')
        self.assertIn('Explain what changed in A01.S02.', rendered)
        self.assertIn('A failed approach led to a change.', rendered)
        self.assertIn('Points to cover', rendered)
        self.assertIn('Show the changed approach in A01.S02.', rendered)
        self.assertNotIn('5.A09.S64', rendered)
        self.assertNotIn('Narrator draft', rendered)
        self.assertNotIn('segment state', rendered)
        self.assertEqual(rendered.count('segment activity'), 1)

    def test_invalid_narration_ranges_are_rejected(self):
        provider = Mock()
        provider.inspect.return_value = {'suggestions': [{'first_state_id': 's2', 'last_state_id': 's1',
            'purpose': 'Connect attempts', 'memory_jog': 'The approach changed.',
            'talking_points': ['Changed approach'], 'representative_visuals': ['Attempt'], 'kind': 'summary'}]}
        with tempfile.TemporaryDirectory() as directory, patch('subtitler.editorial_narration.HostedInspectionProvider', return_value=provider):
            with self.assertRaises(SubtitlerError):
                generate_narration(self.project(), Path(directory), {})
