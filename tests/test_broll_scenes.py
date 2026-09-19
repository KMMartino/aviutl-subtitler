from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from subtitler.broll import (
    BrollNeed, CatalogAsset, CatalogSegment, ProposedPlacement, collect_broll_needs,
    enforce_timeline_policy, load_catalog, parse_broll_response, plan_broll, retrieve_catalog_assets,
)
from subtitler.broll_review import request_placement_choices
from subtitler.broll_stage import retime_source_scenes
from subtitler.models import Subtitle
from subtitler.review_exchange import ReviewError
from tests.test_broll import FakeProvider


class SceneRetrievalTests(unittest.TestCase):
    def test_primary_scene_protection_uses_the_edited_timeline(self):
        scenes = retime_source_scenes([
            {"start_ms": 2000, "end_ms": 4000, "observed_label": "loading"},
            {"start_ms": 8000, "end_ms": 10000, "observed_label": "item reveal"},
        ], ((2, 4),))
        self.assertEqual(scenes, [{"start_ms": 6000, "end_ms": 8000, "observed_label": "item reveal"}])
        prompts = []

        class Provider:
            def complete(self, prompt, **_):
                prompts.append(prompt)
                return json.dumps({"needs": [], "protected_ranges": [{"start_line": 1, "end_line": 1}]})

        needs, protected = collect_broll_needs(Provider(), [Subtitle(6, 8, "Here it is")], scenes)
        self.assertEqual(needs, [])
        self.assertEqual(protected, [(1, 1)])
        self.assertIn('"observed_label": "item reveal"', prompts[0])

    def test_scene_label_retrieves_late_action_without_unrelated_scenes(self):
        scenes = tuple(CatalogSegment(str(i), i * 3, (i + 1) * 3, "Walking", .9) for i in range(400))
        scenes += (CatalogSegment("dodge", 1200, 1210, "Gameplay", .9, observed_label="Dodging an enemy attack"),)
        assets = [CatalogAsset("a", Path("game.mp4"), "video", "Recording", "Gameplay", 1210, False, scenes)]
        need = BrollNeed(1, 1, "evasion", ("dodging",), "video", .9)
        result = retrieve_catalog_assets(assets, [need])
        self.assertEqual([segment.id for segment in result[0].segments], ["dodge"])
        jp = replace(scenes[-1], observed_label="敵の攻撃を回避する")
        result = retrieve_catalog_assets([replace(assets[0], segments=(jp,))], [replace(need, search_terms=("回避",))])
        self.assertEqual(result[0].segments[0].id, "dodge")

    def test_catalog_does_not_hide_old_assets_or_late_scenes(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "media.mp4"
            media.write_bytes(b"test")
            database = Path(directory) / "catalog.db"
            with sqlite3.connect(database) as db:
                db.executescript("""
                    CREATE TABLE library_roots(id TEXT, enabled INTEGER);
                    INSERT INTO library_roots VALUES ('r',1);
                    CREATE TABLE assets(id TEXT, root_id TEXT, canonical_path TEXT, media_kind TEXT,
                        title TEXT, user_description TEXT, ai_description TEXT, inferred_description TEXT,
                        duration_ms INTEGER, frame_rate_num INTEGER, frame_rate_den INTEGER, has_audio INTEGER,
                        availability TEXT, updated_at TEXT);
                    CREATE TABLE asset_segments(id TEXT, asset_id TEXT, start_ms INTEGER, end_ms INTEGER,
                        description TEXT, confidence REAL, observed_label TEXT);
                """)
                db.executemany("INSERT INTO assets VALUES (?, 'r', ?, 'video', 'clip', '', '', '', 500000, 30, 1, 0, 'active', ?)",
                               ((str(i), str(media), str(i).zfill(4)) for i in range(1002)))
                db.executemany("INSERT INTO asset_segments VALUES (?, '0', ?, ?, 'gameplay', .9, ?)",
                               ((str(i), i * 1000, (i + 1) * 1000, "dodge" if i == 350 else "walk") for i in range(351)))
            db.close()
            assets = load_catalog(database)
            self.assertEqual(len(assets), 1002)
            result = retrieve_catalog_assets(assets, [BrollNeed(1, 1, "dodge", ("dodge",), "video", .9)])
            self.assertEqual(result[0].id, "0")
            self.assertEqual(result[0].segments[0].id, "350")

    def test_windows_keep_global_line_ids_and_cover_tail(self):
        subtitles = [Subtitle(i, i + 1, f"line-{i + 1}") for i in range(8)]
        prompts = []

        class Provider:
            def complete(self, prompt, **_):
                prompts.append(prompt)
                return json.dumps({"needs": [{"start_line": 8, "end_line": 8, "description": "dodge",
                    "search_terms": ["dodge"], "preferred_media": "video", "need_score": .9}], "protected_ranges": []})

        with patch("subtitler.broll.MAX_TRANSCRIPT_CHARS", 260):
            needs, _ = collect_broll_needs(Provider(), subtitles)
        self.assertIn("8\t7.000\t8.000\tline-8", prompts[-1])
        self.assertEqual(len(needs), 1)
        self.assertEqual(needs[0].start_line, 8)

    def test_scene_bounds_are_validated_and_subranges_are_preserved(self):
        asset = CatalogAsset("a", Path("a.mp4"), "video", "a", "", 30, False,
                             (CatalogSegment("s", 10, 20, "dodge", 1),))
        row = {"asset_id": "a", "segment_id": "s", "start_line": 1, "end_line": 1,
               "source_start_sec": 12, "source_end_sec": 15, "confidence": .9}
        items, _, rejected = parse_broll_response(json.dumps({"placements": [row]}), [asset], [Subtitle(0, 3, "dodge")])
        self.assertFalse(rejected)
        self.assertEqual((items[0].source_start_sec, items[0].source_end_sec), (12, 15))
        for invalid in ({"source_end_sec": 21}, {"segment_id": "missing"}):
            items, _, rejected = parse_broll_response(json.dumps({"placements": [{**row, **invalid}]}), [asset], [Subtitle(0, 3, "dodge")])
            self.assertFalse(items)
            self.assertEqual(len(rejected), 1)

    def test_timeline_policy_rejects_short_and_repeated_intervals(self):
        asset = CatalogAsset("a", Path("a.mp4"), "video", "a", "", 30, False, ())
        best = ProposedPlacement("best", asset, 1, 1, 10, 15, .9, "dodge")
        repeated = replace(best, id="repeat", start_line=2, end_line=2, confidence=.8)
        short = replace(best, id="short", start_line=3, end_line=3, source_end_sec=11)
        accepted, omitted = enforce_timeline_policy([best, repeated, short], [Subtitle(i * 3, i * 3 + 3, "dodge") for i in range(3)])
        self.assertEqual([item.id for item in accepted], ["best"])
        self.assertEqual({item["reason"] for item in omitted}, {"repeated_source_interval", "insufficient_source_duration"})

    def test_review_can_choose_an_alternative_and_reject_overlapping_choices(self):
        assets = [CatalogAsset(str(i), Path(f"{i}.png"), "image", "dodge", "dodge", None, False, ()) for i in range(2)]
        row = {"asset_id": "0", "start_line": 1, "end_line": 1, "confidence": .9, "reason": "dodge"}
        provider = FakeProvider(json.dumps({"needs": [{"start_line": 1, "end_line": 1, "description": "dodge",
            "search_terms": ["dodge"], "preferred_media": "image", "need_score": .9}]}),
            json.dumps({"placements": [row], "alternatives": [{**row, "asset_id": "1"}]}))
        seen = []

        def choose(rows, _):
            seen.extend(rows)
            return {rows[-1].id}

        outcome = plan_broll(mode="automatic", database_path=None, subtitles=[Subtitle(0, 3, "dodge")],
                             fps=30, provider=provider, sidecar_path=None, catalog=assets, choose=choose)
        self.assertEqual(outcome.placements[0].asset_id, "1")
        self.assertTrue(all(not row.description_required for row in seen))
        with patch("subtitler.broll_review.request_filename_descriptions", return_value=({}, {row.id for row in seen})):
            with self.assertRaises(ReviewError):
                request_placement_choices(seen, [], "stdio-v1")


if __name__ == "__main__":
    unittest.main()
