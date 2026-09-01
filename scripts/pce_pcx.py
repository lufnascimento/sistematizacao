"""Deterministic PCE0 erosion and PCX0 event-balance screening kernels.

These kernels intentionally stop before design. PCE0 evaluates a declared
RUSLE factor set for sheet-and-rill erosion screening. PCX0 checks event water
mass balance from already-derived rainfall excess and declared interval
volumes. Neither kernel sizes structures, verifies receivers, models sediment,
or authorizes field execution or machine guidance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence


PCE_RELEASE = "PCE0_RUSLE_SCREENING_ONLY"
PCX_RELEASE = "PCX0_EVENT_MASS_BALANCE_ONLY"

RELEASE_LIMITATIONS = (
    "NOT_PROJECT_EXECUTIVE",
    "NOT_HYDRAULIC_CAPACITY",
    "NOT_SECTION_DIMENSIONING",
    "NOT_RECEIVER_APPROVAL",
    "NOT_GUIDANCE_AUTHORIZED",
)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> float:
    result = _finite(value, label)
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be {qualifier}")
    return result


@dataclass(frozen=True)
class RusleFactors:
    rainfall_erosivity_r: float
    soil_erodibility_k: float
    slope_length_steepness_ls: float
    cover_management_c: float
    support_practice_p: float
    soil_loss_tolerance_t_ha_year: float | None = None

    def validate(self) -> "RusleFactors":
        _positive(self.rainfall_erosivity_r, "rainfall_erosivity_r")
        _positive(self.soil_erodibility_k, "soil_erodibility_k")
        _positive(self.slope_length_steepness_ls, "slope_length_steepness_ls")
        for label, value in (
            ("cover_management_c", self.cover_management_c),
            ("support_practice_p", self.support_practice_p),
        ):
            numeric = _positive(value, label, allow_zero=True)
            if numeric > 1:
                raise ValueError(f"{label} must be in [0, 1]")
        if self.soil_loss_tolerance_t_ha_year is not None:
            _positive(
                self.soil_loss_tolerance_t_ha_year,
                "soil_loss_tolerance_t_ha_year",
            )
        return self


def calculate_pce0_rusle(factors: RusleFactors) -> dict[str, Any]:
    """Calculate a declared RUSLE factor product without hydraulic claims."""

    factors.validate()
    soil_loss = (
        float(factors.rainfall_erosivity_r)
        * float(factors.soil_erodibility_k)
        * float(factors.slope_length_steepness_ls)
        * float(factors.cover_management_c)
        * float(factors.support_practice_p)
    )
    tolerance = factors.soil_loss_tolerance_t_ha_year
    if tolerance is None:
        ratio = None
        status = "TOLERANCE_NOT_DECLARED"
    else:
        ratio = soil_loss / float(tolerance)
        status = (
            "WITHIN_DECLARED_TOLERANCE_SCREENING"
            if ratio <= 1.0 + 1e-12
            else "EXCEEDS_DECLARED_TOLERANCE_SCREENING"
        )
    return {
        "release": PCE_RELEASE,
        "method": "RUSLE_DECLARED_FACTORS_PRODUCT",
        "domain": "SHEET_AND_RILL_EROSION_SCREENING",
        "estimated_soil_loss_t_ha_year": soil_loss,
        "declared_tolerance_t_ha_year": tolerance,
        "soil_loss_to_tolerance_ratio": ratio,
        "screening_status": status,
        "hydraulic_capacity_evaluated": False,
        "practice_recommendation_authorized": False,
        "guidance_authorized": False,
        "limitations": list(RELEASE_LIMITATIONS),
    }


@dataclass(frozen=True)
class PcxInterval:
    duration_s: float
    rainfall_excess_mm: float
    external_inflow_m3: float
    controlled_outflow_m3: float
    storage_start_m3: float
    storage_end_m3: float

    def validate(self, index: int) -> "PcxInterval":
        prefix = f"intervals[{index}]"
        _positive(self.duration_s, f"{prefix}.duration_s")
        for field_name in (
            "rainfall_excess_mm",
            "external_inflow_m3",
            "controlled_outflow_m3",
            "storage_start_m3",
            "storage_end_m3",
        ):
            _positive(getattr(self, field_name), f"{prefix}.{field_name}", allow_zero=True)
        return self


def calculate_pcx0_mass_balance(
    catchment_area_ha: float,
    intervals: Sequence[PcxInterval],
    *,
    relative_residual_tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Check event volume conservation from method-approved excess rainfall."""

    area_ha = _positive(catchment_area_ha, "catchment_area_ha")
    tolerance = _positive(
        relative_residual_tolerance,
        "relative_residual_tolerance",
        allow_zero=True,
    )
    if tolerance > 0.05:
        raise ValueError("relative_residual_tolerance cannot exceed 0.05")
    if not intervals:
        raise ValueError("intervals cannot be empty")

    area_m2 = area_ha * 10_000.0
    rainfall_volume = 0.0
    external_volume = 0.0
    outflow_volume = 0.0
    duration_s = 0.0
    interval_results: list[dict[str, Any]] = []

    previous_end: float | None = None
    for index, interval in enumerate(intervals):
        interval.validate(index)
        if previous_end is not None and not math.isclose(
            previous_end,
            float(interval.storage_start_m3),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError(f"intervals[{index}] storage is not continuous")
        rain_m3 = float(interval.rainfall_excess_mm) / 1000.0 * area_m2
        inflow_m3 = rain_m3 + float(interval.external_inflow_m3)
        storage_change_m3 = float(interval.storage_end_m3) - float(interval.storage_start_m3)
        residual_m3 = inflow_m3 - float(interval.controlled_outflow_m3) - storage_change_m3
        interval_results.append(
            {
                "index": index,
                "duration_s": float(interval.duration_s),
                "rainfall_excess_volume_m3": rain_m3,
                "total_inflow_volume_m3": inflow_m3,
                "controlled_outflow_volume_m3": float(interval.controlled_outflow_m3),
                "storage_change_m3": storage_change_m3,
                "mass_balance_residual_m3": residual_m3,
                "mean_inflow_rate_m3_s": inflow_m3 / float(interval.duration_s),
                "mean_outflow_rate_m3_s": float(interval.controlled_outflow_m3)
                / float(interval.duration_s),
            }
        )
        rainfall_volume += rain_m3
        external_volume += float(interval.external_inflow_m3)
        outflow_volume += float(interval.controlled_outflow_m3)
        duration_s += float(interval.duration_s)
        previous_end = float(interval.storage_end_m3)

    storage_change = float(intervals[-1].storage_end_m3) - float(intervals[0].storage_start_m3)
    total_input = rainfall_volume + external_volume
    residual = total_input - outflow_volume - storage_change
    denominator = max(total_input, outflow_volume + abs(storage_change), 1e-12)
    relative_residual = abs(residual) / denominator
    continuity_passed = relative_residual <= tolerance + 1e-15

    return {
        "release": PCX_RELEASE,
        "method": "DECLARED_EXCESS_RAINFALL_EVENT_VOLUME_BALANCE",
        "catchment_area_ha": area_ha,
        "duration_s": duration_s,
        "rainfall_excess_volume_m3": rainfall_volume,
        "external_inflow_volume_m3": external_volume,
        "total_input_volume_m3": total_input,
        "controlled_outflow_volume_m3": outflow_volume,
        "storage_change_m3": storage_change,
        "mass_balance_residual_m3": residual,
        "relative_mass_balance_residual": relative_residual,
        "relative_residual_tolerance": tolerance,
        "numeric_continuity_status": "PASS" if continuity_passed else "FAIL",
        "peak_mean_interval_inflow_m3_s": max(
            item["mean_inflow_rate_m3_s"] for item in interval_results
        ),
        "peak_mean_interval_outflow_m3_s": max(
            item["mean_outflow_rate_m3_s"] for item in interval_results
        ),
        "intervals": interval_results,
        "rainfall_excess_method_evaluated": False,
        "hydraulic_capacity_evaluated": False,
        "receiver_approved": False,
        "guidance_authorized": False,
        "limitations": list(RELEASE_LIMITATIONS),
    }


def validate_release_boundary(result: dict[str, Any]) -> None:
    """Reject promotion of a PCE0/PCX0 result beyond its screening boundary."""

    forbidden_true = (
        "hydraulic_capacity_evaluated",
        "practice_recommendation_authorized",
        "receiver_approved",
        "guidance_authorized",
    )
    for key in forbidden_true:
        if result.get(key) is True:
            raise ValueError(f"{key} cannot be true in PCE0/PCX0")
    limitations = set(result.get("limitations", []))
    if not set(RELEASE_LIMITATIONS).issubset(limitations):
        raise ValueError("PCE0/PCX0 release limitations are incomplete")
