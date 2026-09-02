"""Volume-conserving triangular unit-hydrograph screening kernel."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


PCX2_RELEASE = "PCX2_TRIANGULAR_UNIT_HYDROGRAPH_SCREENING_ONLY"
PCX2_LIMITATIONS = (
    "LAG_REQUIRES_PROJECT_EVIDENCE",
    "TRIANGULAR_SHAPE_REQUIRES_PROJECT_APPROVAL",
    "NO_CHANNEL_ROUTING",
    "NO_STRUCTURE_ROUTING",
    "NOT_HYDRAULIC_CAPACITY",
    "NOT_SECTION_DIMENSIONING",
    "NOT_RECEIVER_APPROVAL",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def calculate_pcx2_triangular_hydrograph(
    rainfall_excess_intervals: Sequence[Mapping[str, Any]],
    *,
    catchment_area_ha: float,
    lag_time_s: float,
    output_step_s: float,
    base_to_peak_time_ratio: float = 2.67,
) -> dict[str, Any]:
    """Superpose triangular responses while preserving excess-runoff volume."""

    area_ha = _positive(catchment_area_ha, "catchment_area_ha")
    lag = _positive(lag_time_s, "lag_time_s")
    step = _positive(output_step_s, "output_step_s")
    ratio = _positive(base_to_peak_time_ratio, "base_to_peak_time_ratio")
    if ratio <= 1:
        raise ValueError("base_to_peak_time_ratio must exceed 1")
    if not rainfall_excess_intervals:
        raise ValueError("rainfall_excess_intervals cannot be empty")

    pulses: list[dict[str, float]] = []
    interval_start = 0.0
    input_volume = 0.0
    breakpoints = {0.0}
    for index, item in enumerate(rainfall_excess_intervals):
        duration = _positive(item.get("duration_s"), f"intervals[{index}].duration_s")
        excess_value = item.get("rainfall_excess_mm")
        if isinstance(excess_value, bool) or not isinstance(excess_value, (int, float)):
            raise ValueError(f"intervals[{index}].rainfall_excess_mm must be numeric")
        excess = float(excess_value)
        if not math.isfinite(excess) or excess < 0:
            raise ValueError(f"intervals[{index}].rainfall_excess_mm must be finite and non-negative")
        volume = excess / 1000.0 * area_ha * 10_000.0
        time_to_peak = lag + duration / 2.0
        base_duration = ratio * time_to_peak
        peak_time = interval_start + time_to_peak
        end_time = interval_start + base_duration
        peak_flow = 2.0 * volume / base_duration if volume else 0.0
        pulses.append(
            {
                "start_s": interval_start,
                "peak_s": peak_time,
                "end_s": end_time,
                "peak_flow_m3_s": peak_flow,
                "volume_m3": volume,
            }
        )
        breakpoints.update((interval_start, peak_time, end_time))
        input_volume += volume
        interval_start += duration

    maximum_time = max(pulse["end_s"] for pulse in pulses)
    grid_count = int(math.floor(maximum_time / step))
    breakpoints.update(index * step for index in range(grid_count + 1))
    breakpoints.add(maximum_time)
    times = sorted(breakpoints)

    def pulse_flow(pulse: Mapping[str, float], time_s: float) -> float:
        start = pulse["start_s"]
        peak = pulse["peak_s"]
        end = pulse["end_s"]
        peak_flow = pulse["peak_flow_m3_s"]
        if time_s <= start or time_s >= end or peak_flow == 0:
            return 0.0
        if time_s <= peak:
            return peak_flow * (time_s - start) / (peak - start)
        return peak_flow * (end - time_s) / (end - peak)

    rows: list[dict[str, float]] = []
    cumulative_volume = 0.0
    previous_time: float | None = None
    previous_flow = 0.0
    for time_s in times:
        flow = sum(pulse_flow(pulse, time_s) for pulse in pulses)
        if previous_time is not None:
            cumulative_volume += (previous_flow + flow) / 2.0 * (time_s - previous_time)
        rows.append(
            {
                "time_s": time_s,
                "flow_m3_s": flow,
                "cumulative_volume_m3": cumulative_volume,
            }
        )
        previous_time = time_s
        previous_flow = flow

    peak_row = max(rows, key=lambda row: (row["flow_m3_s"], -row["time_s"]))
    residual = cumulative_volume - input_volume
    relative_residual = abs(residual) / max(input_volume, 1e-12)
    return {
        "release": PCX2_RELEASE,
        "method": "TRIANGULAR_UNIT_HYDROGRAPH_SUPERPOSITION",
        "catchment_area_ha": area_ha,
        "lag_time_s": lag,
        "output_step_s": step,
        "base_to_peak_time_ratio": ratio,
        "input_rainfall_excess_volume_m3": input_volume,
        "hydrograph_volume_m3": cumulative_volume,
        "mass_balance_residual_m3": residual,
        "relative_mass_balance_residual": relative_residual,
        "mass_balance_status": "PASS" if relative_residual <= 1e-10 else "FAIL",
        "peak_flow_m3_s": peak_row["flow_m3_s"],
        "time_to_peak_s": peak_row["time_s"],
        "hydrograph_duration_s": maximum_time,
        "hydrograph": rows,
        "channel_routing_evaluated": False,
        "structure_routing_evaluated": False,
        "hydraulic_capacity_evaluated": False,
        "receiver_approved": False,
        "guidance_authorized": False,
        "limitations": list(PCX2_LIMITATIONS),
    }


def validate_pcx2_release(result: Mapping[str, Any]) -> None:
    if result.get("release") != PCX2_RELEASE:
        raise ValueError("PCX2 release changed")
    if result.get("mass_balance_status") != "PASS":
        raise ValueError("PCX2 hydrograph does not conserve input volume")
    for key in (
        "channel_routing_evaluated",
        "structure_routing_evaluated",
        "hydraulic_capacity_evaluated",
        "receiver_approved",
        "guidance_authorized",
    ):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false in PCX2")
    if not set(PCX2_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("PCX2 limitations are incomplete")
