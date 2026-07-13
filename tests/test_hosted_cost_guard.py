import unittest

from subtitler.backends.existing_pipeline import enforce_cost_guard, estimate_backend_run_cost
from subtitler.config import load_workflow_config
from subtitler.errors import SubtitlerError


class HostedCostGuardTests(unittest.TestCase):
    def test_hosted_estimate_uses_selected_speech_seconds(self):
        config = load_workflow_config("hosted")

        cost = estimate_backend_run_cost(config, speech_seconds=60.0)

        self.assertGreater(cost, 0.0)

    def test_local_workflow_estimate_is_zero(self):
        config = load_workflow_config("local")

        cost = estimate_backend_run_cost(config, speech_seconds=600.0)

        self.assertEqual(cost, 0.0)

    def test_allow_api_spend_setting_is_config_only(self):
        config = load_workflow_config("hosted")
        config["cost"]["allow_api_spend"] = True

        self.assertTrue(config["cost"]["allow_api_spend"])
        self.assertGreater(estimate_backend_run_cost(config, speech_seconds=60.0), 0.0)

    def test_hosted_cost_above_limit_raises(self):
        config = load_workflow_config("hosted")
        config["cost"]["max_estimated_api_cost_usd"] = 0.01

        with self.assertRaises(SubtitlerError):
            enforce_cost_guard(config, estimated_api_cost=1.0)

    def test_allow_api_spend_permits_above_limit_estimate(self):
        config = load_workflow_config("hosted")
        config["cost"]["max_estimated_api_cost_usd"] = 0.01
        config["cost"]["allow_api_spend"] = True

        enforce_cost_guard(config, estimated_api_cost=1.0)

    def test_string_false_cannot_authorize_api_spend(self):
        config = load_workflow_config("hosted")
        config["cost"]["max_estimated_api_cost_usd"] = 0.01
        config["cost"]["allow_api_spend"] = "false"

        with self.assertRaisesRegex(SubtitlerError, "allow_api_spend must be a boolean"):
            enforce_cost_guard(config, estimated_api_cost=1.0)

    def test_non_finite_limit_fails_closed(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                config = load_workflow_config("hosted")
                config["cost"]["max_estimated_api_cost_usd"] = value
                config["cost"]["allow_api_spend"] = True
                with self.assertRaisesRegex(SubtitlerError, "finite non-negative number"):
                    enforce_cost_guard(config, estimated_api_cost=1.0)

    def test_non_finite_estimate_fails_closed(self):
        config = load_workflow_config("hosted")
        config["cost"]["allow_api_spend"] = True

        with self.assertRaisesRegex(SubtitlerError, "estimated API cost must be"):
            enforce_cost_guard(config, estimated_api_cost=float("nan"))


if __name__ == "__main__":
    unittest.main()
