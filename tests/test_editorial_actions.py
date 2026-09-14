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

if __name__ == "__main__":
    unittest.main()
