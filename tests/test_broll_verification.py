from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.api_usage import ApiUsageLedger
from subtitler.broll import CatalogAsset, CatalogSegment, ProposedPlacement
from subtitler.broll_verification import verify_placement
from subtitler.models import Subtitle
from tests.test_visual_analysis import analysis


class BrollVerificationTests(unittest.TestCase):
    def test_compatible_evidence_avoids_paid_visual_request_and_keeps_exact_range(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "clip.mp4"
            media.write_bytes(b"test")
            asset = CatalogAsset("a", media, "video", "game", "dodge", 20, False, ())
            item = ProposedPlacement("chosen", asset, 1, 1, 2, 8, .9, "dodge", need_score=.9)
            session = Mock(config={"broll": {"analysis_model": "test"}}, usage=ApiUsageLedger())
            session.execute.side_effect = lambda _, __, produce, decode: decode(produce())
            session.complete.return_value = json.dumps({"placements": [{
                "asset_id": "a", "segment_id": "verified-0", "start_line": 1, "end_line": 1,
                "source_start_sec": 2, "source_end_sec": 5, "confidence": .9,
            }]})
            with patch("subtitler.broll_verification.load_visual_evidence", return_value=analysis(2000, 5000)), \
                 patch("subtitler.broll_verification.analyze_media") as paid:
                result = verify_placement(session, item, [Subtitle(0, 3, "dodge")])
            paid.assert_not_called()
            self.assertEqual(result.id, "chosen")
            self.assertEqual((result.source_start_sec, result.source_end_sec), (2, 5))
            self.assertEqual(session.usage.total_cost_usd, 0)

    def test_user_verified_ranges_and_insufficient_duration_do_not_make_paid_calls(self):
        asset = CatalogAsset("a", Path("clip.mp4"), "video", "game", "", 20, False,
            (CatalogSegment("s", 0, 10, "dodge", 1, description_source="user", locked=True),))
        session = Mock()
        item = ProposedPlacement("chosen", asset, 1, 1, 2, 8, .9, "dodge")
        result = verify_placement(session, item, [Subtitle(0, 3, "dodge")])
        self.assertEqual(result.source_end_sec, 5)
        self.assertIsNone(verify_placement(session, item, [Subtitle(0, 12, "dodge")]))
        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
