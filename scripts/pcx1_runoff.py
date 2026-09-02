"""Deterministic NRCS-CN event rainfall-excess screening.

PCX1 converts a declared rainfall hyetograph into incremental rainfall excess.
It does not transform runoff depth into a hydrograph or evaluate hydraulic
capacity, storage structures, receivers, or failure paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence


PCX1_RELEASE = "PCX1_NRCS_CN_RAINFALL_EXCESS_ONLY"
PCX1_LIMITATIONS = (
    "RAINFALL_EXCESS_METHOD_REQUIRES_PROJECT_APPROVAL",
    "NO_HYDROGRAPH",
    "NO_PEAK_FLOW",
    "NO_HYDRAULIC_ROUTING",
    "NOT_HYDRAULIC_CAPACITY",
    "NOT_SECTION_DIMENSIONING",
    "NOT_RECEIVER_APPROVAL",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class RainfallInterval:
    duration_s: float
    rainfall_mm: float

    def validate(self, index: int) -> "RainfallInterval":
        duration = _finite(self.duration_s, f"intervals[{index}].duration_s")
        rainfall = _finite(self.rainfall_mm, f"intervals[{index}].rainfall_mm")
        if duration <= 0:
            raise ValueError(f"intervals[{index}].duration_s must be positive")
        if rainfall < 0:
            raise ValueError(f"intervals[{index}].rainfall_mm must be non-negative")
        return self


def potential_retention_mm(curve_number: float) -> float:
    cn = _finite(curve_number, "curve_number")
    if not 0 < cn <= 100:
        raise ValueError("curve_number must be in (0, 100]")
    return 25_400.0 / cn - 254.0


def cumulative_runoff_depth_mm(
    cumulative_rainfall_mm: float,
    curve_number: float,
    initial_abstraction_ratio: float,
) -> float:
    rainfall = _finite(cumulative_rainfall_mm, "cumulative_rainfall_mm")
    ratio = _finite(initial_abstraction_ratio, "initial_abstraction_ratio")
    if rainfall < 0:
        raise ValueError("cumulative_rainfall_mm must be non-negative")
    if not 0 <= ratio <= 0.30:
        raise ValueError("initial_abstraction_ratio must be in [0, 0.30]")
    retention = potential_retention_mm(curve_number)
    initial_abstraction = ratio * retention
    if rainfall <= initial_abstraction:
        return 0.0
    effective = rainfall - initial_abstraction
    return effective * effective / (effective + retention)


def calculate_pcx1_rainfall_excess(
    intervals: Sequence[RainfallInterval],
    *,
    catchment_area_ha: float,
    curve_number: float,
    initial_abstraction_ratio: float,
) -> dict[str, Any]:
    """Calculate incremental excess from a declared event hyetograph."""

    area = _finite(catchment_area_ha, "catchment_area_ha")
    if area <= 0:
        raise ValueError("catchment_area_ha must be positive")
    if not intervals:
        raise ValueError("intervals cannot be empty")
    retention = potential_retention_mm(curve_number)
    ratio = _finite(initial_abstraction_ratio, "initial_abstraction_ratio")
    if not 0 <= ratio <= 0.30:
        raise ValueError("initial_abstraction_ratio must be in [0, 0.30]")

    cumulative_rainfall = 0.0
    previous_runoff = 0.0
    elapsed_s = 0.0
    rows: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals):
        interval.validate(index)
        rainfall = float(interval.rainfall_mm)
        duration = float(interval.duration_s)
        cumulative_rainfall += rainfall
        cumulative_runoff = cumulative_runoff_depth_mm(
            cumulative_rainfall,
            curve_number,
            ratio,
        )
        incremental_excess = max(0.0, cumulative_runoff - previous_runoff)
        elapsed_s += duration
        rows.append(
            {
                "index": index,
                "duration_s": duration,
                "elapsed_s": elapsed_s,
                "rainfall_mm": rainfall,
                "rainfall_intensity_mm_h": rainfall / duration * 3600.0,
                "cumulative_rainfall_mm": cumulative_rainfall,
                "rainfall_excess_mm": incremental_excess,
                "cumulative_rainfall_excess_mm": cumulative_runoff,
                "incremental_loss_mm": rainfall - incremental_excess,
            }
        )
        previous_runoff = cumulative_runoff

    excess_volume = previous_runoff / 1000.0 * area * 10_000.0
    rainfall_volume = cumulative_rainfall / 1000.0 * area * 10_000.0
    return {
        "release": PCX1_RELEASE,
        "method": "NRCS_CURVE_NUMBER_EVENT_SCREENING",
        "catchment_area_ha": area,
        "curve_number": float(curve_number),
        "initial_abstraction_ratio": ratio,
        "potential_retention_mm": retention,
        "initial_abstraction_mm": ratio * retention,
        "event_duration_s": elapsed_s,
        "total_rainfall_mm": cumulative_rainfall,
        "total_rainfall_volume_m3": rainfall_volume,
        "total_rainfall_excess_mm": previous_runoff,
        "total_rainfall_excess_volume_m3": excess_volume,
        "runoff_coefficient_event": (
            previous_runoff / cumulative_rainfall if cumulative_rainfall > 0 else 0.0
        ),
        "intervals": rows,
        "hydrograph_generated": False,
        "peak_flow_evaluated": False,
        "hydraulic_capacity_evaluated": False,
        "receiver_approved": False,
        "guidance_authorized": False,
        "limitations": list(PCX1_LIMITATIONS),
    }


def validate_pcx1_release(result: dict[str, Any]) -> None:
    if result.get("release") != PCX1_RELEASE:
        raise ValueError("PCX1 release changed")
    for key in (
        "hydrograph_generated",
        "peak_flow_evaluated",
        "hydraulic_capacity_evaluated",
        "receiver_approved",
        "guidance_authorized",
    ):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false in PCX1")
    if not set(PCX1_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("PCX1 limitations are incomplete")
