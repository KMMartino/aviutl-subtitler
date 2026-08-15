import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.editorial_assets import resolve_editorial_assets
from subtitler.media_analysis import VisualSample


class _EvidenceProvider:
    model = "gpt-5.6-luna"

    def __init__(self, *, verified: bool = True) -> None:
        self.verified = verified
        self.prompts: list[str] = []

    def select_reference(self, prompt, samples, labels):
        self.prompts.append(prompt)
        return (
            {
                "candidate_index": min(1, len(samples) - 1),
                "verified": self.verified,
                "caption": "The reward value is visible.",
                "verification_note": "Visible in the result panel." if self.verified else "No reward value is legible.",
                "confidence": 0.9,
                "crop_x": 0.1,
                "crop_y": 0.2,
                "crop_width": 0.5,
                "crop_height": 0.4,
            },
            1_000,
            200,
        )


class EditorialAssetsTests(unittest.TestCase):
    def test_resolves_only_selected_reference_requests_and_records_verified_crop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "game.mp4"
            media.write_bytes(b"media")
            project = _project(media)

            def extract(_path, *, timestamps, output_dir, prefix, **_kwargs):
                frame = output_dir / f"{prefix}.jpg"
                frame.write_bytes(b"jpeg")
                return [VisualSample(0, timestamps[0], frame)]

            def crop(_source, target, _crop, _ffmpeg):
                target.write_bytes(b"crop")

            with patch("subtitler.editorial_assets._extract_samples", side_effect=extract), patch(
                "subtitler.editorial_assets._write_crop", side_effect=crop
            ):
                result = resolve_editorial_assets(
                    project,
                    workspace=root / "assets",
                    provider=_EvidenceProvider(),
                )

            self.assertEqual(len(result["editorial_assets"]), 1)
            self.assertTrue(result["editorial_assets"][0]["verified"])
            self.assertEqual(result["supporting_edits"][0]["resolved_asset_id"], "asset-001")
            self.assertGreater(result["api_cost_usd"], 0)

    def test_unverified_reference_is_softened_for_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "game.mp4"
            media.write_bytes(b"media")
            project = _project(media)

            def extract(_path, *, timestamps, output_dir, prefix, **_kwargs):
                frame = output_dir / f"{prefix}.jpg"
                frame.write_bytes(b"jpeg")
                return [VisualSample(0, timestamps[0], frame)]

            with patch("subtitler.editorial_assets._extract_samples", side_effect=extract), patch(
                "subtitler.editorial_assets._write_crop"
            ):
                result = resolve_editorial_assets(
                    project,
                    workspace=root / "assets",
                    provider=_EvidenceProvider(verified=False),
                )

            edit = result["supporting_edits"][0]
            self.assertFalse(edit["evidence_verified"])
            self.assertTrue(edit["instruction"].startswith("Verify manually"))


def _project(media: Path) -> dict:
    source_id = "source-1"
    return {
        "output_locale": "en",
        "sources": [{
            "source_id": source_id,
            "original_name": media.name,
            "visual_path": str(media),
            "duration_ms": 60_000,
            "stages": {"visual_learning": {"output": {"segments": [{
                "start_ms": 10_000,
                "end_ms": 20_000,
                "description": "Result screen showing the earned reward",
                "tags": ["reward", "result"],
                "confidence": 0.9,
            }]}}},
        }],
        "editorial_map": {"supporting_edits": [{
            "edit_id": "edit-001",
            "parent_action_id": "action-001",
            "action_type": "insert_reference_visual",
            "source_id": source_id,
            "start_ms": 30_000,
            "end_ms": 31_000,
            "instruction": "Show the reward obtained earlier.",
            "evidence_request": True,
            "reference_query": "earned reward result",
            "reference_source_ids": [source_id],
        }]},
    }


if __name__ == "__main__":
    unittest.main()
