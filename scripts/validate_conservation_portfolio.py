"""Validate the canonical conservation macro-scenario definitions."""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PRESETS = REPO / "config" / "cenarios_conservacionistas.json"
SCHEMA = REPO / "schemas" / "conservation-scenario.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    presets = json.loads(PRESETS.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    require(presets["schema_version"] == "2.1.0", "Preset contract must use schema version 2.1.0.")
    require(schema["properties"]["schema_version"]["const"] == "2.1.0", "Schema version is out of sync.")

    macros = presets["macros"]
    by_id = {item["id"]: item for item in macros}
    require(len(by_id) == len(macros), "Macro IDs must be unique.")
    require(
        set(presets["portfolio_policy"]["required_macros"])
        == {"C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD"},
        "The three required client macros changed unexpectedly.",
    )
    require(presets["portfolio_policy"]["conditional_macro"] == "C4_MISTO_POR_ZONA", "Missing mixed macro.")

    embedded = by_id["C1_CURVA_EMBUTIDA"]
    require(embedded["terrace"]["section"] == "EMBUTIDA", "C1 must fix the embedded section.")
    require(set(embedded["internal_variants"]) == {"EMBUTIDA_TI", "EMBUTIDA_TD"}, "C1 must evaluate TI and TD.")

    wide = by_id["C2_BASE_LARGA_PASSANTE"]
    require(wide["terrace"]["section"] == "BASE_LARGA", "C2 must fix the wide-base section.")
    require("PASSANTE" in wide["terrace"]["trafficability"], "C2 must retain conditional passability.")
    require(
        set(wide["internal_variants"]) == {"BASE_LARGA_TI_PASSANTE", "BASE_LARGA_TD_PASSANTE"},
        "C2 must evaluate TI and TD.",
    )

    esd = by_id["C3_ESD"]
    require(esd["system_family"] == "ESCOAMENTO_SUPERFICIAL_DIFUSO", "C3 must be the ESD system.")
    require(set(esd["internal_variants"]) == {"ESD_SETORIAL", "ESD_COMPLEMENTADO"}, "C3 variants are incomplete.")
    require("EVERY_ROW_REACH" in esd["row_relationship"]["hydraulic_continuity"], "Every ESD reach needs a destination.")

    mixed = by_id["C4_MISTO_POR_ZONA"]
    require(mixed["system_family"] == "MIXED", "C4 must combine systems by zone.")
    require(len(mixed["internal_variants"]) >= 2, "C4 needs multiple mixed variants.")

    archetypes = set(schema["$defs"]["archetype"]["enum"])
    require(
        archetypes == {"CURVA_EMBUTIDA", "BASE_LARGA_PASSANTE", "ESD", "MISTO_POR_ZONA"},
        "Schema archetypes do not match the portfolio.",
    )
    require(len(presets["common_hard_gates"]) >= 8, "The common hard-gate contract is incomplete.")

    connection_portfolio = presets["operational_connection_portfolio"]
    connection_variants = connection_portfolio["variants"]
    connection_by_id = {item["id"]: item for item in connection_variants}
    require(len(connection_by_id) == len(connection_variants), "Operational connection IDs must be unique.")
    require(
        set(connection_by_id)
        == {"OC0_ISOLADO", "OC1_EIXO_COMUM", "OC2_GUIA_CONTINUA", "OC3_TRABALHO_CONTINUO"},
        "The operational connection portfolio is incomplete.",
    )
    expected_connections = {
        "ISOLADO_POR_TALHAO",
        "EIXO_COMUM",
        "GUIA_CONTINUA",
        "TRABALHO_CONTINUO",
    }
    require(
        {item["connection"] for item in connection_variants} == expected_connections,
        "Operational connection semantics changed unexpectedly.",
    )
    require(
        len(connection_portfolio["invariants"]) == 3,
        "Operational, work and hydraulic continuity invariants are incomplete.",
    )
    require(
        len(connection_portfolio["mandatory_metrics"]) >= 10,
        "The operational connection scorecard is incomplete.",
    )
    schema_connections = set(
        schema["properties"]["technical_axes"]["properties"]["operational_connection"]["enum"]
    )
    require(schema_connections == expected_connections, "Schema connections do not match the preset portfolio.")
    require(
        {"optimization_scope", "guidance_groups", "connection_plan"}.issubset(schema["required"]),
        "The multifield scenario contract is not mandatory in the schema.",
    )
    require(
        {"optimizationScope", "guidanceGroup", "connectionPlan", "portalUse", "permissionScope"}.issubset(
            schema["$defs"]
        ),
        "The multifield schema definitions are incomplete.",
    )

    multifield_gates = {
        "classified_farm_field_and_legal_boundaries",
        "verified_transit_work_earthwork_and_water_permissions",
        "complete_terrain_coverage_of_every_portal_and_machine_envelope",
        "validated_portal_geometry_profile_and_drainage",
        "independent_operational_and_hydraulic_continuity",
        "public_road_environmental_and_third_party_authorizations",
    }
    require(
        multifield_gates.issubset(presets["common_hard_gates"]),
        "Multifield hard gates are incomplete.",
    )

    interference = presets["interference_policy"]
    power_v1 = interference["power_line_v1"]
    require(power_v1["input_feature"] == "POWER_LINE_AXIS", "The V1 power-line input changed unexpectedly.")
    require(power_v1["operational_effect"] == "ABSOLUTE_BARRIER", "Power lines must remain barriers in V1.")
    require(
        power_v1["missing_layer_policy"] == "REQUIRE_DECLARED_NONE_OR_KEEP_PENDING",
        "A missing power layer cannot mean that no line exists.",
    )
    require(
        set(interference["catalog_completeness_states"])
        == {"COMPLETE", "PARTIAL", "NOT_REVIEWED"},
        "Interference completeness states are incomplete.",
    )

    poa_portfolio = presets["poa_logistics_portfolio"]
    poa_ids = {item["id"] for item in poa_portfolio["variants"]}
    require(
        poa_ids == {"P0_POA_EXISTENTE", "P1_SEM_NOVA_OBRA", "P2_NOVOS_POAS", "P3_INTEGRADO"},
        "The POA/logistics portfolio is incomplete.",
    )
    require(
        set(poa_portfolio["objective_profiles"])
        == {"MIN_TRANSBORDO", "MIN_COMPACTACAO", "BALANCEADO_CAPACITADO", "ROBUSTO_PICO"},
        "The POA objective profiles are incomplete.",
    )
    require(len(poa_portfolio["mandatory_metrics"]) >= 10, "The POA/logistics scorecard is incomplete.")
    require(
        {"interference_plan", "poa_logistics_plan"}.issubset(schema["required"]),
        "Interference and POA plans must be explicit in every scenario.",
    )
    require(
        {"interferencePlan", "poaLogisticsPlan"}.issubset(schema["$defs"]),
        "Interference and POA schema definitions are missing.",
    )

    new_gates = {
        "complete_interference_declaration_and_operation_specific_effects",
        "power_axis_barrier_applied_or_declared_none",
        "full_lifecycle_fleet_profiles",
        "poa_site_access_soil_drainage_and_permissions",
        "harvest_transshipment_poa_truck_capacity_and_queue_stability",
        "no_feasible_candidate_is_never_released_as_best",
    }
    require(new_gates.issubset(presets["common_hard_gates"]), "Safety and POA hard gates are incomplete.")

    result = {
        "status": "verified",
        "required_macro_count": 3,
        "conditional_macro_count": 1,
        "technical_variant_count": sum(len(item["internal_variants"]) for item in macros),
        "operational_connection_variant_count": len(connection_variants),
        "operational_connection_metric_count": len(connection_portfolio["mandatory_metrics"]),
        "poa_logistics_variant_count": len(poa_portfolio["variants"]),
        "poa_logistics_metric_count": len(poa_portfolio["mandatory_metrics"]),
        "interference_effect_count": len(interference["effects_by_operation"]),
        "common_hard_gate_count": len(presets["common_hard_gates"]),
        "preset_file": str(PRESETS.relative_to(REPO)),
        "schema_file": str(SCHEMA.relative_to(REPO)),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
