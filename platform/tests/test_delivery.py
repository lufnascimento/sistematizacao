import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terraflux_api.api import create_app
from terraflux_api.delivery import DeliveryError, build_delivery
from terraflux_api.services import canonical_sha256


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "data", self.root / "workspace")
        self.store = self.app.state.store
        self.run = {"id": "run-a", "project_id": "project-a", "request_id": "req-a", "status": "SUCCEEDED"}
        self.store.insert("runs", self.run)
        request = {"id": "req-a", "project_id": "project-a", "configuration_snapshot": {"sigma": 2.5}}
        request["sha256"] = canonical_sha256(request)
        self.store.insert("generation_requests", request)
        self.path = self.store.project_dir("project-a") / "runs" / "run-a" / "products" / "map.pdf"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"example product")
        self.artifact = {"id": "artifact-a", "run_id": "run-a", "project_id": "project-a",
                         "product_id": "TOPOGRAPHY_E0", "filename": "map.pdf", "path": str(self.path),
                         "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(), "size_bytes": self.path.stat().st_size}
        self.store.insert("artifacts", self.artifact)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, **kwargs):
        return build_delivery(self.store.snapshot(), "run-a", self.store.root, **kwargs)

    def test_endpoint_delivers_verified_files_and_cleans_temporary_zip(self):
        with TestClient(self.app) as client:
            response = client.get("/api/runs/run-a/delivery")
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertFalse(manifest["guidance_authorized"])
            self.assertFalse(manifest["original_inputs_included"])
            self.assertEqual(manifest["artifact_count"], 1)
            entry = manifest["artifacts"][0]
            self.assertEqual(hashlib.sha256(archive.read(entry["archive_path"])).hexdigest(), entry["sha256"])
            self.assertNotIn("path", entry)
            self.assertEqual(json.loads(archive.read("request.json"))["configuration_snapshot"]["sigma"], 2.5)
            self.assertEqual(json.loads(archive.read("artifact_reviews.json")), [])
        self.assertEqual(list((self.store.root / "exports").glob("*.zip")), [])

    def test_nonterminal_run_is_rejected(self):
        for status in ("RUNNING", "FAILED", "CANCELLED"):
            self.store.update("runs", "run-a", {"status": status})
            with self.subTest(status=status), self.assertRaisesRegex(DeliveryError, "COMPLETED_RUN"):
                self.build()

    def test_same_size_corruption_is_rejected_and_zip_removed(self):
        self.path.write_bytes(b"EXAMPLE PRODUCT")
        with self.assertRaisesRegex(DeliveryError, "HASH_MISMATCH"):
            self.build()
        self.assertEqual(list((self.store.root / "exports").glob("*.zip")), [])

    def test_other_run_path_is_rejected_even_with_matching_hash(self):
        other = self.path.parent.parent.parent / "run-b" / "map.pdf"
        other.parent.mkdir()
        other.write_bytes(self.path.read_bytes())
        self.store.update("artifacts", "artifact-a", {"path": str(other)})
        with self.assertRaisesRegex(DeliveryError, "OUTSIDE_RUN"):
            self.build()

    def test_different_project_metadata_is_rejected(self):
        self.store.update("artifacts", "artifact-a", {"project_id": "project-b"})
        with self.assertRaisesRegex(DeliveryError, "SCOPE_MISMATCH"):
            self.build()

    def test_changed_request_is_rejected(self):
        self.store.update("generation_requests", "req-a", {"configuration_snapshot": {"sigma": 3}})
        with self.assertRaisesRegex(DeliveryError, "REQUEST_HASH_MISMATCH"):
            self.build()

    def test_review_cannot_reference_another_run_artifact(self):
        self.store.insert("artifact_reviews", {"id": "rev-a", "run_id": "run-a",
                                               "project_id": "project-a", "artifact_id": "other-artifact"})
        with self.assertRaisesRegex(DeliveryError, "REVIEW_ARTIFACT_MISMATCH"):
            self.build()

    def test_limits_and_missing_files_fail_closed(self):
        with self.assertRaisesRegex(DeliveryError, "SIZE_LIMIT"):
            self.build(max_bytes=2)
        self.path.unlink()
        with self.assertRaisesRegex(DeliveryError, "MISSING"):
            self.build()

    def test_duplicate_names_get_unique_safe_archive_paths(self):
        self.store.insert("artifacts", {**self.artifact, "id": "artifact-b", "filename": "../../map.pdf", "product_id": "../unsafe"})
        with zipfile.ZipFile(self.build()) as archive:
            entries = json.loads(archive.read("manifest.json"))["artifacts"]
            names = [entry["archive_path"] for entry in entries]
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(".." not in name.split("/") for name in names))
