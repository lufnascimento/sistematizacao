import json
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.row_profile_summary import summarize_profile


REFERENCE = {"usage": "SCREENING_ALERT_ONLY", "parameter_id": "e0.reference_alert_grade_pct",
             "grade_alert_pct": 5, "request_id": "req-test", "request_sha256": "a" * 64}
PROFILE = {"chainage_reference": "SOURCE_PROJECTED_METRIC_XY", "source_chainages_m": [0, 10, 20, 30, 40],
           "source_elevations_m": [0, 1, 0, 0, 1], "profile_reference": REFERENCE}


class RowProfileSummaryTests(unittest.TestCase):
    def test_intervals_are_merged_and_length_weighted(self):
        summary = summarize_profile(PROFILE)
        self.assertEqual(summary["alert_length_m"], 30)
        self.assertEqual(summary["maximum_absolute_segment_grade_pct"], 10)
        self.assertEqual([(item["start_chainage_m"], item["end_chainage_m"]) for item in summary["alert_intervals"]], [(0, 20), (30, 40)])

    def test_equality_does_not_count_as_exceedance_or_approval(self):
        summary = summarize_profile({**PROFILE, "profile_reference": {**REFERENCE, "grade_alert_pct": 10}})
        self.assertEqual(summary["screening_status"], "NO_SAMPLED_EXCEEDANCE_NOT_APPROVED")
        self.assertEqual(summary["alert_length_m"], 0)

    def test_missing_reference_is_not_zero_exceedance(self):
        summary = summarize_profile({**PROFILE, "profile_reference": None})
        self.assertEqual(summary["reason"], "TRACEABLE_REFERENCE_MISSING")
        self.assertIsNone(summary["alert_length_m"])
        self.assertEqual(summary["length_m"], 40)

    def test_invalid_geometry_is_not_evaluated(self):
        for change in ({"source_chainages_m": [0, 10, 10, 30, 40]},
                       {"source_elevations_m": [0, None, 0, 0, 1]},
                       {"source_elevations_m": [0, True, 0, 0, 1]},
                       {"source_chainages_m": [0, 10]},
                       {"chainage_reference": "DISPLAY_MERCATOR"}):
            with self.subTest(change=change):
                summary = summarize_profile({**PROFILE, **change})
                self.assertEqual(summary["reason"], "INVALID_SOURCE_PROFILE")
                self.assertIsNone(summary["length_m"])

    @unittest.skipUnless(shutil.which("node"), "Node required for browser/server parity")
    def test_server_and_browser_profiles_agree(self):
        module = (Path(__file__).resolve().parents[1] / "platform/web/line-profile.mjs").as_uri()
        fixtures = [PROFILE, {**PROFILE, "profile_reference": None},
                    {**PROFILE, "profile_reference": {**REFERENCE, "grade_alert_pct": 10}},
                    {**PROFILE, "source_elevations_m": [2, 2, 2, 2, 2]},
                    {**PROFILE, "source_chainages_m": [0, 3, 17, 81, 500]}]
        script = f'import {{lineProfile}} from {json.dumps(module)}; import {{readFileSync}} from "node:fs"; console.log(JSON.stringify(JSON.parse(readFileSync(0,"utf8")).map(lineProfile)));'
        completed = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                                   input=json.dumps(fixtures), text=True, capture_output=True, check=True, timeout=30)
        for properties, browser in zip(fixtures, json.loads(completed.stdout)):
            server = summarize_profile(properties)
            self.assertEqual(server["length_m"], browser["length"])
            self.assertEqual(server["maximum_absolute_segment_grade_pct"], browser["maximumGrade"])
            if browser["alert"] is None:
                self.assertIsNone(server["alert_intervals"])
                continue
            self.assertEqual(server["alert_length_m"], browser["alert"]["length"])
            self.assertEqual(server["alert_intervals"], [{"start_chainage_m": item["start"],
                "end_chainage_m": item["end"], "maximum_absolute_grade_pct": item["maximumGrade"]}
                for item in browser["alert"]["intervals"]])
