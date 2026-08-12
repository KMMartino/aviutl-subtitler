from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import patch

from subtitler.api_usage import ApiUsageLedger
from subtitler.web_assets import discover_web_assets


@dataclass
class Need:
    start_line: int = 1
    end_line: int = 2
    description: str = "official game trailer"
    reason: str = "The developer announcement is discussed"


class WebAssetDiscoveryTests(unittest.TestCase):
    def test_collects_citations_and_sources_without_claiming_rights(self) -> None:
        response = {
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Official trailer",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/trailer",
                                    "title": "Official trailer",
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"url": "https://example.com/press", "title": "Press kit"},
                            {"url": "file:///unsafe", "title": "Unsafe"},
                        ]
                    },
                },
            ],
        }
        ledger = ApiUsageLedger()
        with patch("subtitler.web_assets.request_json", return_value=response):
            candidates = discover_web_assets(
                [Need()],
                model="gpt-5.6-terra",
                usage=ledger,
                api_key="test-key",
            )
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item.rights_status == "unverified" for item in candidates))
        self.assertEqual(ledger.rows[0].operation, "broll_web_search")


if __name__ == "__main__":
    unittest.main()
