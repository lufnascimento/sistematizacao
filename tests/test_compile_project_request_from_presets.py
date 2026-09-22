from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import compile_project_request_from_presets as compiler
from scripts import verify_preset_request_lineage as lineage
from scripts.project_request import load_project_request
from scripts.validate_project_inputs import ContractError


class ProjectPresetCompilationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_path = compiler.DEFAULT_PACKAGE
        cls.package = compiler.load_json(cls.package_path)
        cls.base_path = compiler.resolve_path(
            cls.package["sources"]["base_request"]["path"], cls.package_path
        )
        cls.base_request = compiler.load_json(cls.base_path)

    def test_compilation_preserves_project_facts_and_only_adds_safe_e0_values(self):
        compiled, manifest = compiler.compile_request(
            self.package_path,
            write_outputs=False,
        )
        base_by_id = {
            item["parameter_id"]: item for item in self.base_request["parameter_values"]
        }
        compiled_by_id = {
            item["parameter_id"]: item for item in compiled["parameter_values"]
        }
        for parameter_id, base_record in base_by_id.items():
            self.assertEqual(base_record, compiled_by_id[parameter_id])

        self.assertEqual(
            {
                "e0.maneuver_time_s",
                "e0.reference_alert_grade_pct",
                "e0.nominal_field_speed_kmh",
                "e0.yield_proxy_t_ha",
                "objectives.pareto_representative_count",
            },
            set(manifest["materialized_parameter_ids"]),
        )
        for parameter_id in manifest["materialized_parameter_ids"]:
            record = compiled_by_id[parameter_id]
            self.assertIn(record["parameter_class"], {"E0_ASSUMPTION", "OPTIMIZER"})
            self.assertEqual(
                ["PRESET_E0_ONLY", "PROJECT_EVIDENCE_UNRESOLVED"],
                record["quality_flags"],
            )
        self.assertEqual(0, manifest["summary"]["project_evidence_resolved_by_compiler"])
        self.assertEqual(0, manifest["summary"]["project_evidence_resolved_by_selection"])
        self.assertEqual(6, manifest["summary"]["custom_selection_parameter_count"])
        self.assertEqual(6, manifest["summary"]["custom_selection_confirmed_by_base_count"])
        self.assertEqual(0, manifest["summary"]["auto_materialized_fail_closed_count"])
        self.assertEqual([], manifest["provenance_breakdown"]["fail_closed_materialized_parameter_ids"])

    def test_client_declared_no_power_remains_authoritative_and_non_blocking(self):
        compiled, manifest = compiler.compile_request(
            self.package_path,
            write_outputs=False,
        )
        power = next(
            item for item in compiled["parameter_values"]
            if item["parameter_id"] == "constraints.overhead_power_line_inventory_status"
        )
        self.assertEqual("DECLARED_NONE", power["value"])
        self.assertEqual("example-declaration-no-power-v1", power["provenance"]["source_ref"])
        self.assertEqual("DECLARED_NONE", compiled["constraint_inventory"]["overhead_power_line"]["status"])
        self.assertEqual("DECLARED_NONE", manifest["request_validation"]["power_inventory_status"])
        decision = next(
            item for item in manifest["parameter_decisions"]
            if item["parameter_id"] == "constraints.overhead_power_line_inventory_status"
        )
        self.assertEqual("PRESERVE_BASE_REQUEST", decision["decision"])
        self.assertEqual("MATCHES_PRESET_PROPOSAL", decision["relation"])

    def test_compiled_output_is_a_valid_request_and_has_engine_join_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            compiled, manifest = compiler.compile_request(
                self.package_path,
                output_request_path=request_path,
                output_manifest_path=manifest_path,
            )
            resolved = load_project_request(request_path)
            self.assertEqual(compiled["request_id"], resolved.request["request_id"])
            digest = hashlib.sha256(request_path.read_bytes()).hexdigest()
            self.assertEqual(digest, manifest["output_request"]["sha256"])
            self.assertEqual(digest, manifest["engine_lineage_join"]["request_sha256"])
            self.assertEqual("generation_request.request_sha256", manifest["engine_lineage_join"]["e0_manifest_pointer"])
            self.assertEqual("project_request_ref.sha256", manifest["engine_lineage_join"]["cf0_manifest_pointer"])

    def test_stale_resolution_is_rejected_even_when_its_file_hash_is_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolution = compiler.load_json(
                compiler.resolve_path(
                    self.package["sources"]["preset_resolution"]["path"],
                    self.package_path,
                )
            )
            resolution["warnings"].append("tampered-stale-resolution")
            stale_path = root / "stale-resolution.json"
            stale_path.write_text(json.dumps(resolution), encoding="utf-8")
            package = copy.deepcopy(self.package)
            package["sources"]["preset_resolution"] = {
                "path": str(stale_path),
                "sha256": hashlib.sha256(stale_path.read_bytes()).hexdigest(),
            }
            package_path = root / "package.json"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "Stored preset resolution is stale"):
                compiler.compile_request(package_path, write_outputs=False)

    def test_source_hash_drift_is_rejected_before_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = copy.deepcopy(self.package)
            package["sources"]["preset_selection"]["sha256"] = "0" * 64
            package_path = root / "package.json"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "Referenced file hash changed"):
                compiler.compile_request(package_path, write_outputs=False)

    def test_policy_cannot_enable_reference_or_evidence_materialization(self):
        package = copy.deepcopy(self.package)
        package["policy"]["reference_materialization"] = "ALWAYS"
        with self.assertRaisesRegex(ContractError, "safety policy cannot be relaxed"):
            compiler.validate_package(package)

    def test_rfc3339_datetime_with_timezone_is_required(self):
        package = copy.deepcopy(self.package)
        package["created_at"] = "2026-08-24"
        with self.assertRaisesRegex(ContractError, "RFC 3339 date-time with timezone"):
            compiler.validate_package(package)

    def test_outputs_cannot_collide_with_each_other_or_any_source(self):
        source_digest = hashlib.sha256(self.base_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ContractError, "collides with protected source base_request"):
            compiler.compile_request(
                self.package_path,
                output_request_path=self.base_path,
                output_manifest_path=self.base_path.parent / "unreachable-manifest.json",
                write_outputs=False,
            )
        self.assertEqual(source_digest, hashlib.sha256(self.base_path.read_bytes()).hexdigest())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "same.json"
            with self.assertRaisesRegex(ContractError, "must be different files"):
                compiler.compile_request(
                    self.package_path,
                    output_request_path=output,
                    output_manifest_path=output,
                    write_outputs=False,
                )

    def test_outputs_are_staged_with_unique_names_and_manifest_is_published_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            replacements: list[Path] = []
            original_replace = Path.replace

            def observed_replace(source: Path, target: Path) -> Path:
                target = Path(target)
                if target.name == request_path.name:
                    staged_requests = list(root.glob(".request.json.*.tmp"))
                    staged_manifests = list(root.glob(".manifest.json.*.tmp"))
                    self.assertEqual(1, len(staged_requests))
                    self.assertEqual(1, len(staged_manifests))
                    request_token = staged_requests[0].name.split(".")[-2]
                    manifest_token = staged_manifests[0].name.split(".")[-2]
                    self.assertEqual(request_token, manifest_token)
                    self.assertRegex(request_token, r"^[0-9a-f]{32}$")
                replacements.append(target)
                return original_replace(source, target)

            with mock.patch.object(Path, "replace", new=observed_replace):
                compiler.compile_request(
                    self.package_path,
                    output_request_path=request_path,
                    output_manifest_path=manifest_path,
                )
            self.assertEqual([request_path.name, manifest_path.name], [item.name for item in replacements])
            self.assertEqual([], list(root.glob(".*.tmp")))

    def test_custom_confirmation_requires_matching_selection_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = copy.deepcopy(self.package)
            source_paths = {
                key: compiler.resolve_path(record["path"], self.package_path)
                for key, record in package["sources"].items()
            }
            selection = compiler.load_json(source_paths["preset_selection"])
            custom = next(
                item for item in selection["custom_parameter_values"]
                if item["parameter_id"] == "scope.target_field_ids"
            )
            custom["provenance"]["source_ref"] = "different-unconfirmed-source"
            selection_path = root / "selection.json"
            selection_path.write_bytes(
                (json.dumps(selection, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            resolution = compiler.preset_resolver.resolve(
                selection_path,
                source_paths["parameter_catalog"],
                source_paths["acquisition_catalog"],
                source_paths["reference_model_catalog"],
                source_paths["preset_catalog"],
            )
            resolution_path = root / "resolution.json"
            resolution_path.write_bytes(
                (json.dumps(resolution, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            package["sources"]["preset_selection"] = {
                "path": str(selection_path),
                "sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
            }
            package["sources"]["preset_resolution"] = {
                "path": str(resolution_path),
                "sha256": hashlib.sha256(resolution_path.read_bytes()).hexdigest(),
            }
            package_path = root / "package.json"
            package_path.write_bytes(
                (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            _, manifest = compiler.compile_request(package_path, write_outputs=False)
            self.assertEqual(5, manifest["summary"]["custom_selection_confirmed_by_base_count"])
            decision = next(
                item for item in manifest["parameter_decisions"]
                if item["parameter_id"] == "scope.target_field_ids"
            )
            self.assertFalse(decision["custom_provenance_matches_base"])

    def test_machine_readable_schemas_declare_evidence_neutral_contract(self):
        package_schema = compiler.load_json(
            compiler.REPO / "schemas" / "project-preset-compilation-package.schema.json"
        )
        manifest_schema = compiler.load_json(
            compiler.REPO / "schemas" / "project-preset-compilation-manifest.schema.json"
        )
        self.assertEqual("NEVER", package_schema["properties"]["policy"]["properties"]["unresolved_evidence_materialization"]["const"])
        self.assertEqual(0, manifest_schema["properties"]["summary"]["properties"]["project_evidence_resolved_by_compiler"]["const"])
        self.assertFalse(manifest_schema["properties"]["release_boundary"]["properties"]["guidance_authorized"]["const"])
        self.assertFalse(manifest_schema["properties"]["sources"]["additionalProperties"])
        self.assertEqual(compiler.SOURCE_KEYS, set(manifest_schema["properties"]["sources"]["required"]))
        self.assertEqual(
            "NEVER",
            manifest_schema["properties"]["policy"]["properties"]["reference_materialization"]["const"],
        )
        self.assertFalse(manifest_schema["$defs"]["parameter_decision"]["additionalProperties"])

    def test_lineage_verifier_recompiles_and_rejects_changed_user_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            compiler.compile_request(
                self.package_path,
                output_request_path=request_path,
                output_manifest_path=manifest_path,
            )
            request = compiler.load_json(request_path)
            fact = next(
                item for item in request["parameter_values"]
                if item["parameter_id"] == "qa.constraint_revision"
            )
            fact["value"] = "forged-evidence-revision"
            request_payload = (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            request_path.write_bytes(request_payload)
            manifest = compiler.load_json(manifest_path)
            manifest["output_request"]["sha256"] = hashlib.sha256(request_payload).hexdigest()
            manifest["output_request"]["canonical_sha256"] = compiler.canonical_sha256(request)
            manifest["engine_lineage_join"]["request_sha256"] = manifest["output_request"]["sha256"]
            manifest_path.write_bytes(
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            with self.assertRaisesRegex(ContractError, "deterministic recompilation"):
                lineage.verify(manifest_path)

    def test_lineage_verifier_uses_package_pinned_catalog_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            compiler.compile_request(
                self.package_path,
                output_request_path=request_path,
                output_manifest_path=manifest_path,
            )
            expected_catalog = compiler.resolve_path(
                self.package["sources"]["parameter_catalog"]["path"], self.package_path
            )
            expected_schema = compiler.resolve_path(
                self.package["sources"]["request_schema"]["path"], self.package_path
            )
            with mock.patch.object(
                lineage,
                "load_project_request",
                wraps=lineage.load_project_request,
            ) as loader:
                lineage.verify(manifest_path)
            self.assertEqual(expected_catalog, loader.call_args.kwargs["catalog_path"])
            self.assertEqual(expected_schema, loader.call_args.kwargs["schema_path"])

    def test_lineage_verifier_accepts_matching_e0_and_cf0_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            compilation_manifest_path = root / "compilation.json"
            _, manifest = compiler.compile_request(
                self.package_path,
                output_request_path=request_path,
                output_manifest_path=compilation_manifest_path,
            )
            request_id = manifest["output_request"]["request_id"]
            request_sha256 = manifest["output_request"]["sha256"]
            e0_path = root / "e0.json"
            cf0_path = root / "cf0.json"
            e0_path.write_text(
                json.dumps(
                    {
                        "generation_request": {
                            "mode": "VALIDATED_PROJECT_REQUEST",
                            "request_id": request_id,
                            "request_sha256": request_sha256,
                        }
                    }
                ),
                encoding="utf-8",
            )
            cf0_path.write_text(
                json.dumps(
                    {
                        "project_request_ref": {"id": request_id, "sha256": request_sha256},
                        "inputs": {"project_request": {"sha256": request_sha256}},
                    }
                ),
                encoding="utf-8",
            )
            report = lineage.verify(
                compilation_manifest_path,
                e0_manifest_path=e0_path,
                cf0_manifest_path=cf0_path,
            )
            self.assertEqual("PASS", report["status"])
            self.assertEqual({"E0", "CF0"}, set(report["engine_manifests"]))

    def test_lineage_verifier_rejects_an_engine_built_from_another_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            compilation_manifest_path = root / "compilation.json"
            compiler.compile_request(
                self.package_path,
                output_request_path=request_path,
                output_manifest_path=compilation_manifest_path,
            )
            e0_path = root / "e0.json"
            e0_path.write_text(
                json.dumps(
                    {
                        "generation_request": {
                            "mode": "VALIDATED_PROJECT_REQUEST",
                            "request_id": "another-request",
                            "request_sha256": "0" * 64,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "E0 manifest request_id differs"):
                lineage.verify(compilation_manifest_path, e0_manifest_path=e0_path)


if __name__ == "__main__":
    unittest.main()
