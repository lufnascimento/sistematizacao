"""Limited steady subcritical standard-step profiles for prismatic trapezoids."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from scripts.preliminary_section_capacity import trapezoid_properties


PROFILE_RELEASE = "PRELIMINARY_STEADY_SUBCRITICAL_STANDARD_STEP_ONLY"
PROFILE_LIMITATIONS = (
    "STEADY_ONE_DIMENSIONAL_FLOW_ONLY",
    "PRISMATIC_TRAPEZOIDAL_SECTION_ONLY",
    "SUBCRITICAL_REGIME_ONLY",
    "KNOWN_DOWNSTREAM_DEPTH_REQUIRED",
    "AVERAGE_FRICTION_SLOPE_METHOD",
    "NO_JUNCTION_MOMENTUM_OR_STRUCTURES",
    "NO_UNSTEADY_ATTENUATION",
    "NO_SEDIMENT_OR_GEOMETRY_CHANGE",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)
G = 9.80665


def _hydraulics(depth: float, flow: float, bottom: float, side: float, roughness: float) -> dict[str, float]:
    props = trapezoid_properties(depth, bottom, side)
    velocity = flow / props["area_m2"]
    velocity_head = velocity * velocity / (2.0 * G)
    froude = velocity / math.sqrt(G * props["area_m2"] / props["top_width_m"])
    conveyance = props["area_m2"] * props["hydraulic_radius_m"] ** (2.0 / 3.0) / roughness
    friction_slope = (flow / conveyance) ** 2
    return {**props, "velocity_m_s": velocity, "velocity_head_m": velocity_head, "froude_number": froude, "friction_slope_m_m": friction_slope}


def _critical_depth(flow: float, bottom: float, side: float) -> float:
    if flow <= 0:
        return 0.0
    low, high = 1e-8, 1.0
    def residual(depth: float) -> float:
        props = trapezoid_properties(depth, bottom, side)
        return flow * flow * props["top_width_m"] / (G * props["area_m2"] ** 3) - 1.0
    while residual(high) > 0:
        high *= 2.0
        if high > 10_000:
            raise ValueError("critical depth could not be bracketed")
    for _ in range(100):
        middle = (low + high) / 2.0
        if residual(middle) > 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _solve_upstream_depth(
    downstream_depth: float, flow: float, bottom: float, side: float, roughness: float,
    bed_rise: float, step_length: float, critical_depth: float,
) -> float:
    downstream = _hydraulics(downstream_depth, flow, bottom, side, roughness)
    target_without_upstream_friction = downstream_depth + downstream["velocity_head_m"]

    def residual(depth: float) -> float:
        upstream = _hydraulics(depth, flow, bottom, side, roughness)
        friction_loss = 0.5 * (upstream["friction_slope_m_m"] + downstream["friction_slope_m_m"]) * step_length
        return bed_rise + depth + upstream["velocity_head_m"] - target_without_upstream_friction - friction_loss

    low = max(critical_depth * 1.000001, 1e-7)
    high = max(downstream_depth * 2.0, low * 2.0, 1.0)
    samples = 400
    roots: list[tuple[float, float]] = []
    previous_depth = low
    previous_value = residual(low)
    while high <= 100_000:
        for index in range(1, samples + 1):
            depth = low + (high - low) * index / samples
            value = residual(depth)
            if value == 0 or value * previous_value < 0:
                roots.append((previous_depth, depth))
            previous_depth, previous_value = depth, value
        if roots:
            break
        low, high = high, high * 2.0
    if not roots:
        raise ValueError("subcritical standard-step solution did not converge")
    bracket = min(roots, key=lambda item: abs((item[0] + item[1]) / 2.0 - downstream_depth))
    low, high = bracket
    low_value = residual(low)
    for _ in range(100):
        middle = (low + high) / 2.0
        value = residual(middle)
        if value * low_value <= 0:
            high = middle
        else:
            low, low_value = middle, value
    return (low + high) / 2.0


def calculate_standard_step_profiles(reaches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not reaches:
        raise ValueError("profile reaches cannot be empty")
    results = []
    seen = set()
    for index, reach in enumerate(reaches):
        reach_id = str(reach.get("id") or "").strip()
        condition = str(reach.get("condition_state") or "CURRENT").strip().upper()
        key = (reach_id, condition)
        if not reach_id or key in seen:
            raise ValueError("profile reach and condition combinations must be unique")
        seen.add(key)
        numeric = {}
        for name in ("peak_flow_m3_s", "length_m", "bottom_width_m", "side_slope_h_to_v", "slope_m_m", "manning_n", "downstream_water_depth_m"):
            value = reach.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"reaches[{index}].{name} must be numeric and finite")
            numeric[name] = float(value)
        steps = reach.get("profile_step_count", 20)
        if isinstance(steps, bool) or not isinstance(steps, int) or not 2 <= steps <= 1000:
            raise ValueError("profile step count must be an integer from 2 to 1000")
        if numeric["peak_flow_m3_s"] <= 0 or numeric["length_m"] <= 0 or numeric["slope_m_m"] <= 0 or numeric["manning_n"] <= 0 or numeric["downstream_water_depth_m"] <= 0:
            raise ValueError("profile flow, length, slope, roughness and downstream depth must be positive")
        critical = _critical_depth(numeric["peak_flow_m3_s"], numeric["bottom_width_m"], numeric["side_slope_h_to_v"])
        downstream_hydraulics = _hydraulics(numeric["downstream_water_depth_m"], numeric["peak_flow_m3_s"], numeric["bottom_width_m"], numeric["side_slope_h_to_v"], numeric["manning_n"])
        if downstream_hydraulics["froude_number"] >= 1.0:
            raise ValueError("SUPERCRITICAL_OR_CRITICAL_DOWNSTREAM_BOUNDARY_NOT_SUPPORTED")
        step_length = numeric["length_m"] / steps
        points = []
        depth = numeric["downstream_water_depth_m"]
        for step in range(steps + 1):
            distance_upstream = step * step_length
            bed_elevation = distance_upstream * numeric["slope_m_m"]
            hydraulics = _hydraulics(depth, numeric["peak_flow_m3_s"], numeric["bottom_width_m"], numeric["side_slope_h_to_v"], numeric["manning_n"])
            if hydraulics["froude_number"] >= 1.0:
                raise ValueError("STANDARD_STEP_REACHED_UNSUPPORTED_FLOW_REGIME")
            points.append({"station_from_downstream_m": distance_upstream, "bed_elevation_relative_m": bed_elevation, "water_depth_m": depth, "water_surface_elevation_relative_m": bed_elevation + depth, **hydraulics})
            if step < steps:
                depth = _solve_upstream_depth(depth, numeric["peak_flow_m3_s"], numeric["bottom_width_m"], numeric["side_slope_h_to_v"], numeric["manning_n"], numeric["slope_m_m"] * step_length, step_length, critical)
        results.append({"id": reach_id, "condition_state": condition, **numeric, "profile_step_count": steps, "critical_depth_m": critical, "maximum_water_depth_m": max(item["water_depth_m"] for item in points), "maximum_froude_number": max(item["froude_number"] for item in points), "profile": points, "profile_status": "PASS_PRELIMINARY_SUBCRITICAL_PROFILE"})
    return {"release": PROFILE_RELEASE, "method": "STANDARD_STEP_AVERAGE_FRICTION_SLOPE", "profile_count": len(results), "profiles": results, "mixed_or_supercritical_flow_evaluated": False, "structures_evaluated": False, "unsteady_flow_evaluated": False, "receiver_approved": False, "project_executive_authorized": False, "guidance_authorized": False, "limitations": list(PROFILE_LIMITATIONS)}


def validate_profile_release(result: Mapping[str, Any]) -> None:
    if result.get("release") != PROFILE_RELEASE:
        raise ValueError("profile release changed")
    for key in ("mixed_or_supercritical_flow_evaluated", "structures_evaluated", "unsteady_flow_evaluated", "receiver_approved", "project_executive_authorized", "guidance_authorized"):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false")
    if not set(PROFILE_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("profile limitations are incomplete")
