import unittest
from datetime import date

from subtitler.api_costs import estimate_cleanup_cost, estimate_transcription_cost, token_cost


class ApiCostTests(unittest.TestCase):
    def test_gemini_audio_estimate_uses_32_tokens_per_second(self) -> None:
        cost = estimate_transcription_cost("gemini", "gemini-3-flash-preview", 60.0)
        self.assertAlmostEqual(cost, (1920 * 1.00 + 720 * 3.00) / 1_000_000)

    def test_gemini_text_cost_uses_input_and_output(self) -> None:
        cost = token_cost("gemini", "gemini-2.5-flash", input_tokens=1000, output_tokens=500)
        self.assertAlmostEqual(cost, (1000 * 0.30 + 500 * 2.50) / 1_000_000)

    def test_gemini_36_and_37_flash_prices_roll_over_after_promotion(self) -> None:
        for model in ("gemini-3.6-flash", "gemini-3.7-flash"):
            with self.subTest(model=model):
                promotional = token_cost(
                    "gemini", model, input_tokens=1000, output_tokens=500, as_of=date(2026, 12, 31)
                )
                standard = token_cost(
                    "gemini", model, input_tokens=1000, output_tokens=500, as_of=date(2027, 1, 1)
                )
                self.assertAlmostEqual(promotional, (1000 * 0.75 + 500 * 3.75) / 1_000_000)
                self.assertAlmostEqual(standard, (1000 * 1.50 + 500 * 7.50) / 1_000_000)

    def test_gpt_transcribe_estimate_uses_per_minute_rate(self) -> None:
        self.assertAlmostEqual(estimate_transcription_cost("openai", "gpt-transcribe", 120.0), 0.009)

    def test_cleanup_estimate_is_zero_for_none_backend(self) -> None:
        self.assertEqual(estimate_cleanup_cost("none", "", 600.0), 0.0)

    def test_gpt_56_cleanup_prices(self) -> None:
        for model, input_rate, output_rate in (
            ("gpt-5.6-sol", 5.00, 30.00),
            ("gpt-5.6-terra", 2.00, 12.00),
            ("gpt-5.6-luna", 0.20, 1.20),
        ):
            with self.subTest(model=model):
                self.assertAlmostEqual(
                    token_cost("openai", model, input_tokens=1000, output_tokens=500),
                    (1000 * input_rate + 500 * output_rate) / 1_000_000,
                )


if __name__ == "__main__":
    unittest.main()
