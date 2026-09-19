from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from subtitler.broll import BrollNeed, load_catalog, retrieve_catalog_assets
from subtitler.media_search import search_media
from subtitler.media_tags import parse_tag


class MediaSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.media = self.root / "recording.mp4"
        self.media.write_bytes(b"test recording")
        self.database = self.root / "library.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript("""
                CREATE TABLE library_roots(id TEXT PRIMARY KEY,enabled INTEGER);
                INSERT INTO library_roots VALUES('root',1);
                CREATE TABLE assets(id TEXT PRIMARY KEY,root_id TEXT,canonical_path TEXT,media_kind TEXT,
                    title TEXT,user_description TEXT,ai_description TEXT,inferred_description TEXT,duration_ms INTEGER,
                    frame_rate_num INTEGER,frame_rate_den INTEGER,has_audio INTEGER,availability TEXT,updated_at TEXT,
                    tags_json TEXT,relative_directory TEXT);
                CREATE TABLE asset_segments(id TEXT,asset_id TEXT,start_ms INTEGER,end_ms INTEGER,description TEXT,
                    confidence REAL,observed_label TEXT,tags_json TEXT);
                CREATE TABLE media_tags(id TEXT,asset_id TEXT,segment_id TEXT,category TEXT,value TEXT,
                    normalized_value TEXT,origin TEXT,evidence TEXT,confidence REAL);
                CREATE VIEW effective_media_tags AS SELECT t.* FROM media_tags t WHERE t.origin='manual' OR NOT EXISTS(
                    SELECT 1 FROM media_tags m WHERE m.asset_id=t.asset_id AND m.segment_id=t.segment_id
                    AND m.category=t.category AND m.origin='manual');
                CREATE TABLE library_directory_visibility(root_id TEXT,relative_directory TEXT,kind TEXT,visible INTEGER);
                INSERT INTO asset_segments VALUES('combat','file',0,5000,'A defensive move',.95,'Knight parries','[]');
                INSERT INTO asset_segments VALUES('walk','file',5000,12000,'Walking through the castle',.9,'Exploring','[]');
            """)
            db.execute("INSERT INTO assets VALUES ('file','root',?,'video','Recording','','AI summary','',12000,30,1,0,'active','now',?,'clips')",
                       (str(self.media), json.dumps(["game:Incorrect Game"])))
            db.executemany("INSERT INTO media_tags VALUES (?,?,?,?,?,?,?,?,?)", [
                ("game-ai", "file", "", "game", "Incorrect Game", "incorrect game", "analysis", "analysis:one", .8),
                ("game-user", "file", "", "game", "Elden Ring", "elden ring", "manual", "user", 1),
                ("broad-action", "file", "", "action", "dodging", "dodging", "analysis", "analysis:one", .8),
                ("scene-action", "file", "combat", "action", "parrying", "parrying", "manual", "user", 1),
                ("scene-tone", "file", "combat", "tone", "tense", "tense", "analysis", "analysis:one", .6),
                ("walk-tone", "file", "walk", "tone", "calm", "calm", "analysis", "analysis:one", .9),
            ])
            db.commit()

    def test_command_outputs_machine_readable_json_without_modifying_the_database(self):
        before = self.database.read_bytes()
        result = subprocess.run([sys.executable, "-m", "subtitler.media_search", "--database", str(self.database),
            "--tag", "game:Elden Ring", "--tag", "action:parrying", "--limit", "1"],
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["results"][0]["id"], "file:combat")
        self.assertEqual(self.database.read_bytes(), before)

    def test_agent_search_returns_exact_scene_with_identity_and_tag_provenance(self):
        result = search_media(self.database, tags=["game:Ｅｌｄｅｎ Ring", "action:parrying"], min_duration=4, min_confidence=.9)
        self.assertEqual(result["total"], 1)
        scene = result["results"][0]
        self.assertEqual(scene["id"], "file:combat")
        self.assertEqual((scene["start_sec"], scene["end_sec"]), (0, 5))
        self.assertEqual(scene["preview"]["path"], str(self.media))
        self.assertIn("action:parrying", scene["matched_tags"])
        self.assertEqual(next(tag for tag in scene["tags"] if tag["category"] == "game")["origin"], "manual")
        self.assertEqual(search_media(self.database, tags=["game:Incorrect Game"])["total"], 0)

    def test_filters_do_not_combine_unrelated_scenes_or_inherit_a_whole_file_action(self):
        self.assertEqual(search_media(self.database, tags=["action:parrying", "tone:calm"])["total"], 0)
        self.assertEqual(search_media(self.database, tags=["action:dodging"])["total"], 0)
        self.assertEqual(search_media(self.database, tags=["tone:tense"], min_confidence=.9)["total"], 0)
        self.assertEqual(search_media(self.database, tags=["action:dodging"], scope="files")["total"], 1)
        self.assertEqual(search_media(self.database, tags=["action:parrying"], min_duration=6)["total"], 0)

    def test_catalog_and_broll_use_effective_tags_after_manual_override(self):
        assets = load_catalog(self.database)
        self.assertIn("game:Elden Ring", assets[0].tags)
        self.assertNotIn("game:Incorrect Game", assets[0].tags)
        result = retrieve_catalog_assets(assets, [BrollNeed(1, 1, "parrying", ("parrying",), "video", .9)])
        self.assertEqual([scene.id for scene in result[0].segments], ["combat"])

    def test_hidden_disabled_and_missing_files_are_excluded(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("INSERT INTO library_directory_visibility VALUES('root','clips','subtree',0)")
            db.commit()
        self.assertEqual(search_media(self.database)["total"], 0)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM library_directory_visibility")
            db.execute("UPDATE library_roots SET enabled=0")
            db.commit()
        self.assertEqual(search_media(self.database)["total"], 0)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE library_roots SET enabled=1")
            db.commit()
        self.media.unlink()
        self.assertEqual(search_media(self.database)["total"], 0)

    def test_stable_pagination_and_invalid_filters(self):
        result = search_media(self.database, limit=1, offset=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["results"][0]["scene_id"], "walk")
        self.assertEqual(search_media(self.database, query="castle")["results"][0]["scene_id"], "walk")
        self.assertEqual(search_media(self.database, tags=["game:' OR 1=1 --"])["total"], 0)
        for filters in ({"limit": 0}, {"min_duration": -1}, {"max_duration": float("nan")}, {"min_confidence": 2}):
            with self.assertRaises(ValueError):
                search_media(self.database, **filters)
        with self.assertRaises(ValueError):
            parse_tag("unsupported:value")


if __name__ == "__main__":
    unittest.main()
