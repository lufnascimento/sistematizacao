from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terraflux_api.api import create_app


class ScenarioSelectionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        data_root = root / "state"
        workspace = root / "workspace"
        (workspace / "config").mkdir(parents=True)
        (workspace / "dataset" / "derived").mkdir(parents=True)
        (workspace / "config" / "catalogo_presets_sistema.json").write_text(
            json.dumps({"schema_version": "test", "package_profiles": []}),
            encoding="utf-8",
        )
        self.app = create_app(data_root, workspace)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        project_response = self.client.post(
            "/api/projects",
            json={"name": "Fazenda Seleção", "crs": "EPSG:31982"},
        )
        self.assertEqual(project_response.status_code, 201, project_response.text)
        self.project_id = project_response.json()["id"]
        self.run_id = "run_selection_test"
        self.app.state.store.insert(
            "runs",
            {
                "id": self.run_id,
                "project_id": self.project_id,
                "status": "SUCCEEDED",
                "created_at": "2026-08-31T12:00:00Z",
            },
        )

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def add_scenario(
        self,
        scenario_id: str,
        *,
        code: str,
        family: str,
        status: str,
        geometry_eligible: bool,
    ) -> None:
        self.app.state.store.insert(
            "scenarios",
            {
                "id": scenario_id,
                "project_id": self.project_id,
                "run_id": self.run_id,
                "code": code,
                "name": code,
                "family": family,
                "status": status,
                "geometry_eligible": geometry_eligible,
                "guidance_authorized": False,
                "selected_for_review": False,
                "created_at": f"2026-08-31T12:00:0{len(code)}Z",
            },
        )

    def test_selection_is_exclusive_persistent_and_can_be_removed(self) -> None:
        self.add_scenario(
            "scenario_e0_a",
            code="E0A",
            family="E0",
            status="E0_SCREENING_ONLY_NOT_AUTHORIZED",
            geometry_eligible=True,
        )
        self.add_scenario(
            "scenario_e0_b",
            code="E0B",
            family="E0",
            status="E0_SCREENING_ONLY_NOT_AUTHORIZED",
            geometry_eligible=True,
        )

        first = self.client.post(
            f"/api/runs/{self.run_id}/scenarios/scenario_e0_a/selection",
            json={},
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertTrue(first.json()["scenario"]["selected_for_review"])
        self.assertEqual(first.json()["selection"]["event"], "SELECTED")
        self.assertFalse(first.json()["selection"]["guidance_authorized"])

        second = self.client.post(
            f"/api/runs/{self.run_id}/scenarios/scenario_e0_b/selection",
            json={"selected_for_review": True, "reviewer_note": "Revisar em campo"},
        )
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["selection"]["superseded_scenario_ids"], ["scenario_e0_a"])

        scenarios = {
            item["id"]: item
            for item in self.client.get(f"/api/runs/{self.run_id}/scenarios").json()["items"]
        }
        self.assertFalse(scenarios["scenario_e0_a"]["selected_for_review"])
        self.assertIsNotNone(scenarios["scenario_e0_a"]["deselected_at"])
        self.assertTrue(scenarios["scenario_e0_b"]["selected_for_review"])
        active = self.client.get(f"/api/runs/{self.run_id}/scenario-selection").json()
        self.assertEqual(active["active"]["id"], "scenario_e0_b")
        self.assertEqual(active["history_count"], 2)
        self.assertFalse(active["guidance_authorized"])

        removed = self.client.post(
            f"/api/runs/{self.run_id}/scenarios/scenario_e0_b/selection",
            json={"selected_for_review": False},
        )
        self.assertEqual(removed.status_code, 201, removed.text)
        self.assertFalse(removed.json()["scenario"]["selected_for_review"])
        self.assertEqual(removed.json()["selection"]["event"], "DESELECTED")
        self.assertIsNone(removed.json()["selection"]["selected_at"])
        self.assertIsNotNone(removed.json()["selection"]["deselected_at"])

        active_after_removal = self.client.get(f"/api/runs/{self.run_id}/scenario-selection").json()
        self.assertIsNone(active_after_removal["active"])
        self.assertEqual(active_after_removal["history_count"], 3)
        history = self.client.get(f"/api/runs/{self.run_id}/scenario-selections").json()["items"]
        self.assertEqual(len(history), 3)
        self.assertTrue(all(item["guidance_authorized"] is False for item in history))

        duplicate_removal = self.client.post(
            f"/api/runs/{self.run_id}/scenarios/scenario_e0_b/selection",
            json={"selected_for_review": False},
        )
        self.assertEqual(duplicate_removal.status_code, 409)
        self.assertEqual(duplicate_removal.json()["detail"]["code"], "SCENARIO_NOT_SELECTED")

    def test_cf0_c1_and_ineligible_e0_remain_blocked(self) -> None:
        cases = [
            (
                "scenario_cf0",
                "CF0A",
                "CF0",
                "CF0_GEOMETRIC_PASS_HYDRAULIC_UNCONFIRMED",
                True,
                "CF0_REVIEW_REPRESENTATIVE_FORBIDDEN",
            ),
            (
                "scenario_c1",
                "C1-TI-04",
                "C1_SCREENING",
                "CONCEPT_ONLY",
                False,
                "CONCEPT_ONLY_REVIEW_REPRESENTATIVE_FORBIDDEN",
            ),
            (
                "scenario_e0_diagnostic",
                "E0D",
                "E0",
                "E0_SCREENING_ONLY_NOT_AUTHORIZED",
                False,
                "E0_GEOMETRY_INELIGIBLE",
            ),
        ]
        for scenario_id, code, family, release_status, eligible, blocker_code in cases:
            with self.subTest(scenario_id=scenario_id):
                self.add_scenario(
                    scenario_id,
                    code=code,
                    family=family,
                    status=release_status,
                    geometry_eligible=eligible,
                )
                response = self.client.post(
                    f"/api/runs/{self.run_id}/scenarios/{scenario_id}/selection",
                    json={"selected_for_review": True},
                )
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], blocker_code)


if __name__ == "__main__":
    unittest.main()
