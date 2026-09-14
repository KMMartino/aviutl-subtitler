import unittest
from unittest.mock import patch

from subtitler.game_wiki import lookup_game_wiki


class GameWikiTests(unittest.TestCase):
    def test_accepts_a_confident_game_match_and_bounds_the_extract(self) -> None:
        for page_title in ("Example Game", "Example Game (2025 video game)", "Example Game (ゲーム)"):
            with self.subTest(page_title=page_title):
                responses = [
                    {"query": {"search": [{"title": page_title, "snippet": "2025 video game"}]}},
                    {"query": {"pages": {"1": {"title": page_title, "extract": "x" * 6000}}}},
                ]
                with patch("subtitler.game_wiki.request_json", side_effect=responses):
                    result = lookup_game_wiki("  EXAMPLE   Game ")
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["page_title"], page_title)
                self.assertEqual(len(result["summary"]), 5000)

    def test_rejects_an_unrelated_reference(self) -> None:
        for query, page_title in (("RE9 Insanity", "Deep Insanity"), ("Example Game 2", "Example Game"),
                                  ("Example Game", "Example Game (film)")):
            with self.subTest(query=query, page_title=page_title), patch(
                "subtitler.game_wiki.request_json",
                return_value={"query": {"search": [{"title": page_title, "snippet": "video game"}]}},
            ) as request:
                result = lookup_game_wiki(query)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(request.call_count, 2)  # No extract is fetched for either language.

    def test_rejects_an_unverified_redirect_target(self) -> None:
        for target in ("Different Game", "", "Example Game (film)"):
            with self.subTest(target=target), patch("subtitler.game_wiki.request_json", side_effect=[
                {"query": {"search": [{"title": "Example Game", "snippet": "video game"}]}},
                {"query": {"pages": {"1": {"title": target, "extract": "An unrelated summary."}}}},
                {"query": {"search": []}},
            ]):
                self.assertEqual(lookup_game_wiki("Example Game")["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
