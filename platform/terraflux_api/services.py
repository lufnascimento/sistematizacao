from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import PRODUCTS
from .storage import LocalStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scenario_review_selection_blocker(scenario: dict[str, Any]) -> dict[str, str] | None:
    """Return the fail-closed reason that prevents review-representative selection."""

    family = str(scenario.get("family") or "").upper()
    code = str(scenario.get("code") or "").upper()
    status = str(scenario.get("status") or "").upper()
    if "CF0" in family or code.startswith("CF0") or status.startswith("CF0"):
        return {
            "code": "CF0_REVIEW_REPRESENTATIVE_FORBIDDEN",
            "message": "CF0 remains hydraulically unconfirmed or partial and cannot be selected as the review representative.",
        }
    if "C1" in family or code.startswith("C1") or status == "CONCEPT_ONLY":
        return {
            "code": "CONCEPT_ONLY_REVIEW_REPRESENTATIVE_FORBIDDEN",
            "message": "Concept-only scenarios cannot be selected as the review representative.",
        }
    if not (family.startswith("E0") or code.startswith("E0")):
        return {
            "code": "SCENARIO_FAMILY_NOT_SELECTABLE",
            "message": "Only an E0 screening scenario may represent this run in technical review.",
        }
    if scenario.get("geometry_eligible") is not True:
        return {
            "code": "E0_GEOMETRY_INELIGIBLE",
            "message": "The E0 scenario failed geometric gates and is diagnostic only.",
        }
    if status != "E0_SCREENING_ONLY_NOT_AUTHORIZED":
        return {
            "code": "E0_RELEASE_STATUS_NOT_SELECTABLE",
            "message": "The E0 scenario is outside the selectable screening release status.",
        }
    if scenario.get("guidance_authorized") is not False:
        return {
            "code": "GUIDANCE_BOUNDARY_INVALID",
            "message": "The scenario does not carry the required explicit no-guidance boundary.",
        }
    return None


def build_readiness(store: LocalStore, project: dict[str, Any]) -> dict[str, Any]:
    assets = store.list("assets", lambda item: item["project_id"] == project["id"] and item["status"] == "STORED")
    by_role: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        by_role.setdefault(asset["role"], []).append(asset)

    boundary_assets = by_role.get("FIELD_BOUNDARY", [])
    usable_boundaries = _usable_vector_assets(boundary_assets)
    elevation_assets = [
        asset for role in ("POINT_CLOUD", "DTM_DEM") for asset in by_role.get(role, [])
    ]
    orthomosaics = by_role.get("ORTHOMOSAIC", [])
    configuration = project["configuration"]
    power_state = configuration["constraints"]["power_network_state"]
    power_assets = by_role.get("POWER_NETWORK", [])
    usable_power_assets = _usable_vector_assets(power_assets)

    evidence = [
        {
            "id": "FIELD_BOUNDARY",
            "status": (
                "PASSED_E0_ENGINE"
                if usable_boundaries
                and all(item.get("spatial_qa_status") == "PASSED_E0_ENGINE" for item in usable_boundaries)
                else "AVAILABLE_PENDING_SPATIAL_QA"
                if usable_boundaries
                else "PARTIAL"
                if boundary_assets
                else "MISSING"
            ),
            "asset_ids": [item["id"] for item in boundary_assets],
            "required": True,
        },
        {
            "id": "ELEVATION_SOURCE",
            "status": (
                "PASSED_E0_ENGINE"
                if elevation_assets
                and all(item.get("spatial_qa_status") == "PASSED_E0_ENGINE" for item in elevation_assets)
                else "AVAILABLE_PENDING_SPATIAL_QA"
                if elevation_assets
                else "MISSING"
            ),
            "asset_ids": [item["id"] for item in elevation_assets],
            "required": True,
        },
    ]
    evidence.append(
        {
            "id": "ORTHOMOSAIC_CONTEXT",
            "status": "AVAILABLE_PENDING_SPATIAL_QA" if orthomosaics else "MISSING_OPTIONAL",
            "asset_ids": [item["id"] for item in orthomosaics],
            "required": False,
            "counts_as_elevation": False,
        }
    )
    if power_state == "DECLARED_NONE":
        power_status = "NOT_APPLICABLE"
    elif power_state == "UPLOADED" and usable_power_assets:
        power_status = "AVAILABLE_PENDING_BARRIER_QA"
    elif power_state == "UPLOADED":
        power_status = "PARTIAL" if power_assets else "MISSING"
    else:
        power_status = "UNKNOWN"
    evidence.append(
        {
            "id": "POWER_NETWORK",
            "status": power_status,
            "asset_ids": [item["id"] for item in power_assets],
            "required": power_state != "DECLARED_NONE",
        }
    )

    project_input_blockers: list[dict[str, str]] = []
    if not usable_boundaries:
        if boundary_assets:
            project_input_blockers.append(
                {"code": "FIELD_BOUNDARY_SHAPEFILE_INCOMPLETE", "message": "Envie SHP, SHX e DBF com o mesmo nome ou um ZIP/GeoPackage/GeoJSON."}
            )
        else:
            project_input_blockers.append({"code": "FIELD_BOUNDARY_MISSING", "message": "Envie os poligonos dos talhoes."})
    if not elevation_assets:
        message = "O ortomosaico nao contem uma superficie altimetrica utilizavel; envie LAS/LAZ ou MDE/MDT." if orthomosaics else "Envie LAS/LAZ ou MDE/MDT."
        project_input_blockers.append({"code": "ELEVATION_SOURCE_MISSING", "message": message})

    selected_product_ids = set(configuration["selected_product_ids"])
    products: list[dict[str, Any]] = []
    for product_id, definition in PRODUCTS.items():
        product_blockers: list[str] = []
        for requirement in definition.get("required_asset_groups", []):
            if requirement == "FIELD_BOUNDARY" and not usable_boundaries:
                product_blockers.append("FIELD_BOUNDARY_MISSING")
            elif requirement == "ELEVATION_SOURCE" and not elevation_assets:
                product_blockers.append("ELEVATION_SOURCE_MISSING")
            elif requirement == "SOIL_HYDROLOGY":
                if not by_role.get("SOIL_MAP") or not by_role.get("RAINFALL"):
                    product_blockers.append("SOIL_HYDROLOGY_MISSING")
            elif requirement == "ROADS_CARRIERS" and not by_role.get("ROADS_CARRIERS"):
                product_blockers.append("ROADS_CARRIERS_MISSING")
            elif requirement == "FLEET" and not configuration["logistics"].get("harvester_model"):
                product_blockers.append("FLEET_CONFIGURATION_MISSING")
            elif requirement == "RECEIVER" and not configuration["conservation"].get("hydraulic_receiver_id"):
                product_blockers.append("HYDRAULIC_RECEIVER_MISSING")
        implementation = definition["implementation"]
        client_engine = definition.get("client_engine")
        if product_id in {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"}:
            hydrology = configuration.get("hydrology_screening", {})
            if hydrology.get("enabled") is not True:
                product_blockers.append("PCX1_CONFIGURATION_NOT_ENABLED")
            for key in ("catchment_area_ha", "curve_number", "parameter_source_id"):
                if hydrology.get(key) in (None, ""):
                    product_blockers.append("PCX1_PARAMETERS_INCOMPLETE")
                    break
            if not hydrology.get("rainfall_intervals"):
                product_blockers.append("PCX1_RAINFALL_INTERVALS_REQUIRED")
            if product_id in {"PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"}:
                if "PCX1_RUNOFF_SCREENING" not in selected_product_ids:
                    product_blockers.append("PCX1_RUNOFF_DEPENDENCY_REQUIRED")
                if hydrology.get("hydrograph_enabled") is not True:
                    product_blockers.append("HYDROGRAPH_CONFIGURATION_NOT_ENABLED")
                if hydrology.get("catchment_lag_minutes") in (None, ""):
                    product_blockers.append("CATCHMENT_LAG_REQUIRED")
            if product_id in {"PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"}:
                if "PCX2_HYDROGRAPH_SCREENING" not in selected_product_ids:
                    product_blockers.append("HYDROGRAPH_DEPENDENCY_REQUIRED")
                if hydrology.get("routing_enabled") is not True:
                    product_blockers.append("ROUTING_CONFIGURATION_NOT_ENABLED")
                if not hydrology.get("routing_source_node_id") or not hydrology.get("routing_reaches"):
                    product_blockers.append("ROUTING_NETWORK_REQUIRED")
            if product_id == "PCX4_SECTION_CAPACITY_SCREENING":
                if "PCX3_REACH_ROUTING_SCREENING" not in selected_product_ids:
                    product_blockers.append("ROUTING_DEPENDENCY_REQUIRED")
                if hydrology.get("capacity_enabled") is not True or not hydrology.get("reach_sections"):
                    product_blockers.append("SECTION_CONFIGURATION_REQUIRED")
        if product_id in {"SULCATION_E0", "CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"}:
            if not configuration["topography"].get("field_id_column"):
                product_blockers.append("FIELD_ID_COLUMN_REQUIRED")
            terrain_resolution_m = float(configuration["topography"].get("resolution_m", 1.0))
            if (
                product_id == "SULCATION_E0"
                and terrain_resolution_m > float(configuration["sulcation"].get("row_spacing_m", 1.5))
            ):
                product_blockers.append("TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS")
            if (
                product_id in {"CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"}
                and terrain_resolution_m > float(configuration["sulcation"].get("row_spacing_m", 1.5))
            ):
                product_blockers.append("TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0")
            if (
                product_id == "C1_EMBEDDED_SCREENING"
                and "CF0_CONTINUOUS" not in selected_product_ids
            ):
                product_blockers.append("CF0_CONTINUOUS_DEPENDENCY_REQUIRED")
            if power_state == "UNKNOWN":
                product_blockers.append("POWER_NETWORK_UNRESOLVED")
            elif power_state == "UPLOADED":
                product_blockers.append("POWER_BARRIER_STAGE_REQUIRED")
                if not usable_power_assets:
                    product_blockers.append("POWER_NETWORK_ASSET_INCOMPLETE")
            if configuration["sulcation"].get("allow_cross_field"):
                product_blockers.append("CROSS_FIELD_CONTINUITY_NOT_SUPPORTED")
            if configuration["sulcation"].get("allow_cross_property"):
                product_blockers.append("CROSS_PROPERTY_CONTINUITY_NOT_SUPPORTED")
        auto_generated = bool(definition.get("auto_generated"))
        if not client_engine and not auto_generated:
            product_blockers.append("CLIENT_ENGINE_NOT_AVAILABLE")
        product_blockers = list(dict.fromkeys(product_blockers))
        input_status = (
            "AUTO_GENERATED_WITH_RUN"
            if auto_generated and not product_blockers
            else "READY_FOR_ENGINE_QA"
            if not product_blockers
            else "BLOCKED"
        )
        products.append(
            {
                "product_id": product_id,
                "selected": product_id in selected_product_ids,
                "implementation": implementation,
                "input_status": input_status,
                "blockers": product_blockers,
                "client_data_generation_available": bool(client_engine and not product_blockers),
                "client_engine": client_engine,
                "auto_generated": auto_generated,
                "demo_available": implementation.startswith("DEMO_AVAILABLE"),
            }
        )

    blocker_codes = {item["code"] for item in project_input_blockers}
    covered_product_blockers = set(blocker_codes)
    if not usable_boundaries:
        covered_product_blockers.add("FIELD_BOUNDARY_MISSING")
    if not elevation_assets:
        covered_product_blockers.add("ELEVATION_SOURCE_MISSING")
    selected_product_blockers = {
        code
        for product in products
        if product["selected"]
        for code in product["blockers"]
        if code not in covered_product_blockers
    }
    blockers = project_input_blockers + [
        {"code": code, "message": _BLOCKER_MESSAGES[code]}
        for code in sorted(selected_product_blockers)
    ]
    available_product_ids = [
        product["product_id"] for product in products if product["client_data_generation_available"]
    ]

    return {
        "project_id": project["id"],
        "calculated_at": utc_now(),
        "summary": {
            "asset_count": len(assets),
            "blocking_count": len(blockers),
            "minimum_inputs_present": bool(usable_boundaries and elevation_assets),
            "minimum_upload_roles_present": bool(usable_boundaries and elevation_assets),
            "client_data_release": "CLIENT_PRODUCTS_AVAILABLE" if available_product_ids else "BLOCKED_INPUTS",
            "available_product_ids": available_product_ids,
            "demo_release": "AVAILABLE",
        },
        "evidence": evidence,
        "blockers": blockers,
        "products": products,
        "delivery_boundary": {
            "maximum": "E0_TRIAGEM",
            "guidance_authorized": False,
            "note": "Topografia, sulcacao E0, CF0 e C1 sao produtos de triagem. C1 e apenas conceitual; nenhum produto autoriza orientacao de maquinas ou projeto hidraulico executivo.",
        },
    }


_BLOCKER_MESSAGES = {
    "FIELD_BOUNDARY_MISSING": "Envie os poligonos dos talhoes em um pacote vetorial completo.",
    "ELEVATION_SOURCE_MISSING": "Envie LAS/LAZ ou MDE/MDT.",
    "FIELD_ID_COLUMN_REQUIRED": "Informe a coluna identificadora dos talhoes antes de gerar sulcacao E0, CF0 ou a triagem C1.",
    "TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS": "Use resolucao do MDT igual ou menor que o espacamento entre linhas para a sulcacao E0.",
    "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0": "Use resolucao do MDT igual ou menor que o espacamento entre linhas para CF0 e para a triagem C1 derivada.",
    "CF0_CONTINUOUS_DEPENDENCY_REQUIRED": "Selecione CF0 junto com C1; a triagem conceitual C1 usa o pacote CF0 da mesma rodada.",
    "POWER_NETWORK_UNRESOLVED": "Declare que nao existe rede eletrica ou envie seus dados para revisao.",
    "POWER_BARRIER_STAGE_REQUIRED": "A rede eletrica enviada ainda precisa ser convertida e validada como barreira operacional.",
    "POWER_NETWORK_ASSET_INCOMPLETE": "O pacote vetorial da rede eletrica esta ausente ou incompleto.",
    "CROSS_FIELD_CONTINUITY_NOT_SUPPORTED": "Continuidade entre talhoes ainda nao e suportada pelo pipeline E0/CF0/C1.",
    "CROSS_PROPERTY_CONTINUITY_NOT_SUPPORTED": "Continuidade entre propriedades ainda nao e suportada pelo pipeline E0/CF0/C1.",
    "CLIENT_ENGINE_NOT_AVAILABLE": "Este produto ainda nao possui motor para dados do cliente.",
    "SOIL_HYDROLOGY_MISSING": "Envie mapa de solos e dados de chuva para este produto.",
    "ROADS_CARRIERS_MISSING": "Envie carreadores e estradas para este produto.",
    "FLEET_CONFIGURATION_MISSING": "Configure a frota para este produto.",
    "HYDRAULIC_RECEIVER_MISSING": "Informe o receptor hidraulico para este produto.",
    "PCX1_CONFIGURATION_NOT_ENABLED": "Habilite a triagem PCX1 na configuracao do projeto.",
    "PCX1_PARAMETERS_INCOMPLETE": "Informe area contribuinte, Curve Number e a fonte dos parametros.",
    "PCX1_RAINFALL_INTERVALS_REQUIRED": "Informe ao menos um intervalo do hietograma de chuva.",
    "PCX1_RUNOFF_DEPENDENCY_REQUIRED": "Selecione tambem o produto Chuva que vira escoamento.",
    "HYDROGRAPH_CONFIGURATION_NOT_ENABLED": "Habilite o hidrograma preliminar na configuracao.",
    "CATCHMENT_LAG_REQUIRED": "Informe o tempo de resposta da bacia com sua fonte.",
    "HYDROGRAPH_DEPENDENCY_REQUIRED": "Selecione tambem o Hidrograma preliminar.",
    "ROUTING_CONFIGURATION_NOT_ENABLED": "Habilite a propagacao preliminar na configuracao.",
    "ROUTING_NETWORK_REQUIRED": "Informe o no de entrada e ao menos um trecho da rede.",
    "ROUTING_DEPENDENCY_REQUIRED": "Selecione tambem a Propagacao preliminar na rede.",
    "SECTION_CONFIGURATION_REQUIRED": "Informe a secao, declividade, rugosidade e profundidade de cada trecho.",
}


def _usable_vector_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct = [item for item in assets if item.get("extension") in {".zip", ".gpkg", ".geojson", ".json"}]
    sidecars: dict[str, set[str]] = {}
    for item in assets:
        original = Path(item.get("original_filename", ""))
        sidecars.setdefault(original.stem.casefold(), set()).add(item.get("extension", ""))
    complete_stems = {stem for stem, extensions in sidecars.items() if {".shp", ".shx", ".dbf"} <= extensions}
    return direct + [
        item for item in assets if Path(item.get("original_filename", "")).stem.casefold() in complete_stems
    ]
