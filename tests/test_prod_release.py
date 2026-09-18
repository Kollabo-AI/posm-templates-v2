import datetime as dt
import unittest
from unittest.mock import patch

from scripts.deploy_prod import parameters_for_image, should_activate, verify_mapping, wait_for_scan


class ProductionReleaseSafety(unittest.TestCase):
    @patch("scripts.deploy_prod.time.sleep")
    @patch("scripts.deploy_prod.aws")
    def test_waits_for_scan_registration_then_requires_completion(self, aws, sleep):
        aws.side_effect = [RuntimeError("ScanNotFoundException"),
                           {"imageScanStatus": {"status": "IN_PROGRESS"}},
                           {"imageScanStatus": {"status": "COMPLETE"}, "imageScanFindings": {"findingSeverityCounts": {"LOW": 2}}}]
        self.assertEqual(wait_for_scan("repo", "digest"), {"LOW": 2})
        self.assertEqual(sleep.call_count, 2)

    @patch("scripts.deploy_prod.time.sleep")
    @patch("scripts.deploy_prod.aws")
    def test_missing_scan_times_out_and_permission_errors_fail(self, aws, sleep):
        aws.side_effect = RuntimeError("ScanNotFoundException")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            wait_for_scan("repo", "digest", attempts=2)
        aws.side_effect = RuntimeError("AccessDeniedException")
        with self.assertRaisesRegex(RuntimeError, "AccessDeniedException"):
            wait_for_scan("repo", "digest")

    @patch("scripts.deploy_prod.aws")
    def test_high_findings_block_release(self, aws):
        aws.return_value = {"imageScanStatus": {"status": "COMPLETE"}, "imageScanFindings": {"findingSeverityCounts": {"HIGH": 1}}}
        with self.assertRaisesRegex(RuntimeError, "severity gate"):
            wait_for_scan("repo", "digest")

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
