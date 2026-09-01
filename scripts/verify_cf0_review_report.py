#!/usr/bin/env python3
"""Verify integrity and release claims of a published CF0 review report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageStat

try:
    from pypdf import PdfReader
except ModuleNotFoundError:
    _pdf_sites = [
        *Path.home().glob(
            "AppData/Local/Packages/PythonSoftwareFoundation.Python.*/LocalCache/local-packages/Python*/site-packages"
        ),
        *Path.home().glob("AppData/Roaming/Python/Python*/site-packages"),
        *Path.home().glob("AppData/Local/Programs/Python/Python*/Lib/site-packages"),
    ]
    for _pdf_site in _pdf_sites:
        sys.path.append(str(_pdf_site))
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError:
            continue
        break
    else:
        raise ModuleNotFoundError(
            "pypdf is required for searchable-text and media-box verification."
        )

try:
    from verify_continuous_family import verify as verify_cf0_package
except ModuleNotFoundError:
    from scripts.verify_continuous_family import verify as verify_cf0_package


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_MANIFEST = ROOT / "dataset" / "derived" / "cf0_review_report_manifest.json"
DEFAULT_CF0_SCHEMA = ROOT / "schemas" / "continuous-family-stage.schema.json"

REPORT_SCHEMA_VERSION = "1.2.0"
SOURCE_SCHEMA_VERSION = "1.2.0"
SEAL = "CF0 PROTÓTIPO GEOMÉTRICO | NÃO USAR PARA GUIAMENTO"
EXPECTED_SOURCE_LAYERS = (
    "continuous_rows",
    "diagnostic_rows",
    "family_summary",
    "hydraulic_precheck",
)
EXPECTED_BOUNDARY_CONTRACT = {
    "solve_mask_rasterization": "ALL_TOUCHED_SUPERCOVER",
    "contour_extrapolation_method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
    "contour_extrapolation_halo_cells": 1,
    "contour_gradient_lsq_max_radius_cells": 2,
    "contour_gradient_lsq_max_relative_residual": 0.30,
    "contour_gradient_lsq_max_condition_number": 100.0,
    "contour_gradient_lsq_minimum_neighbor_count": 3,
    "contour_gradient_lsq_required_rank": 2,
    "contour_gradient_component_connectivity": 4,
    "endpoint_extension_mode": "TANGENT_ONLY_FAIL_CLOSED",
}
EXPECTED_PHASE_CONTRACT = {
    "gauge_method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
    "gauge_anchor_value_m": 0,
    "solve_mask_required_component_count": 1,
    "solve_mask_component_connectivity": 4,
}
EXPECTED_PHASE_OFFSET_CONTRACT = {
    "phase_level_equation": "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
    "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
    "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
    "phase_offset_search_revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
    "phase_offset_fractions": [0, 0.25, 0.5, 0.75],
    "phase_offset_selection_rule": (
        "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
        "MIN_COVERAGE_ERROR_MIN_OFFSET"
    ),
    "phase_offset_search_scope": (
        "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE"
    ),
}
EXPECTED_BLOCKER_CONTRACT = {
    "spline_fit_failure": "ROW_SPLINE_FIT_FAILED",
    "halo_gradient_failure": "PHASE_HALO_GRADIENT_UNESTIMABLE",
    "solve_mask_failure": "WORK_BLOCK_SOLVE_MASK_DISCONNECTED",
}
EXPECTED_SPLINE_REPRESENTATION_CONTRACT = {
    "spline_representation_method": "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
    "spline_representation_tolerance_fraction": 0.10,
}
EXPECTED_SPLINE_MAX_DEVIATION_M = 0.12
EXPECTED_ROADMAP = {
    "C1_CURVA_EMBUTIDA": "NOT_GENERATED",
    "C2_BASE_LARGA_PASSANTE": "NOT_GENERATED",
    "C3_ESD": "NOT_GENERATED",
}
REQUIRED_GLOBAL_TOKENS = (
    "CF0A_CONSERVACAO",
    "CF0B_EQUILIBRIO",
    "CF0C_OPERACAO",
    "HYDRAULIC_UNCONFIRMED",
    "C1",
    "C2",
    "C3",
    "NÃO GERADO",
    "diagnostic_rows",
    "NÃO APROVADO",
    "ALL_TOUCHED_SUPERCOVER",
    "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
    "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
    "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
    "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
    "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
    "ROW_INDEX_IS_NOT_OPERATIONAL_ROUTE",
    "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
    "contour_extrapolation_halo_cells=1",
    "contour_gradient_lsq_max_relative_residual=0.30",
    "contour_gradient_lsq_max_condition_number=100.0",
    "TANGENT_ONLY_FAIL_CLOSED",
    "ROW_SPLINE_FIT_FAILED",
    "PHASE_HALO_GRADIENT_UNESTIMABLE",
    "WORK_BLOCK_SOLVE_MASK_DISCONNECTED",
    "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
    "spline_representation_tolerance_fraction=0.10",
    "spline_max_deviation_m=0.12",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReportContractError(RuntimeError):
    """Raised when a CF0 report package is incomplete or misleading."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportContractError(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object.")
    return value


def require_array(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    require(isinstance(value, list), f"{label} must be an array.")
    if nonempty:
        require(bool(value), f"{label} must not be empty.")
    return value


def require_keys(
    node: dict[str, Any],
    label: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    require(required_set <= set(node), f"{label} missing keys: {sorted(required_set - set(node))}")
    require(set(node) <= allowed, f"{label} has unexpected keys: {sorted(set(node) - allowed)}")


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    root_candidate = (ROOT / path).resolve()
    local_candidate = (manifest_path.parent / path).resolve()
    return root_candidate if root_candidate.exists() or not local_candidate.exists() else local_candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_file_record(record_value: Any, label: str, report_manifest_path: Path) -> Path:
    record = require_object(record_value, label)
    require_keys(record, label, {"path", "size_bytes", "sha256"}, {"page"})
    require(isinstance(record["path"], str) and record["path"], f"{label}.path is invalid.")
    require(isinstance(record["size_bytes"], int) and record["size_bytes"] > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(record["sha256"], str) and SHA256_PATTERN.fullmatch(record["sha256"]) is not None, f"{label}.sha256 is invalid.")
    path = resolve_path(record["path"], report_manifest_path)
    require(path.is_file(), f"{label} does not exist: {path}")
    require(path.stat().st_size == record["size_bytes"], f"{label} size mismatch.")
    require(sha256_file(path) == record["sha256"], f"{label} SHA-256 mismatch.")
    return path


def build_candidate_phase_audit(source_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in source_manifest["candidates"]:
        records.append(
            {
                "candidate_id": candidate["candidate_id"],
                "field_id": candidate["field_id"],
                "work_block_id": candidate["work_block_id"],
                "geometric_status": candidate["geometric_status"],
                "phase_offset_m": candidate["phase_offset_m"],
                "phase_offset_fraction": candidate["phase_offset_fraction"],
                "phase_offset_role": candidate["phase_offset_role"],
                "phase_offset_selection": candidate["phase_offset_selection"],
                "contour_extrapolation_qa": candidate["contour_extrapolation_qa"],
                "solve_mask_component_count": candidate["metrics"]["solver_gate_metrics"][
                    "solve_mask_component_count"
                ],
                "row_count": candidate["row_count"],
                "diagnostic_row_count": candidate["diagnostic_row_count"],
                "blocker_codes": candidate["blocker_codes"],
            }
        )
    return records


def validate_candidate_phase_audit(value: Any) -> list[dict[str, Any]]:
    records = require_array(value, "candidate_phase_audit", nonempty=True)
    identities: set[tuple[str, str, str]] = set()
    for index, record_value in enumerate(records):
        label = f"candidate_phase_audit[{index}]"
        record = require_object(record_value, label)
        require_keys(
            record,
            label,
            {
                "candidate_id",
                "field_id",
                "work_block_id",
                "geometric_status",
                "phase_offset_m",
                "phase_offset_fraction",
                "phase_offset_role",
                "phase_offset_selection",
                "contour_extrapolation_qa",
                "solve_mask_component_count",
                "row_count",
                "diagnostic_row_count",
                "blocker_codes",
            },
        )
        identity = (
            str(record["candidate_id"]),
            str(record["field_id"]),
            str(record["work_block_id"]),
        )
        require(identity not in identities, f"Duplicate candidate phase audit identity: {identity}")
        identities.add(identity)
        require(record["geometric_status"] in {"GEOMETRIC_PASS", "NO_FEASIBLE_FAMILY"}, f"{label} has invalid geometric status.")
        require(record["phase_offset_role"] in {"SELECTED_PASS", "DIAGNOSTIC_ONLY", "NOT_EVALUATED"}, f"{label} has invalid phase offset role.")
        require(isinstance(record["row_count"], int) and record["row_count"] >= 0, f"{label}.row_count is invalid.")
        require(isinstance(record["diagnostic_row_count"], int) and record["diagnostic_row_count"] >= 0, f"{label}.diagnostic_row_count is invalid.")
        blockers = require_array(record["blocker_codes"], f"{label}.blocker_codes")
        require("C2_SPLINE_FAILED" not in blockers, f"{label} uses the retired spline blocker.")

        selection = require_object(record["phase_offset_selection"], f"{label}.phase_offset_selection")
        require_keys(
            selection,
            f"{label}.phase_offset_selection",
            {
                "revision",
                "selection_rule",
                "search_scope",
                "selection_status",
                "selection_key_order",
                "coverage_proxy_target_ratio",
                "phase_offset_role",
                "selected_phase_offset_fraction",
                "selected_phase_offset_m",
                "diagnostic_phase_offset_fraction",
                "diagnostic_phase_offset_m",
                "diagnostic_selection_rule",
                "attempted_offsets",
                "row_spacing_m",
                "level_equation",
                "phase_level_index_scope",
                "row_index_definition",
                "phase_gauge",
            },
        )
        require(selection["revision"] == EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_search_revision"], f"{label} phase-offset revision differs.")
        require(selection["selection_rule"] == EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_selection_rule"], f"{label} phase-offset selection rule differs.")
        require(selection["search_scope"] == EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_search_scope"], f"{label} phase-offset search scope differs.")
        require(selection["level_equation"] == EXPECTED_PHASE_OFFSET_CONTRACT["phase_level_equation"], f"{label} phase-level equation differs.")
        require(selection["phase_level_index_scope"] == EXPECTED_PHASE_OFFSET_CONTRACT["phase_level_index_scope"], f"{label} phase-level scope differs.")
        require(selection["row_index_definition"] == EXPECTED_PHASE_OFFSET_CONTRACT["row_index_definition"], f"{label} row-index definition differs.")
        require(selection["phase_offset_role"] == record["phase_offset_role"], f"{label} phase offset role disagrees with selection.")
        trials = require_array(selection["attempted_offsets"], f"{label}.attempted_offsets")
        trial_objects = [
            require_object(trial, f"{label}.attempted_offsets[{trial_index}]")
            for trial_index, trial in enumerate(trials)
        ]
        status = selection["selection_status"]
        if status in {"SELECTED_GEOMETRIC_PASS", "NO_FEASIBLE_PHASE_OFFSET"}:
            require(
                [trial.get("phase_offset_fraction") for trial in trial_objects]
                == EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_fractions"],
                f"{label} must retain the four configured phase-offset trials in order.",
            )
        else:
            require(status == "NOT_EVALUATED_WORK_BLOCK_BELOW_GRID_SUPPORT" and not trials, f"{label} has an invalid phase-offset selection status.")
        if status == "SELECTED_GEOMETRIC_PASS":
            require(
                record["phase_offset_role"] == "SELECTED_PASS"
                and record["phase_offset_fraction"] == selection["selected_phase_offset_fraction"]
                and record["phase_offset_m"] == selection["selected_phase_offset_m"],
                f"{label} selected phase offset is inconsistent.",
            )
        elif status == "NO_FEASIBLE_PHASE_OFFSET":
            require(
                record["phase_offset_role"] == "DIAGNOSTIC_ONLY"
                and record["phase_offset_fraction"] == selection["diagnostic_phase_offset_fraction"]
                and record["phase_offset_m"] == selection["diagnostic_phase_offset_m"],
                f"{label} diagnostic phase offset is inconsistent.",
            )
        else:
            require(
                record["phase_offset_role"] == "NOT_EVALUATED"
                and record["phase_offset_fraction"] is None
                and record["phase_offset_m"] is None,
                f"{label} non-evaluated phase offset is inconsistent.",
            )

        gauge = require_object(selection["phase_gauge"], f"{label}.phase_gauge")
        require(gauge.get("method") in {EXPECTED_PHASE_CONTRACT["gauge_method"], "NOT_APPLICABLE_WORK_BLOCK_BELOW_GRID_SUPPORT"}, f"{label} has an invalid phase gauge.")
        halo = require_object(record["contour_extrapolation_qa"], f"{label}.contour_extrapolation_qa")
        halo_contract = {
            "method": halo.get("method"),
            "halo_cells": halo.get("halo_cells"),
            "gradient_lsq_max_radius_cells": halo.get("gradient_lsq_max_radius_cells"),
            "gradient_lsq_max_relative_residual": halo.get("gradient_lsq_max_relative_residual"),
            "gradient_lsq_max_condition_number": halo.get("gradient_lsq_max_condition_number"),
            "gradient_lsq_minimum_neighbor_count": halo.get("gradient_lsq_minimum_neighbor_count"),
            "gradient_lsq_required_rank": halo.get("gradient_lsq_required_rank"),
            "gradient_component_connectivity": halo.get("gradient_component_connectivity"),
        }
        expected_halo_contract = {
            "method": EXPECTED_BOUNDARY_CONTRACT["contour_extrapolation_method"],
            "halo_cells": EXPECTED_BOUNDARY_CONTRACT["contour_extrapolation_halo_cells"],
            "gradient_lsq_max_radius_cells": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_radius_cells"],
            "gradient_lsq_max_relative_residual": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_relative_residual"],
            "gradient_lsq_max_condition_number": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_condition_number"],
            "gradient_lsq_minimum_neighbor_count": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_minimum_neighbor_count"],
            "gradient_lsq_required_rank": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_required_rank"],
            "gradient_component_connectivity": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_component_connectivity"],
        }
        require(halo_contract == expected_halo_contract, f"{label} halo LSQ contract differs.")
        if halo.get("gradient_estimation_status") == "FAIL_CLOSED":
            require(EXPECTED_BLOCKER_CONTRACT["halo_gradient_failure"] in blockers, f"{label} omits the halo-gradient blocker.")
        component_count = record["solve_mask_component_count"]
        if component_count is not None and component_count != 1:
            require(EXPECTED_BLOCKER_CONTRACT["solve_mask_failure"] in blockers, f"{label} omits the disconnected-mask blocker.")
        if record["geometric_status"] == "GEOMETRIC_PASS":
            require(record["phase_offset_role"] == "SELECTED_PASS", f"{label} PASS candidate lacks selected phase offset.")
            require(record["row_count"] > 0 and record["diagnostic_row_count"] == 0 and not blockers, f"{label} PASS counts/blockers are invalid.")
            require(component_count == 1, f"{label} PASS candidate solve mask is not connected.")
            require(halo.get("gradient_estimation_status") == "PASS", f"{label} PASS candidate halo did not pass.")
        else:
            require(record["row_count"] == 0 and bool(blockers), f"{label} rejected candidate has published rows or no blocker.")
    return records


def validate_manifest_shape(manifest_value: Any) -> dict[str, Any]:
    manifest = require_object(manifest_value, "report manifest")
    require_keys(
        manifest,
        "report manifest",
        {
            "schema_version",
            "manifest_type",
            "release",
            "generated_at",
            "release_seal",
            "hydraulic_status",
            "guidance_authorized",
            "legacy_pdfs_read",
            "source_package",
            "source_layers",
            "phase_contract",
            "phase_offset_contract",
            "boundary_contract",
            "spline_representation_contract",
            "blocker_contract",
            "candidate_phase_audit",
            "diagnostic_rows",
            "excluded_components",
            "roadmap",
            "page_count",
            "pages",
            "output_integrity",
            "preflight",
            "generator",
        },
    )
    require(manifest["schema_version"] == REPORT_SCHEMA_VERSION, "Unsupported report schema_version.")
    require(manifest["manifest_type"] == "CF0_REVIEW_REPORT", "Unexpected report manifest_type.")
    require(manifest["release"] == "CF0_GEOMETRIC_SCREENING_REVIEW", "Unexpected report release.")
    require(manifest["release_seal"] == SEAL, "Mandatory CF0 report seal differs.")
    require(manifest["hydraulic_status"] == "HYDRAULIC_UNCONFIRMED", "Report cannot approve hydraulics.")
    require(manifest["guidance_authorized"] is False, "Report cannot authorize guidance.")
    require(manifest["legacy_pdfs_read"] is False, "Report must not depend on legacy PDFs.")
    require(
        manifest["source_layers"] == list(EXPECTED_SOURCE_LAYERS),
        "CF0 1.2 report must declare exactly the four source layers in contract order.",
    )
    phase = require_object(manifest["phase_contract"], "phase_contract")
    require_keys(phase, "phase_contract", EXPECTED_PHASE_CONTRACT)
    require(phase == EXPECTED_PHASE_CONTRACT, "Report phase contract differs from CF0 1.2.")
    offsets = require_object(manifest["phase_offset_contract"], "phase_offset_contract")
    require_keys(offsets, "phase_offset_contract", EXPECTED_PHASE_OFFSET_CONTRACT)
    require(offsets == EXPECTED_PHASE_OFFSET_CONTRACT, "Report phase-offset contract differs from CF0 1.2.")
    boundary = require_object(manifest["boundary_contract"], "boundary_contract")
    require_keys(boundary, "boundary_contract", EXPECTED_BOUNDARY_CONTRACT)
    require(
        boundary == EXPECTED_BOUNDARY_CONTRACT,
        "Report boundary contract differs from the fail-closed CF0 contract.",
    )
    spline_representation = require_object(
        manifest["spline_representation_contract"],
        "spline_representation_contract",
    )
    require_keys(
        spline_representation,
        "spline_representation_contract",
        EXPECTED_SPLINE_REPRESENTATION_CONTRACT,
    )
    require(
        spline_representation == EXPECTED_SPLINE_REPRESENTATION_CONTRACT,
        "Report spline representation contract differs from the CF0 contract.",
    )
    blockers = require_object(manifest["blocker_contract"], "blocker_contract")
    require_keys(blockers, "blocker_contract", EXPECTED_BLOCKER_CONTRACT)
    require(blockers == EXPECTED_BLOCKER_CONTRACT, "Report blocker contract differs from CF0 1.2.")
    validate_candidate_phase_audit(manifest["candidate_phase_audit"])
    require(manifest["roadmap"] == EXPECTED_ROADMAP, "C1/C2/C3 roadmap must remain NOT_GENERATED.")

    diagnostics = require_object(manifest["diagnostic_rows"], "diagnostic_rows")
    require_keys(
        diagnostics,
        "diagnostic_rows",
        {
            "layer_required",
            "status",
            "review_status",
            "guidance_status",
            "feature_count",
            "release_authorized",
        },
    )
    require(diagnostics["layer_required"] is True, "diagnostic_rows must be a mandatory source layer.")
    require(diagnostics["status"] == "PRESENT_NOT_APPROVED", "diagnostic_rows layer cannot be reported absent or approved.")
    require(diagnostics["review_status"] == "NOT_APPROVED", "Diagnostic rows must remain NOT_APPROVED.")
    require(diagnostics["guidance_status"] == "NOT_AUTHORIZED", "Diagnostic rows must remain NOT_AUTHORIZED.")
    require(isinstance(diagnostics["feature_count"], int) and diagnostics["feature_count"] >= 0, "Invalid diagnostic_rows count.")
    require(diagnostics["release_authorized"] is False, "Diagnostic rows cannot be approved.")

    excluded = require_object(manifest["excluded_components"], "excluded_components")
    require_keys(excluded, "excluded_components", {"status", "component_count", "total_area_m2", "filter"})
    require(excluded["status"] == "AUDITED_FROM_CF0_MANIFEST", "Excluded components are not marked auditable.")
    require(isinstance(excluded["component_count"], int) and excluded["component_count"] >= 0, "Invalid excluded component count.")
    require(
        isinstance(excluded["total_area_m2"], (int, float))
        and not isinstance(excluded["total_area_m2"], bool)
        and math.isfinite(float(excluded["total_area_m2"]))
        and float(excluded["total_area_m2"]) >= 0,
        "Invalid excluded component area.",
    )
    filter_record = require_object(excluded["filter"], "excluded_components.filter")
    require_keys(
        filter_record,
        "excluded_components.filter",
        {
            "revision",
            "grid_cell_estimate_method",
            "minimum_grid_cell_count",
            "effective_minimum_area_m2",
            "width_method",
            "minimum_width_row_spacing_factor",
            "effective_minimum_width_m",
        },
    )

    pages = require_array(manifest["pages"], "pages", nonempty=True)
    require(manifest["page_count"] == len(pages), "page_count disagrees with pages.")
    numbers: list[int] = []
    slugs: list[str] = []
    kinds: list[str] = []
    for index, page_value in enumerate(pages, start=1):
        page = require_object(page_value, f"pages[{index - 1}]")
        require_keys(page, f"pages[{index - 1}]", {"number", "title", "slug", "kind", "payload"})
        require(page["number"] == index, "Report page numbers must be contiguous and one-based.")
        require(isinstance(page["title"], str) and page["title"], "Report page title is empty.")
        require(isinstance(page["slug"], str) and page["slug"], "Report page slug is empty.")
        numbers.append(page["number"])
        slugs.append(page["slug"])
        kinds.append(page["kind"])
    require(len(slugs) == len(set(slugs)), "Report page slugs are duplicated.")
    for required_kind in {"cover", "scope", "method", "phase_contract", "phase_audit", "extraction_contract", "domains", "domain_exclusions", "qa", "block_map", "block_rasters", "gates", "hydraulic", "limitations", "roadmap", "traceability"}:
        require(required_kind in kinds, f"Required report page kind is absent: {required_kind}")
    return manifest


def inspect_pdf(pdf_path: Path, pages: list[dict[str, Any]]) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    require(len(reader.pages) == len(pages), "PDF page count disagrees with report manifest.")
    page_texts = [page.extract_text() or "" for page in reader.pages]
    combined_text = "\n".join(page_texts)
    media_boxes = []
    for index, pdf_page in enumerate(reader.pages, start=1):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        a4_landscape = width > height and abs(width - 841.68) <= 2.0 and abs(height - 595.44) <= 2.0
        require(a4_landscape, f"PDF page {index} is not A4 landscape.")
        text_value = page_texts[index - 1]
        require("CF0 PROTÓTIPO GEOMÉTRICO" in text_value, f"Page {index} lacks CF0 seal.")
        require("NÃO USAR PARA GUIAMENTO" in text_value, f"Page {index} lacks guidance warning.")
        require("HYDRAULIC_UNCONFIRMED" in text_value, f"Page {index} lacks hydraulic warning.")
        require(pages[index - 1]["title"] in text_value, f"Page {index} title is not searchable.")
        media_boxes.append({"page": index, "width_pt": round(width, 3), "height_pt": round(height, 3), "a4_landscape": True})
    token_checks = {token: token in combined_text for token in REQUIRED_GLOBAL_TOKENS}
    require(all(token_checks.values()), f"PDF lacks required tokens: {[key for key, value in token_checks.items() if not value]}")
    return {
        "page_count": len(reader.pages),
        "all_pages_a4_landscape": True,
        "every_page_has_release_seal": True,
        "every_page_has_hydraulic_warning": True,
        "required_tokens": token_checks,
        "media_boxes": media_boxes,
    }


def inspect_assets(
    records: list[dict[str, Any]],
    report_manifest_path: Path,
    expected_page_count: int,
) -> dict[str, Any]:
    require(len(records) == expected_page_count, "Asset count disagrees with page_count.")
    page_numbers: list[int] = []
    checks: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        path = validate_file_record(record, f"output_integrity.assets[{index}]", report_manifest_path)
        page = record["page"]
        require(isinstance(page, int) and 1 <= page <= expected_page_count, f"Invalid page number in asset {index}.")
        page_numbers.append(page)
        with Image.open(path) as image:
            require(image.width > image.height, f"Page asset {page} is not landscape.")
            statistics = ImageStat.Stat(image.convert("L"))
            stddev = float(statistics.stddev[0])
            require(math.isfinite(stddev) and stddev > 2.0, f"Page asset {page} is blank.")
            checks.append({"page": page, "width": image.width, "height": image.height, "stddev": round(stddev, 3), "nonblank": True})
    require(sorted(page_numbers) == list(range(1, expected_page_count + 1)), "Asset pages are incomplete or duplicated.")
    return {"asset_count": len(records), "all_nonblank": True, "checks": checks}


def verify_report(report_manifest_path: Path, cf0_schema_path: Path) -> dict[str, Any]:
    require(report_manifest_path.is_file(), f"Report manifest does not exist: {report_manifest_path}")
    manifest = validate_manifest_shape(json.loads(report_manifest_path.read_text(encoding="utf-8")))
    source = require_object(manifest["source_package"], "source_package")
    require_keys(source, "source_package", {"manifest", "schema", "verification", "source_release", "source_stage_status"})
    source_manifest_path = validate_file_record(source["manifest"], "source_package.manifest", report_manifest_path)
    source_manifest = require_object(
        json.loads(source_manifest_path.read_text(encoding="utf-8")),
        "source CF0 manifest",
    )
    require(
        source_manifest.get("schema_version") == SOURCE_SCHEMA_VERSION,
        "Report source is not a CF0 1.2 package.",
    )
    source_layer_counts = require_object(source_manifest.get("layer_counts"), "source CF0 layer_counts")
    require(
        set(source_layer_counts) == set(EXPECTED_SOURCE_LAYERS),
        "Source CF0 package does not declare exactly four contractual layers.",
    )
    diagnostics = manifest["diagnostic_rows"]
    require(
        diagnostics["feature_count"] == source_layer_counts["diagnostic_rows"],
        "Report diagnostic_rows count disagrees with source CF0 manifest.",
    )
    source_domain = require_object(source_manifest.get("domain_assembly"), "source CF0 domain_assembly")
    source_excluded = require_array(source_domain.get("excluded_components"), "source CF0 excluded_components")
    excluded = manifest["excluded_components"]
    require(excluded["component_count"] == len(source_excluded), "Excluded component count disagrees with source CF0 manifest.")
    source_excluded_area = round(sum(float(record["area_m2"]) for record in source_excluded), 6)
    require(
        math.isclose(float(excluded["total_area_m2"]), source_excluded_area, rel_tol=0, abs_tol=1e-6),
        "Excluded component area disagrees with source CF0 manifest.",
    )
    require(
        excluded["filter"] == source_domain.get("work_block_filter"),
        "Excluded component filter disagrees with source CF0 manifest.",
    )
    source_solver = require_object(source_manifest.get("solver_parameters"), "source CF0 solver_parameters")
    source_phase = require_object(source_solver.get("phase"), "source CF0 solver_parameters.phase")
    source_extraction = require_object(source_solver.get("extraction"), "source CF0 solver_parameters.extraction")
    source_phase_contract = {
        key: source_phase.get(key) for key in EXPECTED_PHASE_CONTRACT
    }
    require(
        source_phase_contract == EXPECTED_PHASE_CONTRACT,
        "Source CF0 gauge/solve-mask contract differs from the required values.",
    )
    require(
        manifest["phase_contract"] == source_phase_contract,
        "Report phase contract disagrees with source CF0 manifest.",
    )
    source_phase_offset_contract = {
        key: source_extraction.get(key) for key in EXPECTED_PHASE_OFFSET_CONTRACT
    }
    require(
        source_phase_offset_contract == EXPECTED_PHASE_OFFSET_CONTRACT,
        "Source CF0 phase-offset contract differs from the required values.",
    )
    require(
        manifest["phase_offset_contract"] == source_phase_offset_contract,
        "Report phase-offset contract disagrees with source CF0 manifest.",
    )
    source_boundary_contract = {
        key: source_extraction.get(key) for key in EXPECTED_BOUNDARY_CONTRACT
    }
    require(
        source_boundary_contract == EXPECTED_BOUNDARY_CONTRACT,
        "Source CF0 boundary contract differs from the required fail-closed values.",
    )
    require(
        manifest["boundary_contract"] == source_boundary_contract,
        "Report boundary contract disagrees with source CF0 manifest.",
    )
    source_spline_representation_contract = {
        key: source_extraction.get(key)
        for key in EXPECTED_SPLINE_REPRESENTATION_CONTRACT
    }
    require(
        source_spline_representation_contract
        == EXPECTED_SPLINE_REPRESENTATION_CONTRACT,
        "Source CF0 spline representation contract differs from the required values.",
    )
    require(
        manifest["spline_representation_contract"]
        == source_spline_representation_contract,
        "Report spline representation contract disagrees with source CF0 manifest.",
    )
    source_candidate_phase_audit = build_candidate_phase_audit(source_manifest)
    require(
        manifest["candidate_phase_audit"] == source_candidate_phase_audit,
        "Report candidate phase/halo audit disagrees with source CF0 manifest.",
    )
    source_spline_max_deviation = source_extraction.get("spline_max_deviation_m")
    require(
        isinstance(source_spline_max_deviation, (int, float))
        and not isinstance(source_spline_max_deviation, bool)
        and math.isclose(
            float(source_spline_max_deviation),
            EXPECTED_SPLINE_MAX_DEVIATION_M,
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "Source CF0 spline_max_deviation_m was relaxed beyond 0.12 m.",
    )
    declared_schema_path = validate_file_record(source["schema"], "source_package.schema", report_manifest_path)
    require(sha256_file(declared_schema_path) == sha256_file(cf0_schema_path), "Requested CF0 schema differs from report source schema.")
    source_verification = verify_cf0_package(source_manifest_path, cf0_schema_path)
    require(source_verification["status"] == "VERIFIED", "Source CF0 package is not verified.")
    require(source_verification["release"] == source["source_release"], "Source release disagrees with report.")
    require(source_verification["stage_status"] == source["source_stage_status"], "Source stage status disagrees with report.")
    require(source_verification["hydraulic_claim"] == "HYDRAULIC_UNCONFIRMED", "Source package claims hydraulic approval.")
    require(source_verification["guidance_authorized"] is False, "Source package authorizes guidance.")

    outputs = require_object(manifest["output_integrity"], "output_integrity")
    require_keys(outputs, "output_integrity", {"pdf", "assets"})
    pdf_path = validate_file_record(outputs["pdf"], "output_integrity.pdf", report_manifest_path)
    assets = require_array(outputs["assets"], "output_integrity.assets", nonempty=True)
    pdf_checks = inspect_pdf(pdf_path, manifest["pages"])
    asset_checks = inspect_assets(assets, report_manifest_path, manifest["page_count"])
    validate_file_record(manifest["generator"], "generator", report_manifest_path)

    recorded_preflight = require_object(manifest["preflight"], "preflight")
    require(recorded_preflight.get("status") == "PASS", "Recorded report preflight did not pass.")
    require(recorded_preflight.get("pdf_page_count") == manifest["page_count"], "Recorded preflight page count disagrees.")
    require(recorded_preflight.get("every_page_has_release_seal") is True, "Recorded preflight lacks every-page seal.")
    require(recorded_preflight.get("every_page_has_hydraulic_warning") is True, "Recorded preflight lacks hydraulic warning.")
    require(recorded_preflight.get("all_page_assets_nonblank") is True, "Recorded preflight has blank assets.")
    recorded_tokens = require_object(recorded_preflight.get("required_tokens"), "preflight.required_tokens")
    require(
        set(recorded_tokens) == set(REQUIRED_GLOBAL_TOKENS)
        and all(recorded_tokens.values()),
        "Recorded preflight does not prove every CF0 1.2 traceability token.",
    )
    return {
        "status": "VERIFIED",
        "release": manifest["release"],
        "report_manifest": str(report_manifest_path.resolve()),
        "pdf": str(pdf_path.resolve()),
        "page_count": manifest["page_count"],
        "release_seal": SEAL,
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "guidance_authorized": False,
        "roadmap": EXPECTED_ROADMAP,
        "source_package": source_verification,
        "pdf_checks": pdf_checks,
        "asset_checks": asset_checks,
    }


def run_self_test() -> dict[str, Any]:
    synthetic_selection = {
        "revision": EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_search_revision"],
        "selection_rule": EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_selection_rule"],
        "search_scope": EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_search_scope"],
        "selection_status": "SELECTED_GEOMETRIC_PASS",
        "selection_key_order": [
            "MAXIMUM_MINIMUM_WORK_PATH_RADIUS_M",
            "MINIMUM_FINAL_SPACING_OUTSIDE_TOLERANCE_FRACTION",
            "MINIMUM_SPACING_ERROR_P95_M",
            "MINIMUM_ABSOLUTE_COVERAGE_PROXY_ERROR_FROM_1",
            "MINIMUM_PHASE_OFFSET_FRACTION",
        ],
        "coverage_proxy_target_ratio": 1.0,
        "phase_offset_role": "SELECTED_PASS",
        "selected_phase_offset_fraction": 0.0,
        "selected_phase_offset_m": 0.0,
        "diagnostic_phase_offset_fraction": None,
        "diagnostic_phase_offset_m": None,
        "diagnostic_selection_rule": "LOWEST_CONFIGURED_PHASE_OFFSET_V1",
        "attempted_offsets": [
            {"phase_offset_fraction": fraction}
            for fraction in EXPECTED_PHASE_OFFSET_CONTRACT["phase_offset_fractions"]
        ],
        "row_spacing_m": 1.5,
        "level_equation": EXPECTED_PHASE_OFFSET_CONTRACT["phase_level_equation"],
        "phase_level_index_scope": EXPECTED_PHASE_OFFSET_CONTRACT["phase_level_index_scope"],
        "row_index_definition": EXPECTED_PHASE_OFFSET_CONTRACT["row_index_definition"],
        "phase_gauge": {
            "method": EXPECTED_PHASE_CONTRACT["gauge_method"],
            "anchor_phase_value_m": 0.0,
            "anchors": [{"solver_row": 0, "solver_column": 0, "x_m": 0.5, "y_m": 0.5}],
        },
    }
    synthetic_halo = {
        "method": EXPECTED_BOUNDARY_CONTRACT["contour_extrapolation_method"],
        "gradient_estimation_status": "PASS",
        "halo_cells": EXPECTED_BOUNDARY_CONTRACT["contour_extrapolation_halo_cells"],
        "gradient_lsq_max_radius_cells": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_radius_cells"],
        "gradient_lsq_max_relative_residual": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_relative_residual"],
        "gradient_lsq_max_condition_number": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_max_condition_number"],
        "gradient_lsq_minimum_neighbor_count": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_minimum_neighbor_count"],
        "gradient_lsq_required_rank": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_lsq_required_rank"],
        "gradient_component_connectivity": EXPECTED_BOUNDARY_CONTRACT["contour_gradient_component_connectivity"],
    }
    synthetic_candidate_audit = {
        "candidate_id": "CF0A_CONSERVACAO",
        "field_id": "F1",
        "work_block_id": "WB_F1_C001",
        "geometric_status": "GEOMETRIC_PASS",
        "phase_offset_m": 0.0,
        "phase_offset_fraction": 0.0,
        "phase_offset_role": "SELECTED_PASS",
        "phase_offset_selection": synthetic_selection,
        "contour_extrapolation_qa": synthetic_halo,
        "solve_mask_component_count": 1,
        "row_count": 1,
        "diagnostic_row_count": 0,
        "blocker_codes": [],
    }
    page_kinds = [
        "cover",
        "scope",
        "method",
        "phase_contract",
        "phase_audit",
        "extraction_contract",
        "domains",
        "domain_exclusions",
        "qa",
        "block_map",
        "block_rasters",
        "gates",
        "hydraulic",
        "limitations",
        "roadmap",
        "traceability",
    ]
    valid = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "manifest_type": "CF0_REVIEW_REPORT",
        "release": "CF0_GEOMETRIC_SCREENING_REVIEW",
        "generated_at": "2026-08-14T00:00:00Z",
        "release_seal": SEAL,
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "guidance_authorized": False,
        "legacy_pdfs_read": False,
        "source_package": {},
        "source_layers": list(EXPECTED_SOURCE_LAYERS),
        "phase_contract": dict(EXPECTED_PHASE_CONTRACT),
        "phase_offset_contract": dict(EXPECTED_PHASE_OFFSET_CONTRACT),
        "boundary_contract": dict(EXPECTED_BOUNDARY_CONTRACT),
        "spline_representation_contract": dict(
            EXPECTED_SPLINE_REPRESENTATION_CONTRACT
        ),
        "blocker_contract": dict(EXPECTED_BLOCKER_CONTRACT),
        "candidate_phase_audit": [synthetic_candidate_audit],
        "diagnostic_rows": {
            "layer_required": True,
            "status": "PRESENT_NOT_APPROVED",
            "review_status": "NOT_APPROVED",
            "guidance_status": "NOT_AUTHORIZED",
            "feature_count": 0,
            "release_authorized": False,
        },
        "excluded_components": {
            "status": "AUDITED_FROM_CF0_MANIFEST",
            "component_count": 0,
            "total_area_m2": 0.0,
            "filter": {
                "revision": "CF0_WORK_BLOCK_SUPPORT_V1",
                "grid_cell_estimate_method": "FLOOR_AREA_DIVIDED_BY_GRID_CELL_AREA",
                "minimum_grid_cell_count": 9,
                "effective_minimum_area_m2": 36.0,
                "width_method": "MINIMUM_ROTATED_RECTANGLE_SHORT_SIDE",
                "minimum_width_row_spacing_factor": 2.0,
                "effective_minimum_width_m": 3.0,
            },
        },
        "roadmap": dict(EXPECTED_ROADMAP),
        "page_count": len(page_kinds),
        "pages": [
            {"number": index, "title": kind, "slug": f"{index:02d}-{kind}", "kind": kind, "payload": None}
            for index, kind in enumerate(page_kinds, start=1)
        ],
        "output_integrity": {},
        "preflight": {},
        "generator": {},
    }
    validate_manifest_shape(valid)
    invalid = json.loads(json.dumps(valid))
    invalid["guidance_authorized"] = True
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a guidance authorization claim.")
    invalid = json.loads(json.dumps(valid))
    invalid["roadmap"]["C3_ESD"] = "GENERATED"
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a generated C3 claim.")
    invalid = json.loads(json.dumps(valid))
    invalid["diagnostic_rows"]["status"] = "ABSENT_NOT_INVENTED"
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted an absent diagnostic_rows layer.")
    invalid = json.loads(json.dumps(valid))
    invalid["source_layers"].remove("diagnostic_rows")
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted an incomplete CF0 source layer contract.")
    invalid = json.loads(json.dumps(valid))
    invalid["phase_contract"]["gauge_method"] = "IMPLICIT_MEAN_ZERO"
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted an implicit phase gauge.")
    invalid = json.loads(json.dumps(valid))
    invalid["phase_offset_contract"]["phase_offset_search_scope"] = "CONTINUOUS_GAUGE_INVARIANCE"
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a false continuous-gauge claim.")
    invalid = json.loads(json.dumps(valid))
    invalid["candidate_phase_audit"][0]["contour_extrapolation_qa"][
        "gradient_lsq_max_condition_number"
    ] = 101.0
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a relaxed LSQ condition threshold.")
    invalid = json.loads(json.dumps(valid))
    invalid["candidate_phase_audit"][0]["solve_mask_component_count"] = 2
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a disconnected solve mask without blocker.")
    invalid = json.loads(json.dumps(valid))
    invalid["candidate_phase_audit"][0]["blocker_codes"] = ["C2_SPLINE_FAILED"]
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted the retired spline blocker.")
    invalid = json.loads(json.dumps(valid))
    invalid["boundary_contract"]["endpoint_extension_mode"] = "NEAREST_EDGE_LATERAL_LINK"
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a non-tangent endpoint extension mode.")
    invalid = json.loads(json.dumps(valid))
    invalid["spline_representation_contract"][
        "spline_representation_tolerance_fraction"
    ] = 0.20
    try:
        validate_manifest_shape(invalid)
    except ReportContractError:
        pass
    else:
        raise RuntimeError("Self-test accepted a relaxed spline representation tolerance.")
    return {
        "status": "PASS",
        "checks": [
            "manifest_shape",
            "guidance_fail_closed",
            "roadmap_fail_closed",
            "diagnostic_layer_fail_closed",
            "four_source_layers_fail_closed",
            "excluded_components_contract",
            "phase_gauge_contract_fail_closed",
            "discrete_phase_offset_contract_fail_closed",
            "candidate_phase_audit_fail_closed",
            "solve_mask_connectivity_fail_closed",
            "row_spline_blocker_contract",
            "boundary_contract_fail_closed",
            "spline_representation_contract_fail_closed",
        ],
    }


def cli_path(value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-manifest", type=Path, default=DEFAULT_REPORT_MANIFEST)
    parser.add_argument("--cf0-schema", type=Path, default=DEFAULT_CF0_SCHEMA)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    result = verify_report(cli_path(args.report_manifest), cli_path(args.cf0_schema))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"CF0_REVIEW_REPORT_VERIFICATION_FAILED: {error}")
        raise SystemExit(2)
