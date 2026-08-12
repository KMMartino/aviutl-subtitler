from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from subtitler.broll import (
    CatalogAsset,
    CatalogSegment,
    ProposedPlacement,
    apply_confidence_policy,
    load_catalog,
    parse_broll_needs,
    parse_broll_response,
    plan_broll,
    retrieve_catalog_assets,
    _to_exo_placement,
)
from subtitler.models import Subtitle


class FakeProvider:
    provider = "openai"
    model = "test-model"

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    def complete(self, _prompt: str) -> str:
        if not self.responses:
            raise AssertionError("Unexpected B-roll provider call")
        return self.responses.pop(0)

    def close(self) -> None:
        pass


class BrollTests(unittest.TestCase):
    def test_load_catalog_only_returns_enabled_available_existing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = root / "library.sqlite3"
            media = root / "gameplay.mp4"
            media.write_bytes(b"fixture")
            with closing(sqlite3.connect(database)) as db:
                db.executescript(
                    """
                    CREATE TABLE library_roots (id TEXT PRIMARY KEY, enabled INTEGER);
                    CREATE TABLE assets (
                      id TEXT PRIMARY KEY, root_id TEXT, canonical_path TEXT, media_kind TEXT,
                      title TEXT, user_description TEXT, ai_description TEXT,
                      inferred_description TEXT, duration_ms INTEGER,
                      frame_rate_num INTEGER, frame_rate_den INTEGER, has_audio INTEGER,
                      availability TEXT, updated_at TEXT
                    );
                    CREATE TABLE asset_segments (
                      id TEXT, asset_id TEXT, start_ms INTEGER, end_ms INTEGER,
                      description TEXT, confidence REAL
                    );
                    INSERT INTO library_roots VALUES ('root', 1);
                    """
                )
                db.execute(
                    "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "asset",
                        "root",
                        str(media),
                        "video",
                        "Gameplay",
                        "Boss fight gameplay",
                        "",
                        "",
                        30_000,
                        60,
                        1,
                        1,
                        "active",
                        "2026-01-01",
                    ),
                )
                db.execute(
                    "INSERT INTO asset_segments VALUES ('segment', 'asset', 5000, 10000, 'Boss appears', .9)"
                )
                db.commit()
            assets = load_catalog(database, "The boss fight was difficult")
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].description, "Boss fight gameplay")
            self.assertEqual(assets[0].segments[0].start_sec, 5.0)

    def test_load_catalog_respects_directory_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = root / "library.sqlite3"
            media = root / "hidden" / "gameplay.mp4"
            media.parent.mkdir()
            media.write_bytes(b"fixture")
            with closing(sqlite3.connect(database)) as db:
                db.executescript(
                    """
                    CREATE TABLE library_roots (id TEXT PRIMARY KEY, enabled INTEGER);
                    CREATE TABLE assets (
                      id TEXT PRIMARY KEY, root_id TEXT, relative_directory TEXT,
                      canonical_path TEXT, media_kind TEXT, title TEXT,
                      user_description TEXT, ai_description TEXT, inferred_description TEXT,
                      duration_ms INTEGER, frame_rate_num INTEGER, frame_rate_den INTEGER,
                      has_audio INTEGER, availability TEXT, updated_at TEXT
                    );
                    CREATE TABLE asset_segments (
                      id TEXT, asset_id TEXT, start_ms INTEGER, end_ms INTEGER,
                      description TEXT, confidence REAL
                    );
                    CREATE TABLE library_directory_visibility (
                      root_id TEXT, relative_directory TEXT, kind TEXT, visible INTEGER
                    );
                    INSERT INTO library_roots VALUES ('root', 1);
                    INSERT INTO assets VALUES (
                      'asset', 'root', 'hidden', '', 'video', 'Hidden gameplay',
                      'Gameplay', '', '', 30000, 60, 1, 1, 'active', '2026-01-01'
                    );
                    INSERT INTO library_directory_visibility VALUES ('root', 'hidden', 'subtree', 0);
                    """
                )
                db.execute("UPDATE assets SET canonical_path=? WHERE id='asset'", (str(media),))
                db.commit()
            self.assertEqual(load_catalog(database), [])

    def test_parser_rejects_unknown_and_overlapping_placements(self) -> None:
        asset = CatalogAsset(
            id="asset",
            path=Path("gameplay.mp4"),
            media_kind="video",
            title="Gameplay",
            description="Boss fight",
            duration_sec=30.0,
            has_audio=True,
            segments=(),
        )
        subtitles = [
            Subtitle(0.0, 2.0, "first"),
            Subtitle(2.0, 4.0, "second"),
            Subtitle(4.0, 6.0, "third"),
        ]
        raw = json.dumps(
            {
                "placements": [
                    {
                        "start_line": 1,
                        "end_line": 2,
                        "asset_id": "asset",
                        "source_start_sec": 3,
                        "source_end_sec": 10,
                        "confidence": 0.8,
                        "reason": "shows the topic",
                    },
                    {
                        "start_line": 2,
                        "end_line": 3,
                        "asset_id": "asset",
                        "source_start_sec": 12,
                        "source_end_sec": 15,
                        "confidence": 0.9,
                    },
                    {
                        "start_line": 3,
                        "end_line": 3,
                        "asset_id": "unknown",
                        "source_start_sec": 0,
                        "source_end_sec": 2,
                        "confidence": 0.9,
                    },
                ],
                "missing_assets": [
                    {"start_line": 3, "end_line": 3, "description": "developer interview", "reason": "not indexed"}
                ],
            }
        )
        proposed, missing, rejected = parse_broll_response(raw, [asset], subtitles)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(len(missing), 1)
        self.assertEqual(len(rejected), 2)

    def test_automatic_policy_requires_independent_safe_scores(self) -> None:
        asset = CatalogAsset("asset", Path("image.png"), "image", "Still", "", None, False, ())
        items = [
            ProposedPlacement(
                "unsafe", asset, 1, 1, 0, None, 0.9, "",
                need_score=.9, relevance_score=.9, placement_safety_score=.5,
                source_grounding_score=.9, technical_quality_score=.9,
            ),
            ProposedPlacement(
                "good", asset, 2, 2, 0, None, 0.9, "",
                need_score=.9, relevance_score=.9, placement_safety_score=.9,
                source_grounding_score=.9, technical_quality_score=.9,
            ),
        ]
        accepted, omitted = apply_confidence_policy(items, mode="automatic", frontend_protocol=None)
        self.assertEqual([item.id for item in accepted], ["good"])
        self.assertEqual(omitted[0]["reason"], "safe_policy_failed")
        self.assertIn("placement_safety", omitted[0]["failed_scores"])

    def test_need_parser_protects_on_screen_explanation_and_retrieval_balances_media(self) -> None:
        subtitles = [Subtitle(0, 1, "Look at this menu"), Subtitle(1, 2, "The boss fight was difficult")]
        raw = json.dumps({
            "protected_ranges": [{"start_line": 1, "end_line": 1}],
            "needs": [
                {"start_line": 1, "end_line": 1, "description": "menu", "search_terms": ["menu"], "preferred_media": "image", "need_score": .9},
                {"start_line": 2, "end_line": 2, "description": "boss fight", "search_terms": ["boss fight"], "preferred_media": "either", "need_score": .8},
            ],
        })
        needs, protected = parse_broll_needs(raw, subtitles)
        self.assertEqual(protected, [(1, 1)])
        self.assertEqual([need.start_line for need in needs], [2])
        assets = [
            CatalogAsset("video", Path("boss.mp4"), "video", "Boss fight", "Gameplay", 10, False, ()),
            CatalogAsset("image", Path("boss.png"), "image", "Boss fight art", "Artwork", None, False, ()),
            CatalogAsset("other", Path("cat.mp4"), "video", "Cat", "Unrelated", 10, False, ()),
        ]
        retrieved = retrieve_catalog_assets(assets, needs)
        self.assertEqual({asset.id for asset in retrieved}, {"video", "image"})

    def test_source_position_uses_asset_frame_rate(self) -> None:
        asset = CatalogAsset(
            "asset",
            Path("gameplay.mp4"),
            "video",
            "Gameplay",
            "",
            30.0,
            False,
            (),
            source_fps=30.0,
        )
        proposed = ProposedPlacement("placement", asset, 1, 1, 3.0, 5.0, 0.9, "relevant")
        placement = _to_exo_placement(proposed, [Subtitle(0, 2, "line")], 60)
        self.assertEqual(placement.source_start_frame, 91)

    def test_user_described_segment_grounds_a_filename_only_asset(self) -> None:
        asset = CatalogAsset(
            "asset",
            Path("trailer.mp4"),
            "video",
            "Trailer",
            "Trailer",
            60.0,
            False,
            (
                CatalogSegment(
                    "segment",
                    10.0,
                    20.0,
                    "Combat trailer section",
                    1.0,
                    description_source="user",
                    locked=True,
                ),
            ),
            description_source="inferred",
        )
        response = json.dumps({
            "placements": [{
                "start_line": 1,
                "end_line": 1,
                "asset_id": "asset",
                "segment_id": "segment",
                "need_score": .9,
                "relevance_score": .9,
                "placement_safety_score": .9,
                "source_grounding_score": .1,
                "technical_quality_score": .8,
                "reason": "The described segment matches.",
            }],
            "missing_assets": [],
        })
        proposed, _missing, rejected = parse_broll_response(
            response,
            [asset],
            [Subtitle(0, 2, "Combat trailer")],
        )
        self.assertEqual(rejected, [])
        self.assertEqual(proposed[0].source_start_sec, 10.0)
        self.assertEqual(proposed[0].source_grounding_score, 1.0)

    def test_filename_only_title_match_is_reviewed_before_final_planning(self) -> None:
        asset = CatalogAsset(
            "asset",
            Path("battle-gameplay.mp4"),
            "video",
            "Battle gameplay",
            "Battle gameplay",
            10.0,
            False,
            (),
            description_source="inferred",
        )
        needs_response = json.dumps(
            {
                "needs": [
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "description": "battle gameplay",
                        "search_terms": ["battle gameplay"],
                        "preferred_media": "video",
                        "need_score": 0.9,
                    }
                ],
                "protected_ranges": [],
            }
        )
        initial_response = json.dumps(
            {
                "placements": [],
                "filename_review_candidates": [
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "asset_id": "asset",
                        "confidence": 0.76,
                        "reason": "The specific title matches the battle discussion.",
                    }
                ],
                "missing_assets": [],
            }
        )
        final_response = json.dumps(
            {
                "placements": [
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "asset_id": "asset",
                        "source_start_sec": 0,
                        "source_end_sec": 2,
                        "need_score": 0.9,
                        "relevance_score": 0.9,
                        "placement_safety_score": 0.9,
                        "source_grounding_score": 0.9,
                        "technical_quality_score": 0.8,
                        "reason": "The user confirmed that the file shows the discussed battle.",
                    }
                ],
                "filename_review_candidates": [],
                "missing_assets": [],
            }
        )
        provider = FakeProvider(needs_response, initial_response, final_response)

        def describe(candidates: object, _subtitles: object, _protocol: object) -> dict[str, str]:
            rows = list(candidates)  # type: ignore[arg-type]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].asset.id, "asset")
            return {rows[0].id: "A gameplay clip showing the battle from start to finish."}, set()

        with (
            patch("subtitler.broll.load_catalog", return_value=[asset]),
            patch("subtitler.broll.request_filename_descriptions", side_effect=describe),
        ):
            outcome = plan_broll(
                mode="automatic",
                database_path=Path("library.sqlite3"),
                subtitles=[Subtitle(0, 2, "The battle was difficult")],
                fps=60,
                provider=provider,
                frontend_protocol="stdio-v1",
                sidecar_path=None,
            )

        self.assertEqual(len(outcome.placements), 1)
        self.assertEqual(outcome.filename_review_count, 1)
        self.assertEqual(outcome.filename_described_count, 1)
        self.assertEqual(outcome.filename_rejected_count, 0)
        self.assertEqual(outcome.retrieved_asset_count, 1)
        self.assertEqual(provider.responses, [])

    def test_cover_scaling_expands_low_resolution_video_to_canvas(self) -> None:
        asset = CatalogAsset(
            "asset", Path("gameplay.mp4"), "video", "Gameplay", "Boss", 30, False, (),
            width=1280, height=720,
        )
        proposed = ProposedPlacement(
            "placement", asset, 1, 1, 0, 2, .9, "relevant",
            need_score=.9, relevance_score=.9, placement_safety_score=.9,
            source_grounding_score=.9, technical_quality_score=.9,
        )
        placement = _to_exo_placement(proposed, [Subtitle(0, 2, "line")], 60, 2560, 1440)
        self.assertEqual(placement.scale_percent, 200.0)

    def test_plan_writes_auditable_sidecar_and_bounds_video_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = root / "library.sqlite3"
            media = root / "gameplay.mp4"
            media.write_bytes(b"fixture")
            with closing(sqlite3.connect(database)) as db:
                db.executescript(
                    """
                    CREATE TABLE library_roots (id TEXT PRIMARY KEY, enabled INTEGER);
                    CREATE TABLE assets (
                      id TEXT PRIMARY KEY, root_id TEXT, canonical_path TEXT, media_kind TEXT,
                      title TEXT, user_description TEXT, ai_description TEXT,
                      inferred_description TEXT, duration_ms INTEGER,
                      frame_rate_num INTEGER, frame_rate_den INTEGER, has_audio INTEGER,
                      availability TEXT, updated_at TEXT
                    );
                    CREATE TABLE asset_segments (
                      id TEXT, asset_id TEXT, start_ms INTEGER, end_ms INTEGER,
                      description TEXT, confidence REAL
                    );
                    INSERT INTO library_roots VALUES ('root', 1);
                    """
                )
                db.execute(
                    "INSERT INTO assets VALUES (?, 'root', ?, 'video', 'Gameplay', 'battle', '', '', 5000, 60, 1, 1, 'active', 'now')",
                    ("asset", str(media)),
                )
                db.commit()
            needs_response = json.dumps(
                {"needs": [{"start_line": 1, "end_line": 2, "description": "battle", "search_terms": ["battle"], "preferred_media": "video", "need_score": .9}], "protected_ranges": []}
            )
            response = json.dumps(
                {
                    "placements": [
                        {
                            "start_line": 1,
                            "end_line": 2,
                            "asset_id": "asset",
                            "source_start_sec": 3,
                            "source_end_sec": 5,
                            "need_score": 0.9,
                            "relevance_score": 0.9,
                            "placement_safety_score": 0.9,
                            "source_grounding_score": 0.9,
                            "technical_quality_score": 0.9,
                            "reason": "battle is discussed",
                        }
                    ],
                    "missing_assets": [],
                }
            )
            sidecar = root / "run.broll_plan.json"
            outcome = plan_broll(
                mode="automatic",
                database_path=database,
                subtitles=[Subtitle(0, 2, "battle"), Subtitle(2, 6, "continues")],
                fps=60,
                provider=FakeProvider(needs_response, response),
                frontend_protocol=None,
                sidecar_path=sidecar,
            )
            self.assertEqual(len(outcome.placements), 1)
            self.assertEqual(outcome.placements[0].output_end_frame, 121)
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(saved["provider"], "openai")
            self.assertEqual(saved["placements"][0]["asset_id"], "asset")

    def test_web_discovery_failure_does_not_discard_local_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = root / "library.sqlite3"
            media = root / "gameplay.mp4"
            media.write_bytes(b"fixture")
            with closing(sqlite3.connect(database)) as db:
                db.executescript(
                    """
                    CREATE TABLE library_roots (id TEXT PRIMARY KEY, enabled INTEGER);
                    CREATE TABLE assets (
                      id TEXT PRIMARY KEY, root_id TEXT, canonical_path TEXT, media_kind TEXT,
                      title TEXT, user_description TEXT, ai_description TEXT,
                      inferred_description TEXT, duration_ms INTEGER,
                      frame_rate_num INTEGER, frame_rate_den INTEGER, has_audio INTEGER,
                      availability TEXT, updated_at TEXT
                    );
                    CREATE TABLE asset_segments (
                      id TEXT, asset_id TEXT, start_ms INTEGER, end_ms INTEGER,
                      description TEXT, confidence REAL
                    );
                    INSERT INTO library_roots VALUES ('root', 1);
                    """
                )
                db.execute(
                    "INSERT INTO assets VALUES (?, 'root', ?, 'video', 'Gameplay', 'battle', '', '', 5000, 60, 1, 1, 'active', 'now')",
                    ("asset", str(media)),
                )
                db.commit()
            needs_response = json.dumps(
                {"needs": [{"start_line": 1, "end_line": 1, "description": "battle", "search_terms": ["battle"], "preferred_media": "video", "need_score": .9}], "protected_ranges": []}
            )
            provider = FakeProvider(
                needs_response,
                json.dumps(
                    {
                        "placements": [
                            {
                                "start_line": 1,
                                "end_line": 1,
                                "asset_id": "asset",
                                "source_start_sec": 0,
                                "source_end_sec": 2,
                                "need_score": 0.9,
                                "relevance_score": 0.9,
                                "placement_safety_score": 0.9,
                                "source_grounding_score": 0.9,
                                "technical_quality_score": 0.9,
                                "reason": "battle footage",
                            }
                        ],
                        "missing_assets": [
                            {"start_line": 1, "end_line": 1, "description": "developer interview"}
                        ],
                    }
                )
            )

            def fail_discovery(_needs: object) -> list[object]:
                raise RuntimeError("search unavailable")

            outcome = plan_broll(
                mode="automatic",
                database_path=database,
                subtitles=[Subtitle(0, 2, "battle")],
                fps=60,
                provider=provider,
                frontend_protocol=None,
                sidecar_path=None,
                web_discovery=fail_discovery,  # type: ignore[arg-type]
            )
            self.assertEqual(len(outcome.placements), 1)
            self.assertEqual(len(outcome.missing_assets), 1)
            self.assertFalse(outcome.error)


if __name__ == "__main__":
    unittest.main()
