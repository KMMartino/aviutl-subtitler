import unittest

from subtitler.editorial_actions import (
    CANONICAL_ACTION_TYPES,
    EDITORIAL_ACTION_SPECS,
    PRIMARY_ACTION_TYPES,
    SUPPORTING_ACTION_TYPES,
)


class EditorialActionCatalogTests(unittest.TestCase):
    def test_catalog_has_unique_dispatch_methods_and_complete_primary_support_split(self) -> None:
        action_types = [item.action_type for item in EDITORIAL_ACTION_SPECS]
        self.assertEqual(len(action_types), len(set(action_types)))
        self.assertEqual(PRIMARY_ACTION_TYPES | SUPPORTING_ACTION_TYPES, CANONICAL_ACTION_TYPES)
        self.assertFalse(PRIMARY_ACTION_TYPES & SUPPORTING_ACTION_TYPES)
        self.assertTrue(all(item.execution_method for item in EDITORIAL_ACTION_SPECS))

    def test_catalog_covers_timeline_narration_accents_continuity_and_review(self) -> None:
        families = {item.family for item in EDITORIAL_ACTION_SPECS}
        self.assertEqual(families, {"timeline", "narration", "accent", "continuity", "review"})
        self.assertTrue(
            {"preserve", "trim", "cut", "extract_highlights", "narrated_montage", "punch_in", "foreshadow", "manual_review"}
            <= CANONICAL_ACTION_TYPES
        )


if __name__ == "__main__":
    unittest.main()
