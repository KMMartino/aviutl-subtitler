import unittest
from unittest.mock import patch

from subtitler.game_wiki import lookup_game_wiki


class GameWikiTests(unittest.TestCase):
    def test_accepts_a_confident_game_match_and_bounds_the_extract(self) -> None:
        responses = [
            {"query": {"search": [{"title": "Example Game", "snippet": "2025 video game"}]}},
            {"query": {"pages": {"1": {"extract": "A useful game summary."}}}},
        ]
        with patch("subtitler.game_wiki.request_json", side_effect=responses):
            result = lookup_game_wiki("Example Game")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["page_title"], "Example Game")
        self.assertEqual(result["summary"], "A useful game summary.")

    def test_rejects_an_unrelated_reference(self) -> None:
        with patch(
            "subtitler.game_wiki.request_json",
            return_value={"query": {"search": [{"title": "Different Person", "snippet": "biography"}]}},
        ):
            result = lookup_game_wiki("Private stream title")
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
