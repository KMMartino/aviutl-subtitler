import unittest

from subtitler.config import WORKFLOWS, load_workflow_config


class WorkflowConfigTests(unittest.TestCase):
    def test_all_workflows_load(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                config = load_workflow_config(workflow)
                self.assertEqual(config["workflow"]["name"], workflow)
                self.assertEqual(config["backend"]["name"], "existing-pipeline")
                self.assertFalse(config["additional_settings"]["youtube_chapters"])

    def test_long_stream_workflows_enable_long_stream_mode(self):
        self.assertEqual(load_workflow_config("local-long-stream")["workflow"]["mode"], "long-stream")
        self.assertEqual(load_workflow_config("hosted-long-stream")["workflow"]["mode"], "long-stream")

    def test_hosted_workflows_default_to_gpt_transcribe(self):
        for workflow in ("hosted", "hosted-long-stream"):
            with self.subTest(workflow=workflow):
                backend = load_workflow_config(workflow)["backend"]
                self.assertEqual(backend["transcriber"], "openai")
                self.assertEqual(backend["transcription_model"], "gpt-transcribe")

if __name__ == "__main__":
    unittest.main()
