"""Mass-conserving lag routing over a directed acyclic reach network."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Mapping, Sequence


ROUTING_RELEASE = "PRELIMINARY_DAG_LAG_ROUTING_ONLY"
ROUTING_LIMITATIONS = (
    "TRAVEL_TIMES_REQUIRE_PROJECT_EVIDENCE",
    "NO_ATTENUATION",
    "NO_BACKWATER",
    "NO_FLOW_SPLITS",
    "NO_CHANNEL_OR_STRUCTURE_CAPACITY",
    "NO_FAILURE_PATH",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)


def _series(rows: Sequence[Mapping[str, Any]], label: str) -> list[tuple[float, float]]:
    if len(rows) < 2:
        raise ValueError(f"{label} must contain at least two rows")
    parsed: list[tuple[float, float]] = []
    for index, row in enumerate(rows):
        time_s = row.get("time_s")
        flow = row.get("flow_m3_s")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (time_s, flow)):
            raise ValueError(f"{label}[{index}] time and flow must be numeric")
        point = (float(time_s), float(flow))
        if not all(math.isfinite(value) for value in point) or point[0] < 0 or point[1] < 0:
            raise ValueError(f"{label}[{index}] contains an invalid value")
        if parsed and point[0] <= parsed[-1][0]:
            raise ValueError(f"{label} times must be strictly increasing")
        parsed.append(point)
    if parsed[0][1] != 0 or parsed[-1][1] != 0:
        raise ValueError(f"{label} must start and end at zero flow")
    return parsed


def _flow_at(series: Sequence[tuple[float, float]], time_s: float) -> float:
    if time_s < series[0][0] or time_s > series[-1][0]:
        return 0.0
    for (left_t, left_q), (right_t, right_q) in zip(series, series[1:]):
        if left_t <= time_s <= right_t:
            fraction = (time_s - left_t) / (right_t - left_t)
            return left_q + fraction * (right_q - left_q)
    return 0.0


def _combine(series_list: Sequence[Sequence[tuple[float, float]]]) -> list[tuple[float, float]]:
    times = sorted({time for series in series_list for time, _ in series})
    return [(time, sum(_flow_at(series, time) for series in series_list)) for time in times]


def _volume(series: Sequence[tuple[float, float]]) -> float:
    return sum((q0 + q1) / 2.0 * (t1 - t0) for (t0, q0), (t1, q1) in zip(series, series[1:]))


def route_hydrographs_by_lag(
    reaches: Sequence[Mapping[str, Any]],
    lateral_hydrographs_by_node: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Route piecewise-linear hydrographs through a dendritic DAG by pure lag."""

    if not reaches:
        raise ValueError("reaches cannot be empty")
    if not lateral_hydrographs_by_node:
        raise ValueError("lateral_hydrographs_by_node cannot be empty")

    parsed_reaches: dict[str, dict[str, Any]] = {}
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for index, reach in enumerate(reaches):
        reach_id = str(reach.get("id") or "").strip()
        upstream = str(reach.get("upstream_node_id") or "").strip()
        downstream = str(reach.get("downstream_node_id") or "").strip()
        travel_time = reach.get("travel_time_s")
        if not reach_id or not upstream or not downstream or upstream == downstream:
            raise ValueError(f"reaches[{index}] identifiers are invalid")
        if reach_id in parsed_reaches:
            raise ValueError("reach ids must be unique")
        if isinstance(travel_time, bool) or not isinstance(travel_time, (int, float)):
            raise ValueError(f"reaches[{index}].travel_time_s must be numeric")
        travel_time = float(travel_time)
        if not math.isfinite(travel_time) or travel_time < 0:
            raise ValueError(f"reaches[{index}].travel_time_s must be finite and non-negative")
        parsed_reaches[reach_id] = {
            "id": reach_id,
            "upstream_node_id": upstream,
            "downstream_node_id": downstream,
            "travel_time_s": travel_time,
        }
        outgoing[upstream].append(reach_id)
        incoming[downstream].append(reach_id)
        nodes.update((upstream, downstream))

    split_nodes = sorted(node for node, reach_ids in outgoing.items() if len(reach_ids) > 1)
    if split_nodes:
        raise ValueError("flow splits require explicit allocation and are not supported")

    indegree = {node: len(incoming[node]) for node in nodes}
    queue = deque(sorted(node for node in nodes if indegree[node] == 0))
    node_order: list[str] = []
    while queue:
        node = queue.popleft()
        node_order.append(node)
        for reach_id in outgoing[node]:
            downstream = parsed_reaches[reach_id]["downstream_node_id"]
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)
    if len(node_order) != len(nodes):
        raise ValueError("reach network must be acyclic")

    lateral = {
        str(node): _series(rows, f"lateral_hydrographs_by_node[{node}]")
        for node, rows in lateral_hydrographs_by_node.items()
    }
    unknown_nodes = sorted(set(lateral) - nodes)
    if unknown_nodes:
        raise ValueError("lateral hydrograph references an unknown node")

    reach_outputs: dict[str, list[tuple[float, float]]] = {}
    node_hydrographs: dict[str, list[tuple[float, float]]] = {}
    for node in node_order:
        contributors: list[Sequence[tuple[float, float]]] = []
        if node in lateral:
            contributors.append(lateral[node])
        contributors.extend(reach_outputs[reach_id] for reach_id in incoming[node])
        if contributors:
            combined = _combine(contributors)
            node_hydrographs[node] = combined
            for reach_id in outgoing[node]:
                lag = parsed_reaches[reach_id]["travel_time_s"]
                reach_outputs[reach_id] = [(time + lag, flow) for time, flow in combined]

    dry_reaches = sorted(set(parsed_reaches) - set(reach_outputs))
    outlet_nodes = sorted(node for node in nodes if not outgoing[node])
    outlet_series = {node: node_hydrographs[node] for node in outlet_nodes if node in node_hydrographs}
    input_volume = sum(_volume(series) for series in lateral.values())
    outlet_volume = sum(_volume(series) for series in outlet_series.values())
    residual = outlet_volume - input_volume
    relative_residual = abs(residual) / max(input_volume, 1e-12)

    def rows(series: Sequence[tuple[float, float]]) -> list[dict[str, float]]:
        cumulative = 0.0
        result: list[dict[str, float]] = []
        for index, (time, flow) in enumerate(series):
            if index:
                prior_time, prior_flow = series[index - 1]
                cumulative += (prior_flow + flow) / 2.0 * (time - prior_time)
            result.append({"time_s": time, "flow_m3_s": flow, "cumulative_volume_m3": cumulative})
        return result

    reach_results = []
    for reach_id, definition in parsed_reaches.items():
        series = reach_outputs.get(reach_id, [])
        peak = max(series, key=lambda item: (item[1], -item[0])) if series else (0.0, 0.0)
        reach_results.append({
            **definition,
            "status": "ROUTED" if series else "DRY_NO_UPSTREAM_INFLOW",
            "peak_flow_m3_s": peak[1],
            "time_to_peak_s": peak[0],
            "hydrograph": rows(series) if series else [],
        })

    return {
        "release": ROUTING_RELEASE,
        "method": "PURE_LAG_DENDRITIC_NETWORK_ROUTING",
        "reach_count": len(parsed_reaches),
        "node_count": len(nodes),
        "outlet_node_ids": outlet_nodes,
        "dry_reach_ids": dry_reaches,
        "input_volume_m3": input_volume,
        "outlet_volume_m3": outlet_volume,
        "mass_balance_residual_m3": residual,
        "relative_mass_balance_residual": relative_residual,
        "mass_balance_status": "PASS" if relative_residual <= 1e-10 else "FAIL",
        "reaches": reach_results,
        "outlets": {node: rows(series) for node, series in outlet_series.items()},
        "attenuation_evaluated": False,
        "backwater_evaluated": False,
        "hydraulic_capacity_evaluated": False,
        "failure_path_evaluated": False,
        "guidance_authorized": False,
        "limitations": list(ROUTING_LIMITATIONS),
    }


def validate_routing_release(result: Mapping[str, Any]) -> None:
    if result.get("release") != ROUTING_RELEASE:
        raise ValueError("routing release changed")
    if result.get("mass_balance_status") != "PASS":
        raise ValueError("routing does not conserve volume")
    for key in (
        "attenuation_evaluated", "backwater_evaluated", "hydraulic_capacity_evaluated",
        "failure_path_evaluated", "guidance_authorized",
    ):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false")
    if not set(ROUTING_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("routing limitations are incomplete")
