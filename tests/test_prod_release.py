import datetime as dt
import unittest

from scripts.deploy_prod import parameters_for_image, should_activate, verify_mapping


class ProductionReleaseSafety(unittest.TestCase):
    def test_staging_requires_alias_and_preserves_every_unrelated_parameter(self):
        parameters = [{"ParameterKey": k, "ParameterValue": v} for k, v in {
            "ProdPosterAliasEnabled": "true", "ProdPosterImageUri": "old", "ProdPosterArchitecture": "arm64",
            "ProdSvg2PdfImageUri": "pdf", "ProdVpcId": "vpc", "ProdPosterActivationNotBefore": "date",
        }.items()]
        result = {p["ParameterKey"]: p for p in parameters_for_image(parameters, "new")}
        self.assertEqual(result["ProdPosterImageUri"]["ParameterValue"], "new")
        self.assertEqual(result["ProdPosterArchitecture"]["ParameterValue"], "x86_64")
        for key in ("ProdPosterAliasEnabled", "ProdSvg2PdfImageUri", "ProdVpcId", "ProdPosterActivationNotBefore"):
            self.assertTrue(result[key]["UsePreviousValue"])
        parameters[0]["ParameterValue"] = "false"
        with self.assertRaisesRegex(RuntimeError, "route through live"):
            parameters_for_image(parameters, "new")

    def test_no_early_activation_or_automatic_bypass_of_failed_initial_schedule(self):
        gate = "2026-09-19T16:15:00Z"
        before = dt.datetime(2026, 9, 19, 16, 14, 59, tzinfo=dt.timezone.utc)
        after = before + dt.timedelta(seconds=1)
        self.assertFalse(should_activate(gate, before, "x86_64"))
        self.assertFalse(should_activate(gate, after, "arm64"))
        self.assertTrue(should_activate(gate, after, "x86_64"))

    def test_reject_unqualified_or_disabled_queue_routing(self):
        mapping = {"State": "Enabled", "FunctionArn": "function:live", "BatchSize": 1,
                   "ScalingConfig": {"MaximumConcurrency": 5}, "FunctionResponseTypes": ["ReportBatchItemFailures"]}
        verify_mapping(mapping, "function")
        mapping["FunctionArn"] = "function"
        with self.assertRaisesRegex(RuntimeError, "does not invoke live"):
            verify_mapping(mapping, "function")
        mapping["State"] = "Disabled"
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            verify_mapping(mapping, "function")
