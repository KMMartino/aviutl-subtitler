import unittest

from subtitler.backends.existing_pipeline import validate_cost_estimate, estimate_backend_run_cost
from subtitler.config import load_workflow_config
from subtitler.errors import SubtitlerError


class HostedCostReportingTests(unittest.TestCase):
    def test_hosted_estimate_uses_selected_speech_seconds(self):
        config = load_workflow_config("hosted")

        cost = estimate_backend_run_cost(config, speech_seconds=60.0)

        self.assertGreater(cost, 0.0)

    def test_local_workflow_estimate_is_zero(self):
        config = load_workflow_config("local")

        cost = estimate_backend_run_cost(config, speech_seconds=600.0)

        self.assertEqual(cost, 0.0)

    def test_legacy_spending_limits_do_not_interrupt_execution(self):
        for legacy in ({"max_estimated_api_cost_usd": 0.01, "allow_api_spend": False},
                       {"max_estimated_api_cost_usd": -1, "allow_api_spend": "false"}, {}):
            config = load_workflow_config("hosted")
            config["cost"].update(legacy)
            validate_cost_estimate(config, estimated_api_cost=100.0)

    def test_default_config_has_no_spending_limit(self):
        self.assertEqual(load_workflow_config("hosted")["cost"], {"estimate_cost_only": False})

    def test_non_finite_estimate_fails_closed(self):
        config = load_workflow_config("hosted")
        config["cost"]["allow_api_spend"] = True

        with self.assertRaisesRegex(SubtitlerError, "estimated API cost must be"):
            validate_cost_estimate(config, estimated_api_cost=float("nan"))


if __name__ == "__main__":
    unittest.main()
