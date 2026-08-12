import json
import tempfile
import unittest
from pathlib import Path

from subtitler.game_knowledge import (
    game_profile_context,
    load_game_profile,
    update_game_profile,
)


class _Provider:
    def complete_structured(self, prompt: str, *, max_tokens: int, operation: str) -> str:
        self.prompt = prompt
        self.operation = operation
        return json.dumps({
            "visual_signatures": ["Red health bar in the upper left"],
            "locations": ["The hub contains the upgrade menu"],
            "bosses_enemies": ["A named boss resets after player defeat"],
            "menus_and_upgrade_states": ["Equipment screen shows weapon changes"],
            "retry_patterns": ["Loading screen followed by the same arena indicates a retry"],
            "objectives_and_mechanics": ["Parries create a short damage window"],
            "terminology": ["The creator calls the hub base"],
        })


class GameKnowledgeTests(unittest.TestCase):
    def test_accumulates_a_bounded_profile_under_a_normalized_title_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.json"
            provider = _Provider()

            profile = update_game_profile(
                path=path,
                title="  Example   Game ",
                provider=provider,
                visual_summary={"description": "A boss fight"},
                transcript_excerpt=[],
                temporal_bursts=[],
                output_locale="ja",
            )
            loaded = load_game_profile(path, "example game")

            self.assertEqual(profile["revision"], 1)
            self.assertEqual(loaded["revision"], 1)
            self.assertEqual(loaded["key"], "example game")
            self.assertIn("Red health bar", game_profile_context(loaded))
            self.assertEqual(provider.operation, "editorial_game_learning")
            self.assertEqual(loaded["output_locale"], "ja")
            self.assertIn("natural, concise Japanese", provider.prompt)


if __name__ == "__main__":
    unittest.main()
