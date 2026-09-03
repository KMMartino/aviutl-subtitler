import unittest
from datetime import date
from unittest import mock

from subtitler.api_usage import ApiUsageLedger


class ApiUsageTests(unittest.TestCase):
    def test_gemini_cost_includes_hidden_thinking_tokens(self) -> None:
        ledger = ApiUsageLedger()
        with mock.patch("subtitler.api_costs.date") as mocked_date:
            mocked_date.today.return_value = date(2026, 9, 3)
            ledger.add(
                provider="gemini",
                model="gemini-3.8-flash",
                operation="transcription",
                input_tokens=1_000,
                output_tokens=500,
                total_tokens=1_750,
            )

        self.assertAlmostEqual(
            ledger.rows[0].cost_usd,
            (1_000 * 0.75 + 750 * 3.75) / 1_000_000,
        )
        self.assertEqual(ledger.rows[0].output_tokens, 500)
        self.assertEqual(ledger.rows[0].total_tokens, 1_750)

    def test_openai_cost_keeps_provider_reported_output_tokens(self) -> None:
        ledger = ApiUsageLedger()
        ledger.add(
            provider="openai",
            model="gpt-5.6-luna",
            operation="cleanup",
            input_tokens=1_000,
            output_tokens=500,
            total_tokens=1_750,
        )

        self.assertAlmostEqual(ledger.rows[0].cost_usd, (1_000 * 0.20 + 500 * 1.20) / 1_000_000)


if __name__ == "__main__":
    unittest.main()
