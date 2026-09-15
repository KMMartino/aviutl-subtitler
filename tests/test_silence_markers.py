import copy
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project, write_editorial_checkpoint
from subtitler.editorial_project_cli import main
from subtitler.source_inspection import SourceInspection


class SilenceMarkerTests(unittest.TestCase):
    def test_local_export_ignores_old_transcripts_preserves_analysis_and_reuses_vad(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'source.mkv'
            media.write_bytes(b'media')
            project = create_editorial_project([EditorialSourceInput(media, 10000, frame_rate=30, width=640, height=360)],
                                              EditorialProjectOptions('Recording', 'Silence', 1000, 10000))
            project['sources'][0]['result'] = {'speech_segments': [{'start_ms': 0, 'end_ms': 10000}]}
            original = copy.deepcopy(project['sources'])
            checkpoint = root / 'project.json'
            write_editorial_checkpoint(checkpoint, project)
            probe = SourceInspection(10000, 10000, 30, None, None, None)
            with patch('subtitler.silence_markers.inspect_recording', return_value=probe), \
                 patch('subtitler.silence_markers.collect_voice_activity', return_value={'speech': [(1000, 2000), (6000, 7000)], 'energy': {'frame_ms': 10, 'rms_dbfs': [-80] * 1000}}) as vad, \
                 patch('subtitler.silence_markers.prepare_editorial_audio'), \
                 patch('subtitler.editorial_project_cli.HostedEditorialStageExecutor') as hosted:
                for _ in range(2):
                    self.assertEqual(main(['run', '--checkpoint', str(checkpoint)]), 0)
                hosted.assert_not_called()
                vad.assert_called_once()
            import json
            saved = json.loads(checkpoint.read_text(encoding='utf8'))
            self.assertEqual(saved['sources'], original)
            self.assertEqual(saved['silence_markers']['status'], 'complete')
            self.assertGreater(len(saved['silence_markers']['cuts']), 0)
            self.assertFalse(checkpoint.with_suffix('.html').exists())
            exo = checkpoint.with_suffix('.exo').read_text(encoding='shift_jis')
            self.assertIn(str(media.resolve()), exo)
            texts = re.findall(r'^text=(.*)$', exo, re.M)
            self.assertTrue(texts)
            self.assertTrue(all(not value.strip('0\r') for value in texts))
            self.assertNotIn('[CUT]', exo)

    def test_subtitle_entrypoint_routes_long_stream_before_transcription(self):
        from subtitler.run_context import CliArguments
        from subtitler.subtitle_workflow import run_subtitle_workflow
        with patch('subtitler.editorial_project_cli.main', return_value=0) as markers, \
             patch('subtitler.subtitle_workflow.prepare_run_context') as subtitle:
            self.assertEqual(run_subtitle_workflow(CliArguments(input='source.mkv', workflow='hosted-long-stream', output=None, config=None, env_file='.env', profile=False, audio_track=None, sidecar_dir=None, no_sidecars=False, glossary=None, no_glossary=False)), 0)
            subtitle.assert_not_called()
            self.assertIn('--source', markers.call_args.args[0])
