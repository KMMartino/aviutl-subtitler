from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.reference_synthesis import (
    COMPARISON_SCHEMA,
    RULESET_SCHEMA,
    compare_manifest,
    synthesize_ruleset,
)


def _comparison() -> dict:
    return {
        "comparison_summary": "The finished version compresses the VOD while preserving the encounter payoff.",
        "finished_structure": ["cold open", "setup", "selected gameplay"],
        "vod_to_finished_transformations": ["removed travel and repeated attempts"],
        "narration_findings": ["post-recorded narration is confined to setup"],
        "visual_findings": ["visual continuity carries the gameplay sections"],
        "opening_consistency": "The opening establishes the production style before gameplay settles in.",
        "editorial_principles": ["Make omissions serve a clear payoff."],
        "uncertainties": [],
    }


def _rules() -> dict:
    return {
        "overview": "Choose an intentional opening and preserve a coherent throughline.",
        "style_selection_rules": ["Select a style from the run intent and footage."],
        "opening_rules": ["A later narration should match the opening contract."],
        "narration_rules": ["Use post narration to bridge or explain meaningful omissions."],
        "gameplay_editing_rules": ["Remove repetition while retaining visual payoff."],
        "story_and_pacing_rules": ["Favor a good video over a numeric duration."],
        "application_guidance": ["Use broad beats before proposing local actions."],
        "anti_patterns": ["Do not confuse streamer commentary with voiceover."],
        "evidence_basis": ["finished-vod comparison"],
        "uncertainties": [],
    }


class ReferenceSynthesisTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"items": [{"key": "finished", "kind": "finished", "creator": "A", "notes": "Narration opening.", "vod_key": "vod"}, {"key": "vod", "kind": "vod"}]}), encoding="utf-8")
        artifacts = root / "artifacts"
        alignment = root / "alignment"
        for key in ("finished", "vod"):
            folder = artifacts / key
            folder.mkdir(parents=True)
            (folder / "reference-analysis.json").write_text(json.dumps({"status": "complete", "result": {"video_summary": key}}), encoding="utf-8")
        (alignment / "finished").mkdir(parents=True)
        (alignment / "finished" / "finished-vod-alignment.json").write_text(
            json.dumps({"status": "complete", "spans": []}), encoding="utf-8"
        )
        return manifest, artifacts, alignment

    @patch("tools.reference_synthesis.require_api_key", return_value="secret")
    @patch("tools.reference_synthesis.request_json")
    def test_comparison_schema_checkpoint_and_cache(self, request, _key) -> None:
        request.return_value = {"usage": {"input_tokens": 10, "output_tokens": 5}, "output": [{"content": [{"type": "output_text", "text": json.dumps(_comparison())}]}]}
        with tempfile.TemporaryDirectory() as name:
            manifest, artifacts, alignment = self._fixture(Path(name))
            first = compare_manifest(manifest, artifacts, alignment, Path(name) / "out")
            second = compare_manifest(manifest, artifacts, alignment, Path(name) / "out")
            self.assertEqual(first, second)
            request.assert_called_once()
            payload = request.call_args.args[2]
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertEqual(payload["text"]["format"]["schema"], COMPARISON_SCHEMA)

    @patch("tools.reference_synthesis.require_api_key", return_value="secret")
    @patch("tools.reference_synthesis.request_json")
    def test_synthesis_uses_terra_defaults_and_retains_review_metadata(self, request, _key) -> None:
        request.return_value = {"usage": {}, "output": [{"content": [{"type": "output_text", "text": json.dumps(_rules())}]}]}
        with tempfile.TemporaryDirectory() as name:
            manifest, artifacts, alignment = self._fixture(Path(name))
            comparison = artifacts / "finished" / "finished-vod-comparison.json"
            comparison.write_text(json.dumps({"status": "complete", "result": _comparison()}), encoding="utf-8")
            result = synthesize_ruleset(manifest, artifacts, Path(name) / "rules.json", final_review_model="gpt-5.6-luna", final_review_reasoning="high")
            self.assertEqual(result["model"], "gpt-5.6-terra")
            self.assertEqual(result["final_review_model"], "gpt-5.6-luna")
            self.assertEqual(request.call_count, 2)
            self.assertTrue((Path(name) / "rules.draft.json").is_file())
            self.assertEqual(request.call_args.args[2]["text"]["format"]["schema"], RULESET_SCHEMA)


if __name__ == "__main__":
    unittest.main()
