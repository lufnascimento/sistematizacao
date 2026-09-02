"""Preliminary uniform-flow capacity checks for trapezoidal open sections."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


CAPACITY_RELEASE = "PRELIMINARY_TRAPEZOIDAL_MANNING_CAPACITY_ONLY"
CAPACITY_LIMITATIONS = (
    "UNIFORM_STEADY_FLOW_ASSUMPTION",
    "ENERGY_SLOPE_ASSUMED_EQUAL_TO_BED_SLOPE",
    "NO_BACKWATER_OR_DOWNSTREAM_CONTROL",
    "NO_TRANSITIONS_OR_LOCAL_LOSSES",
    "NO_SEDIMENT_OR_DEBRIS",
    "SECTION_CONDITION_IS_USER_DECLARED",
    "DECLARED_STABILITY_LIMITS_ARE_SCREENING_ONLY",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)


def trapezoid_properties(depth_m: float, bottom_width_m: float, side_slope_h_to_v: float) -> dict[str, float]:
    depth = float(depth_m)
    bottom = float(bottom_width_m)
    side = float(side_slope_h_to_v)
    if not all(math.isfinite(value) for value in (depth, bottom, side)) or depth <= 0 or bottom < 0 or side < 0:
        raise ValueError("trapezoid dimensions are invalid")
    area = depth * (bottom + side * depth)
    wetted_perimeter = bottom + 2.0 * depth * math.sqrt(1.0 + side * side)
    top_width = bottom + 2.0 * side * depth
    if area <= 0 or wetted_perimeter <= 0 or top_width <= 0:
        raise ValueError("trapezoid must have positive area, perimeter and top width")
    return {
        "area_m2": area,
        "wetted_perimeter_m": wetted_perimeter,
        "hydraulic_radius_m": area / wetted_perimeter,
        "top_width_m": top_width,
    }


def manning_discharge_m3_s(depth_m: float, bottom_width_m: float, side_slope_h_to_v: float, slope_m_m: float, manning_n: float) -> float:
    slope = float(slope_m_m)
    roughness = float(manning_n)
    if not math.isfinite(slope) or slope <= 0 or not math.isfinite(roughness) or roughness <= 0:
        raise ValueError("slope and Manning n must be positive")
    properties = trapezoid_properties(depth_m, bottom_width_m, side_slope_h_to_v)
    return properties["area_m2"] * properties["hydraulic_radius_m"] ** (2.0 / 3.0) * math.sqrt(slope) / roughness


def normal_depth_m(discharge_m3_s: float, bottom_width_m: float, side_slope_h_to_v: float, slope_m_m: float, manning_n: float) -> float:
    discharge = float(discharge_m3_s)
    if not math.isfinite(discharge) or discharge < 0:
        raise ValueError("discharge must be finite and non-negative")
    if discharge == 0:
        return 0.0
    low, high = 0.0, 1.0
    while manning_discharge_m3_s(high, bottom_width_m, side_slope_h_to_v, slope_m_m, manning_n) < discharge:
        high *= 2.0
        if high > 10_000:
            raise ValueError("normal depth could not be bracketed")
    for _ in range(100):
        middle = (low + high) / 2.0
        if manning_discharge_m3_s(middle, bottom_width_m, side_slope_h_to_v, slope_m_m, manning_n) < discharge:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def check_reach_capacities(reaches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not reaches:
        raise ValueError("reaches cannot be empty")
    results = []
    seen = set()
    for index, reach in enumerate(reaches):
        reach_id = str(reach.get("id") or "").strip()
        condition_state = str(reach.get("condition_state") or "CURRENT").strip().upper()
        section_key = (reach_id, condition_state)
        if not reach_id or condition_state not in {"NEW", "CURRENT", "DEGRADED"} or section_key in seen:
            raise ValueError("reach and condition combinations must be valid and unique")
        seen.add(section_key)
        values = {}
        for key in ("peak_flow_m3_s", "bottom_width_m", "side_slope_h_to_v", "slope_m_m", "manning_n", "maximum_flow_depth_m"):
            value = reach.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"reaches[{index}].{key} must be numeric and finite")
            values[key] = float(value)
        if values["peak_flow_m3_s"] < 0 or values["bottom_width_m"] < 0 or values["side_slope_h_to_v"] < 0:
            raise ValueError("flow and section dimensions cannot be negative")
        capacity = manning_discharge_m3_s(
            values["maximum_flow_depth_m"], values["bottom_width_m"], values["side_slope_h_to_v"],
            values["slope_m_m"], values["manning_n"],
        )
        required_depth = normal_depth_m(
            values["peak_flow_m3_s"], values["bottom_width_m"], values["side_slope_h_to_v"],
            values["slope_m_m"], values["manning_n"],
        )
        if required_depth > 0:
            props = trapezoid_properties(required_depth, values["bottom_width_m"], values["side_slope_h_to_v"])
            velocity = values["peak_flow_m3_s"] / props["area_m2"]
            hydraulic_depth = props["area_m2"] / props["top_width_m"]
            froude = velocity / math.sqrt(9.80665 * hydraulic_depth)
            shear = 1000.0 * 9.80665 * props["hydraulic_radius_m"] * values["slope_m_m"]
        else:
            velocity = froude = shear = 0.0
        ratio = values["peak_flow_m3_s"] / capacity if capacity else math.inf
        velocity_limit = reach.get("maximum_admissible_velocity_m_s")
        shear_limit = reach.get("maximum_admissible_shear_pa")
        for key, value in (("maximum_admissible_velocity_m_s", velocity_limit), ("maximum_admissible_shear_pa", shear_limit)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0):
                raise ValueError(f"reaches[{index}].{key} must be positive when provided")
        velocity_limit = float(velocity_limit) if velocity_limit is not None else None
        shear_limit = float(shear_limit) if shear_limit is not None else None
        limit_source_id = str(reach.get("stability_limit_source_id") or "").strip() or None
        limit_evidence_state = str(reach.get("stability_limit_evidence_state") or "").strip() or None
        if (velocity_limit is not None or shear_limit is not None) and (
            not limit_source_id or limit_evidence_state not in {"SYSTEM_REFERENCE", "PROJECT_EVIDENCE"}
        ):
            raise ValueError("declared stability limits require valid source lineage")
        velocity_exceeded = velocity_limit is not None and velocity > velocity_limit
        shear_exceeded = shear_limit is not None and shear > shear_limit
        if velocity_limit is None and shear_limit is None:
            stability_status = "NOT_EVALUATED"
        elif velocity_exceeded and shear_exceeded:
            stability_status = "EXCEEDS_BOTH_DECLARED_LIMITS"
        elif velocity_exceeded:
            stability_status = "EXCEEDS_DECLARED_VELOCITY_LIMIT"
        elif shear_exceeded:
            stability_status = "EXCEEDS_DECLARED_SHEAR_LIMIT"
        else:
            stability_status = "WITHIN_DECLARED_LIMITS"
        results.append({
            "id": reach_id,
            "condition_state": condition_state,
            **values,
            "capacity_m3_s": capacity,
            "capacity_ratio": ratio,
            "capacity_margin_m3_s": capacity - values["peak_flow_m3_s"],
            "required_normal_depth_m": required_depth,
            "depth_margin_m": values["maximum_flow_depth_m"] - required_depth,
            "velocity_at_peak_m_s": velocity,
            "froude_number_at_peak": froude,
            "boundary_shear_at_peak_pa": shear,
            "maximum_admissible_velocity_m_s": velocity_limit,
            "maximum_admissible_shear_pa": shear_limit,
            "stability_limit_source_id": limit_source_id,
            "stability_limit_evidence_state": limit_evidence_state,
            "velocity_limit_ratio": velocity / velocity_limit if velocity_limit else None,
            "shear_limit_ratio": shear / shear_limit if shear_limit else None,
            "preliminary_capacity_status": "WITHIN_DECLARED_SECTION" if ratio <= 1 else "EXCEEDS_DECLARED_SECTION",
            "preliminary_stability_status": stability_status,
            "erosion_safety_approved": False,
        })
    return {
        "release": CAPACITY_RELEASE,
        "method": "MANNING_UNIFORM_FLOW_TRAPEZOIDAL_SECTION",
        "reach_count": len(results),
        "within_capacity_count": sum(item["capacity_ratio"] <= 1 for item in results),
        "exceeded_capacity_count": sum(item["capacity_ratio"] > 1 for item in results),
        "stability_evaluated_count": sum(item["preliminary_stability_status"] != "NOT_EVALUATED" for item in results),
        "stability_exceeded_count": sum(item["preliminary_stability_status"].startswith("EXCEEDS_") for item in results),
        "condition_counts": {
            state: sum(item["condition_state"] == state for item in results)
            for state in ("NEW", "CURRENT", "DEGRADED")
        },
        "reaches": results,
        "backwater_evaluated": False,
        "unsteady_flow_evaluated": False,
        "admissible_velocity_or_shear_evaluated": any(
            item["preliminary_stability_status"] != "NOT_EVALUATED" for item in results
        ),
        "receiver_approved": False,
        "project_executive_authorized": False,
        "guidance_authorized": False,
        "limitations": list(CAPACITY_LIMITATIONS),
    }


def validate_capacity_release(result: Mapping[str, Any]) -> None:
    if result.get("release") != CAPACITY_RELEASE:
        raise ValueError("capacity release changed")
    for key in ("backwater_evaluated", "unsteady_flow_evaluated", "receiver_approved", "project_executive_authorized", "guidance_authorized"):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false")
    if result.get("admissible_velocity_or_shear_evaluated") is not any(
        item.get("preliminary_stability_status") != "NOT_EVALUATED" for item in result.get("reaches", [])
    ):
        raise ValueError("stability evaluation flag disagrees with reaches")
    if any(item.get("erosion_safety_approved") is not False for item in result.get("reaches", [])):
        raise ValueError("erosion safety must remain unapproved")
    if not set(CAPACITY_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("capacity limitations are incomplete")
