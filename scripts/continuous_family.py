"""Continuous, integrable row-family primitives for the CF0 screening stage.

The functions in this module deliberately have no GDAL/QGIS dependency.  A
caller supplies regular-grid arrays and receives NumPy arrays, Shapely lines
and JSON-safe quality metrics.  The solver is a geometric screening tool: it
does not size terraces, authorize hydraulic discharge or prove that a planter
can execute a headland manoeuvre.

Angles represent *axes*, not directed vectors.  Consequently ``theta`` and
``theta + pi`` are exactly equivalent.  The scalar phase is reconstructed on
one continuous mask; an analytical preference boundary can influence the
orientation cost, but it is never used as a clipping boundary.

Grid convention: column indices increase with mathematical ``x`` and row
indices increase with mathematical ``y``.  A north-up raster whose row index
increases southward must be vertically flipped, together with every component
array, before entering this module (and flipped back only when publishing).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_edt, gaussian_filter, label
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import lsqr
from shapely import linestrings
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree


_EPS = 1e-12
_HALO_LSQ_MINIMUM_NEIGHBOR_COUNT = 3
_HALO_LSQ_REQUIRED_RANK = 2
_HALO_GRADIENT_COMPONENT_CONNECTIVITY = 4
_CONTOUR_CELL_CHUNK_SIZE = 32_768


@dataclass(frozen=True)
class ContinuousFamilyPolicy:
    """Numerical and geometric gates for a continuous phase family.

    ``minimum_work_path_radius_m`` applies to the row centreline while the
    implement is working.  It is intentionally unrelated to a headland turn
    radius, which needs a classified manoeuvre surface and swept-envelope test.
    """

    grid_resolution_m: float
    row_spacing_m: float
    orientation_smoothing_m: float
    contour_preference: float = 0.5
    minimum_orientation_coherence: float = 0.20
    maximum_low_coherence_fraction: float = 0.02
    maximum_axial_jump_deg: float = 55.0
    maximum_abrupt_edge_fraction: float = 0.0
    maximum_cycle_conflict_fraction: float = 0.0
    integrability_iterations: int = 8
    integrability_convergence_tolerance: float = 1e-4
    phase_scale_min: float = 0.40
    phase_scale_max: float = 2.50
    phase_scale_regularization: float = 1.0
    phase_scale_relaxation: float = 0.65
    lsqr_tolerance: float = 1e-9
    lsqr_iteration_limit: int = 4000
    maximum_integrability_residual_rms: float = 0.18
    maximum_integrability_angular_error_p95_deg: float = 22.5
    maximum_eikonal_residual_p95: float = 0.35
    minimum_phase_gradient: float = 0.30
    maximum_critical_fraction: float = 0.01
    cut_locus_laplacian_threshold: float = 1.25
    maximum_cut_locus_fraction: float = 0.02
    spacing_tolerance_fraction: float = 0.20
    minimum_contour_length_m: float = 3.0
    minimum_work_path_radius_m: float | None = None
    minimum_terrain_gradient: float = 1e-9

    def __post_init__(self) -> None:
        positive = {
            "grid_resolution_m": self.grid_resolution_m,
            "row_spacing_m": self.row_spacing_m,
            "minimum_phase_gradient": self.minimum_phase_gradient,
            "phase_scale_min": self.phase_scale_min,
            "phase_scale_max": self.phase_scale_max,
            "lsqr_tolerance": self.lsqr_tolerance,
            "integrability_convergence_tolerance": self.integrability_convergence_tolerance,
            "minimum_contour_length_m": self.minimum_contour_length_m,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(self.orientation_smoothing_m) or self.orientation_smoothing_m < 0:
            raise ValueError("orientation_smoothing_m must be finite and non-negative")
        if not 0 <= self.contour_preference <= 1:
            raise ValueError("contour_preference must be in [0, 1]")
        if not 0 <= self.minimum_orientation_coherence <= 1:
            raise ValueError("minimum_orientation_coherence must be in [0, 1]")
        for name in (
            "maximum_low_coherence_fraction",
            "maximum_abrupt_edge_fraction",
            "maximum_cycle_conflict_fraction",
            "maximum_critical_fraction",
            "maximum_cut_locus_fraction",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if not 0 < self.maximum_axial_jump_deg <= 90:
            raise ValueError("maximum_axial_jump_deg must be in (0, 90]")
        if self.integrability_iterations < 1:
            raise ValueError("integrability_iterations must be positive")
        if self.lsqr_iteration_limit < 1:
            raise ValueError("lsqr_iteration_limit must be positive")
        if self.phase_scale_max < self.phase_scale_min:
            raise ValueError("phase_scale_max cannot be smaller than phase_scale_min")
        if self.phase_scale_regularization < 0:
            raise ValueError("phase_scale_regularization must be non-negative")
        if not 0 < self.phase_scale_relaxation <= 1:
            raise ValueError("phase_scale_relaxation must be in (0, 1]")
        if self.maximum_integrability_residual_rms < 0:
            raise ValueError("maximum_integrability_residual_rms must be non-negative")
        if self.maximum_eikonal_residual_p95 < 0:
            raise ValueError("maximum_eikonal_residual_p95 must be non-negative")
        if not 0 <= self.maximum_integrability_angular_error_p95_deg <= 90:
            raise ValueError(
                "maximum_integrability_angular_error_p95_deg must be in [0, 90]"
            )
        if not 0 <= self.spacing_tolerance_fraction < 1:
            raise ValueError("spacing_tolerance_fraction must be in [0, 1)")
        if self.minimum_work_path_radius_m is not None:
            if (
                not math.isfinite(self.minimum_work_path_radius_m)
                or self.minimum_work_path_radius_m <= 0
            ):
                raise ValueError("minimum_work_path_radius_m must be positive when provided")


def _as_grid(array: np.ndarray | Sequence[Sequence[float]], name: str) -> np.ndarray:
    result = np.asarray(array, dtype=float)
    if result.ndim != 2:
        raise ValueError(f"{name} must be a two-dimensional array")
    return result


def _validate_same_shape(mask: np.ndarray, *arrays: np.ndarray) -> None:
    if mask.ndim != 2:
        raise ValueError("mask must be a two-dimensional array")
    if any(array.shape != mask.shape for array in arrays):
        raise ValueError("all grid arrays must have the same shape")


def _percentile(values: np.ndarray, percentile: float) -> float | None:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.percentile(finite, percentile)) if finite.size else None


def _json_float(value: float | np.floating | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def axial_from_angles(
    angles: np.ndarray | Sequence[float] | float,
    *,
    degrees: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Encode an unoriented axis as ``(cos(2 theta), sin(2 theta))``.

    Doubling the angle removes the sign ambiguity.  This is the representation
    used for blending and smoothing orientation preferences.
    """

    theta = np.asarray(angles, dtype=float)
    if not np.isfinite(theta).all():
        raise ValueError("angles must be finite")
    if degrees:
        theta = np.deg2rad(theta)
    return np.cos(2.0 * theta), np.sin(2.0 * theta)


def smooth_axial_field(
    mask: np.ndarray,
    qx: np.ndarray,
    qy: np.ndarray,
    resolution_m: float,
    orientation_smoothing_m: float | None = None,
    policy: ContinuousFamilyPolicy | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Smooth an axial field without ever averaging ``theta`` directly."""

    if isinstance(orientation_smoothing_m, ContinuousFamilyPolicy) and policy is None:
        policy = orientation_smoothing_m
        orientation_smoothing_m = None
    valid = np.asarray(mask, dtype=bool)
    qx = _as_grid(qx, "qx")
    qy = _as_grid(qy, "qy")
    _validate_same_shape(valid, qx, qy)
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be finite and positive")
    if np.any(~np.isfinite(qx[valid])) or np.any(~np.isfinite(qy[valid])):
        raise ValueError("axial components must be finite inside mask")
    if orientation_smoothing_m is None:
        orientation_smoothing_m = policy.orientation_smoothing_m if policy else 0.0
    if not math.isfinite(orientation_smoothing_m) or orientation_smoothing_m < 0:
        raise ValueError("orientation_smoothing_m must be finite and non-negative")

    magnitude = np.hypot(qx, qy)
    if np.any(magnitude[valid] <= _EPS):
        raise ValueError("axial field has zero-magnitude cells inside mask")
    unit_x = np.zeros(valid.shape, dtype=float)
    unit_y = np.zeros(valid.shape, dtype=float)
    unit_x[valid] = qx[valid] / magnitude[valid]
    unit_y[valid] = qy[valid] / magnitude[valid]

    sigma_cells = orientation_smoothing_m / resolution_m
    if sigma_cells > 1e-9:
        weights = gaussian_filter(valid.astype(float), sigma_cells, mode="constant", cval=0.0)
        averaged_x = gaussian_filter(unit_x, sigma_cells, mode="constant", cval=0.0)
        averaged_y = gaussian_filter(unit_y, sigma_cells, mode="constant", cval=0.0)
        averaged_x = np.divide(averaged_x, weights, out=np.zeros_like(averaged_x), where=weights > _EPS)
        averaged_y = np.divide(averaged_y, weights, out=np.zeros_like(averaged_y), where=weights > _EPS)
    else:
        averaged_x = unit_x
        averaged_y = unit_y

    coherence = np.hypot(averaged_x, averaged_y)
    smoothed_x = np.full(valid.shape, np.nan, dtype=float)
    smoothed_y = np.full(valid.shape, np.nan, dtype=float)
    usable = valid & (coherence > _EPS)
    smoothed_x[usable] = averaged_x[usable] / coherence[usable]
    smoothed_y[usable] = averaged_y[usable] / coherence[usable]
    coherence_out = np.full(valid.shape, np.nan, dtype=float)
    coherence_out[valid] = coherence[valid]

    threshold = policy.minimum_orientation_coherence if policy else 0.0
    low = valid & (coherence < threshold)
    valid_count = int(np.count_nonzero(valid))
    low_fraction = float(np.count_nonzero(low) / valid_count) if valid_count else 1.0
    maximum_low_fraction = policy.maximum_low_coherence_fraction if policy else 1.0
    blockers = []
    if np.any(valid & ~usable):
        blockers.append("AXIAL_SMOOTHING_ZERO_COHERENCE")
    if low_fraction > maximum_low_fraction + 1e-12:
        blockers.append("LOW_ORIENTATION_COHERENCE")
    qa = {
        "valid_cell_count": valid_count,
        "smoothing_sigma_cells": float(sigma_cells),
        "coherence_min": _percentile(coherence[valid], 0),
        "coherence_p05": _percentile(coherence[valid], 5),
        "coherence_p50": _percentile(coherence[valid], 50),
        "low_coherence_cell_count": int(np.count_nonzero(low)),
        "low_coherence_fraction": low_fraction,
        "blocker_codes": blockers,
        "accepted": not blockers,
    }
    return smoothed_x, smoothed_y, coherence_out, qa


def _neighbours(row: int, column: int, rows: int, columns: int):
    for dr, dc in ((-1, 0), (0, -1), (0, 1), (1, 0)):
        rr, cc = row + dr, column + dc
        if 0 <= rr < rows and 0 <= cc < columns:
            yield rr, cc


def _nearest_valid_anchor(mask: np.ndarray, anchor: Sequence[float] | None) -> tuple[int, int, tuple[float, float] | None]:
    valid_indices = np.argwhere(mask)
    if valid_indices.size == 0:
        raise ValueError("mask must contain at least one valid cell")
    direction = None
    if anchor is None:
        row, column = map(int, valid_indices[0])
        return row, column, direction
    if len(anchor) not in (2, 4):
        raise ValueError("anchor must be (row, column) or (row, column, nx, ny)")
    requested = np.asarray(anchor[:2], dtype=float)
    if not np.isfinite(requested).all():
        raise ValueError("anchor indices must be finite")
    distances = np.sum((valid_indices - requested[None, :]) ** 2, axis=1)
    row, column = map(int, valid_indices[int(np.argmin(distances))])
    if len(anchor) == 4:
        vector = np.asarray(anchor[2:4], dtype=float)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or norm <= _EPS:
            raise ValueError("anchor direction must be finite and non-zero")
        direction = (float(vector[0] / norm), float(vector[1] / norm))
    return row, column, direction


def lift_axial_normal(
    mask: np.ndarray,
    qx: np.ndarray,
    qy: np.ndarray,
    anchor: Sequence[float] | None = None,
    policy: ContinuousFamilyPolicy | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Lift a signless axial normal into a coherent directed normal field.

    A four-neighbour graph transports the sign from an anchor.  QA is evaluated
    on every graph edge, including non-tree edges, so a topological sign defect
    cannot be hidden by the traversal order.
    """

    valid = np.asarray(mask, dtype=bool)
    qx = _as_grid(qx, "qx")
    qy = _as_grid(qy, "qy")
    _validate_same_shape(valid, qx, qy)
    if np.any(~np.isfinite(qx[valid])) or np.any(~np.isfinite(qy[valid])):
        raise ValueError("axial components must be finite inside mask")
    magnitude = np.hypot(qx, qy)
    if np.any(magnitude[valid] <= _EPS):
        raise ValueError("axial field has zero-magnitude cells inside mask")
    uqx = np.full(valid.shape, np.nan, dtype=float)
    uqy = np.full(valid.shape, np.nan, dtype=float)
    uqx[valid] = qx[valid] / magnitude[valid]
    uqy[valid] = qy[valid] / magnitude[valid]
    half_angle = 0.5 * np.arctan2(uqy, uqx)
    candidate_x = np.cos(half_angle)
    candidate_y = np.sin(half_angle)

    nx = np.full(valid.shape, np.nan, dtype=float)
    ny = np.full(valid.shape, np.nan, dtype=float)
    visited = np.zeros(valid.shape, dtype=bool)
    rows, columns = valid.shape
    first_row, first_column, anchor_direction = _nearest_valid_anchor(valid, anchor)
    seeds = [(first_row, first_column)] + [
        tuple(map(int, item))
        for item in np.argwhere(valid)
        if (int(item[0]), int(item[1])) != (first_row, first_column)
    ]
    component_count = 0
    for seed_row, seed_column in seeds:
        if visited[seed_row, seed_column]:
            continue
        component_count += 1
        sx = float(candidate_x[seed_row, seed_column])
        sy = float(candidate_y[seed_row, seed_column])
        preferred = anchor_direction if component_count == 1 else None
        if preferred is not None:
            if sx * preferred[0] + sy * preferred[1] < 0:
                sx, sy = -sx, -sy
        elif sx < -_EPS or (abs(sx) <= _EPS and sy < 0):
            sx, sy = -sx, -sy
        nx[seed_row, seed_column] = sx
        ny[seed_row, seed_column] = sy
        visited[seed_row, seed_column] = True
        queue = deque([(seed_row, seed_column)])
        while queue:
            row, column = queue.popleft()
            for rr, cc in _neighbours(row, column, rows, columns):
                if not valid[rr, cc] or visited[rr, cc]:
                    continue
                vx = float(candidate_x[rr, cc])
                vy = float(candidate_y[rr, cc])
                if nx[row, column] * vx + ny[row, column] * vy < 0:
                    vx, vy = -vx, -vy
                nx[rr, cc] = vx
                ny[rr, cc] = vy
                visited[rr, cc] = True
                queue.append((rr, cc))

    axial_jumps: list[float] = []
    directed_dots: list[float] = []
    for row, column in np.argwhere(valid):
        row, column = int(row), int(column)
        for rr, cc in ((row, column + 1), (row + 1, column)):
            if rr >= rows or cc >= columns or not valid[rr, cc]:
                continue
            doubled_dot = float(np.clip(uqx[row, column] * uqx[rr, cc] + uqy[row, column] * uqy[rr, cc], -1, 1))
            axial_jumps.append(math.degrees(0.5 * math.acos(doubled_dot)))
            directed_dots.append(float(nx[row, column] * nx[rr, cc] + ny[row, column] * ny[rr, cc]))

    jumps = np.asarray(axial_jumps, dtype=float)
    dots = np.asarray(directed_dots, dtype=float)
    jump_limit = policy.maximum_axial_jump_deg if policy else 90.0
    abrupt = jumps > jump_limit + 1e-9
    # A negative transported dot on a locally non-orthogonal edge exposes a
    # sign seam around a cycle.  Orthogonal preferences are an abrupt-axis
    # defect and are counted separately above.
    cycle_conflicts = dots < -1e-8
    edge_count = int(jumps.size)
    abrupt_fraction = float(np.count_nonzero(abrupt) / edge_count) if edge_count else 0.0
    cycle_fraction = float(np.count_nonzero(cycle_conflicts) / edge_count) if edge_count else 0.0
    max_abrupt = policy.maximum_abrupt_edge_fraction if policy else 1.0
    max_cycle = policy.maximum_cycle_conflict_fraction if policy else 1.0
    blockers = []
    if abrupt_fraction > max_abrupt + 1e-12:
        blockers.append("AXIAL_ORIENTATION_DISCONTINUITY")
    if cycle_fraction > max_cycle + 1e-12:
        blockers.append("AXIAL_LIFT_CYCLE_CONFLICT")
    qa = {
        "sign_lift_status": "COHERENT" if not blockers else "FRUSTRATED_OR_DISCONTINUOUS",
        "connected_component_count": component_count,
        "graph_edge_count": edge_count,
        "axial_jump_max_deg": _percentile(jumps, 100),
        "axial_jump_p95_deg": _percentile(jumps, 95),
        "abrupt_edge_count": int(np.count_nonzero(abrupt)),
        "abrupt_edge_fraction": abrupt_fraction,
        "cycle_conflict_edge_count": int(np.count_nonzero(cycle_conflicts)),
        "cycle_conflict_fraction": cycle_fraction,
        "cycle_frustration_edge_count": int(np.count_nonzero(cycle_conflicts)),
        "cycle_frustration_fraction": cycle_fraction,
        "blocker_codes": blockers,
        "accepted": not blockers,
    }
    return nx, ny, qa


def _masked_gradient(values: np.ndarray, mask: np.ndarray, resolution_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Finite differences on a mask, with centred and then one-sided stencils."""

    values = np.asarray(values, dtype=float)
    valid = np.asarray(mask, dtype=bool) & np.isfinite(values)
    gx = np.full(values.shape, np.nan, dtype=float)
    gy = np.full(values.shape, np.nan, dtype=float)

    if values.shape[1] > 2:
        both = valid[:, 1:-1] & valid[:, :-2] & valid[:, 2:]
        target = gx[:, 1:-1]
        target[both] = (values[:, 2:][both] - values[:, :-2][both]) / (2.0 * resolution_m)
    if values.shape[1] > 1:
        forward = valid[:, :-1] & valid[:, 1:] & ~np.isfinite(gx[:, :-1])
        target = gx[:, :-1]
        target[forward] = (values[:, 1:][forward] - values[:, :-1][forward]) / resolution_m
        backward = valid[:, 1:] & valid[:, :-1] & ~np.isfinite(gx[:, 1:])
        target = gx[:, 1:]
        target[backward] = (values[:, 1:][backward] - values[:, :-1][backward]) / resolution_m

    if values.shape[0] > 2:
        both = valid[1:-1, :] & valid[:-2, :] & valid[2:, :]
        target = gy[1:-1, :]
        target[both] = (values[2:, :][both] - values[:-2, :][both]) / (2.0 * resolution_m)
    if values.shape[0] > 1:
        forward = valid[:-1, :] & valid[1:, :] & ~np.isfinite(gy[:-1, :])
        target = gy[:-1, :]
        target[forward] = (values[1:, :][forward] - values[:-1, :][forward]) / resolution_m
        backward = valid[1:, :] & valid[:-1, :] & ~np.isfinite(gy[1:, :])
        target = gy[1:, :]
        target[backward] = (values[1:, :][backward] - values[:-1, :][backward]) / resolution_m
    return gx, gy


def extrapolate_phase_halo(
    phase: np.ndarray,
    mask: np.ndarray,
    resolution_m: float,
    halo_cells: int = 1,
    gradient_lsq_max_radius_cells: int = 2,
    gradient_lsq_max_relative_residual: float = 0.30,
    gradient_lsq_max_condition_number: float = 100.0,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Extend a solved phase into a numerical halo for contour extraction.

    The scalar field is not re-solved outside ``mask``. Each halo cell uses a
    first-order extrapolation from its nearest valid cell and that cell's
    complete phase gradient. Missing finite-difference components are replaced
    only by a rank-2 local plane fit within the same connected component. The
    LSQ residual is normalized by ``max(resolution_m, RMS(local phase
    differences))``. A halo cell whose source has no well-conditioned,
    sufficiently planar complete gradient is removed from the support
    (fail-closed). The halo lets an isoline cross the exact vector work edge
    before the caller clips it; it is never a work surface.
    """

    values = _as_grid(phase, "phase")
    valid = np.asarray(mask, dtype=bool)
    _validate_same_shape(valid, values)
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be finite and positive")
    if isinstance(halo_cells, bool) or not isinstance(halo_cells, (int, np.integer)):
        raise ValueError("halo_cells must be a positive integer")
    if int(halo_cells) <= 0:
        raise ValueError("halo_cells must be a positive integer")
    if (
        isinstance(gradient_lsq_max_radius_cells, bool)
        or not isinstance(gradient_lsq_max_radius_cells, (int, np.integer))
        or int(gradient_lsq_max_radius_cells) <= 0
    ):
        raise ValueError("gradient_lsq_max_radius_cells must be a positive integer")
    if (
        not math.isfinite(gradient_lsq_max_relative_residual)
        or gradient_lsq_max_relative_residual <= 0
    ):
        raise ValueError("gradient_lsq_max_relative_residual must be finite and positive")
    if (
        not math.isfinite(gradient_lsq_max_condition_number)
        or gradient_lsq_max_condition_number < 1
    ):
        raise ValueError("gradient_lsq_max_condition_number must be finite and at least one")
    if not np.any(valid):
        raise ValueError("mask must contain at least one valid phase cell")
    if not np.isfinite(values[valid]).all():
        raise ValueError("phase must be finite inside mask")

    halo_cells = int(halo_cells)
    gradient_lsq_max_radius_cells = int(gradient_lsq_max_radius_cells)
    support = binary_dilation(valid, iterations=halo_cells)
    halo = support & ~valid
    extended = np.array(values, dtype=float, copy=True)
    if not np.any(halo):
        return extended, support, {
            "method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
            "halo_cells": halo_cells,
            "halo_cell_count": 0,
            "supported_halo_cell_count": 0,
            "unsupported_halo_cell_count": 0,
            "direct_gradient_source_count": 0,
            "lsq_gradient_source_count": 0,
            "unestimable_gradient_source_count": 0,
            "missing_gradient_component_count": 0,
            "gradient_lsq_max_radius_cells": gradient_lsq_max_radius_cells,
            "gradient_lsq_minimum_neighbor_count": _HALO_LSQ_MINIMUM_NEIGHBOR_COUNT,
            "gradient_lsq_required_rank": _HALO_LSQ_REQUIRED_RANK,
            "gradient_component_connectivity": _HALO_GRADIENT_COMPONENT_CONNECTIVITY,
            "gradient_lsq_max_radius_used_cells": 0,
            "gradient_lsq_max_relative_residual": gradient_lsq_max_relative_residual,
            "gradient_lsq_max_condition_number": gradient_lsq_max_condition_number,
            "gradient_lsq_residual_rms_max": None,
            "gradient_lsq_relative_residual_max": None,
            "gradient_lsq_condition_number_max": None,
            "gradient_estimation_status": "PASS",
        }

    gradient_x, gradient_y = _masked_gradient(values, valid, resolution_m)
    _, nearest = distance_transform_edt(~valid, return_indices=True)
    nearest_row = nearest[0]
    nearest_column = nearest[1]
    rows, columns = np.indices(valid.shape)
    source_x = np.array(gradient_x[nearest_row, nearest_column], copy=True)
    source_y = np.array(gradient_y[nearest_row, nearest_column], copy=True)
    missing_components = int(
        np.count_nonzero(~np.isfinite(source_x[halo]))
        + np.count_nonzero(~np.isfinite(source_y[halo]))
    )
    component_labels, _ = label(
        valid,
        structure=np.asarray([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int),
    )
    halo_sources = np.unique(
        np.column_stack((nearest_row[halo], nearest_column[halo])), axis=0
    )
    direct_source_count = 0
    lsq_source_count = 0
    unestimable_source_count = 0
    maximum_radius_used = 0
    lsq_residuals: list[float] = []
    lsq_relative_residuals: list[float] = []
    lsq_condition_numbers: list[float] = []
    source_estimates: dict[tuple[int, int], tuple[float, float] | None] = {}

    for source_row_value, source_column_value in halo_sources:
        source_row_value = int(source_row_value)
        source_column_value = int(source_column_value)
        key = (source_row_value, source_column_value)
        direct_x = float(gradient_x[key])
        direct_y = float(gradient_y[key])
        if math.isfinite(direct_x) and math.isfinite(direct_y):
            source_estimates[key] = (direct_x, direct_y)
            direct_source_count += 1
            continue

        component = int(component_labels[key])
        estimate = None
        for radius in range(1, gradient_lsq_max_radius_cells + 1):
            row_start = max(0, source_row_value - radius)
            row_stop = min(valid.shape[0], source_row_value + radius + 1)
            column_start = max(0, source_column_value - radius)
            column_stop = min(valid.shape[1], source_column_value + radius + 1)
            local_rows, local_columns = np.nonzero(
                component_labels[row_start:row_stop, column_start:column_stop]
                == component
            )
            local_rows = local_rows + row_start
            local_columns = local_columns + column_start
            not_source = (local_rows != source_row_value) | (
                local_columns != source_column_value
            )
            local_rows = local_rows[not_source]
            local_columns = local_columns[not_source]
            if local_rows.size < _HALO_LSQ_MINIMUM_NEIGHBOR_COUNT:
                continue
            design = np.column_stack(
                (
                    (local_columns - source_column_value) * resolution_m,
                    (local_rows - source_row_value) * resolution_m,
                )
            ).astype(float)
            if np.linalg.matrix_rank(design) < _HALO_LSQ_REQUIRED_RANK:
                continue
            condition_number = float(np.linalg.cond(design))
            if (
                not math.isfinite(condition_number)
                or condition_number > gradient_lsq_max_condition_number
            ):
                continue
            response = (
                values[local_rows, local_columns]
                - values[source_row_value, source_column_value]
            )
            coefficients, _, rank_value, _ = np.linalg.lstsq(
                design, response, rcond=None
            )
            if rank_value < _HALO_LSQ_REQUIRED_RANK or not np.isfinite(coefficients).all():
                continue
            residual = response - design @ coefficients
            residual_rms = float(np.sqrt(np.mean(residual * residual)))
            response_rms = float(np.sqrt(np.mean(response * response)))
            relative_residual = residual_rms / max(resolution_m, response_rms)
            if (
                not math.isfinite(relative_residual)
                or relative_residual > gradient_lsq_max_relative_residual
            ):
                continue
            estimate = (float(coefficients[0]), float(coefficients[1]))
            maximum_radius_used = max(maximum_radius_used, radius)
            lsq_residuals.append(residual_rms)
            lsq_relative_residuals.append(relative_residual)
            lsq_condition_numbers.append(condition_number)
            break
        source_estimates[key] = estimate
        if estimate is None:
            unestimable_source_count += 1
        else:
            lsq_source_count += 1

    unsupported_halo = np.zeros(valid.shape, dtype=bool)
    for source, estimate in source_estimates.items():
        uses_source = halo & (nearest_row == source[0]) & (nearest_column == source[1])
        if estimate is None:
            unsupported_halo |= uses_source
            continue
        source_x[uses_source] = estimate[0]
        source_y[uses_source] = estimate[1]
    supported_halo = halo & ~unsupported_halo
    support[unsupported_halo] = False
    extended[unsupported_halo] = np.nan
    extended[supported_halo] = (
        values[nearest_row[supported_halo], nearest_column[supported_halo]]
        + source_x[supported_halo]
        * (columns[supported_halo] - nearest_column[supported_halo])
        * resolution_m
        + source_y[supported_halo]
        * (rows[supported_halo] - nearest_row[supported_halo])
        * resolution_m
    )
    return extended, support, {
        "method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
        "halo_cells": halo_cells,
        "halo_cell_count": int(np.count_nonzero(halo)),
        "supported_halo_cell_count": int(np.count_nonzero(supported_halo)),
        "unsupported_halo_cell_count": int(np.count_nonzero(unsupported_halo)),
        "direct_gradient_source_count": direct_source_count,
        "lsq_gradient_source_count": lsq_source_count,
        "unestimable_gradient_source_count": unestimable_source_count,
        "missing_gradient_component_count": missing_components,
        "gradient_lsq_max_radius_cells": gradient_lsq_max_radius_cells,
        "gradient_lsq_minimum_neighbor_count": _HALO_LSQ_MINIMUM_NEIGHBOR_COUNT,
        "gradient_lsq_required_rank": _HALO_LSQ_REQUIRED_RANK,
        "gradient_component_connectivity": _HALO_GRADIENT_COMPONENT_CONNECTIVITY,
        "gradient_lsq_max_radius_used_cells": maximum_radius_used,
        "gradient_lsq_max_relative_residual": gradient_lsq_max_relative_residual,
        "gradient_lsq_max_condition_number": gradient_lsq_max_condition_number,
        "gradient_lsq_residual_rms_max": max(lsq_residuals) if lsq_residuals else None,
        "gradient_lsq_relative_residual_max": (
            max(lsq_relative_residuals) if lsq_relative_residuals else None
        ),
        "gradient_lsq_condition_number_max": (
            max(lsq_condition_numbers) if lsq_condition_numbers else None
        ),
        "gradient_estimation_status": (
            "FAIL_CLOSED" if unestimable_source_count else "PASS"
        ),
    }


def _phase_matrix(mask: np.ndarray, resolution_m: float):
    valid_indices = np.argwhere(mask)
    node_count = len(valid_indices)
    node_ids = np.full(mask.shape, -1, dtype=np.int64)
    node_ids[mask] = np.arange(node_count, dtype=np.int64)

    # Preserve the former row-major, horizontal-then-vertical edge order while
    # avoiding Python work per cell.  At one-metre resolution this topology can
    # contain millions of edges and is rebuilt for every candidate profile.
    if node_count:
        rows = valid_indices[:, 0]
        columns = valid_indices[:, 1]
        has_horizontal = columns + 1 < mask.shape[1]
        has_horizontal[has_horizontal] &= mask[
            rows[has_horizontal], columns[has_horizontal] + 1
        ]
        has_vertical = rows + 1 < mask.shape[0]
        has_vertical[has_vertical] &= mask[
            rows[has_vertical] + 1, columns[has_vertical]
        ]
        edge_counts = has_horizontal.astype(np.int8) + has_vertical.astype(np.int8)
        edge_count = int(np.sum(edge_counts, dtype=np.int64))
        edge_a = np.empty((edge_count, 2), dtype=np.int64)
        edge_b = np.empty((edge_count, 2), dtype=np.int64)
        axes = np.empty(edge_count, dtype=np.int8)
        first_positions = np.cumsum(edge_counts, dtype=np.int64) - edge_counts

        horizontal_positions = first_positions[has_horizontal]
        edge_a[horizontal_positions] = valid_indices[has_horizontal]
        edge_b[horizontal_positions] = valid_indices[has_horizontal] + (0, 1)
        axes[horizontal_positions] = 0

        vertical_positions = first_positions[has_vertical] + has_horizontal[has_vertical]
        edge_a[vertical_positions] = valid_indices[has_vertical]
        edge_b[vertical_positions] = valid_indices[has_vertical] + (1, 0)
        axes[vertical_positions] = 1
    else:
        edge_count = 0
        edge_a = np.empty((0, 2), dtype=np.int64)
        edge_b = np.empty((0, 2), dtype=np.int64)
        axes = np.empty(0, dtype=np.int8)

    component_labels, component_count = label(
        mask,
        structure=np.asarray([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int),
    )
    anchors = []
    for component in range(1, component_count + 1):
        first = np.argwhere(component_labels == component)[0]
        anchors.append((int(first[0]), int(first[1])))

    anchor_count = len(anchors)
    row_ids = np.empty(edge_count * 2 + anchor_count, dtype=np.int64)
    column_ids = np.empty(edge_count * 2 + anchor_count, dtype=np.int64)
    data = np.empty(edge_count * 2 + anchor_count, dtype=float)
    inverse_resolution = 1.0 / resolution_m
    equations = np.arange(edge_count, dtype=np.int64)
    row_ids[: edge_count * 2 : 2] = equations
    row_ids[1 : edge_count * 2 : 2] = equations
    column_ids[: edge_count * 2 : 2] = node_ids[edge_a[:, 0], edge_a[:, 1]]
    column_ids[1 : edge_count * 2 : 2] = node_ids[edge_b[:, 0], edge_b[:, 1]]
    data[: edge_count * 2 : 2] = -inverse_resolution
    data[1 : edge_count * 2 : 2] = inverse_resolution
    if anchor_count:
        anchor_array = np.asarray(anchors, dtype=np.int64)
        row_ids[edge_count * 2 :] = edge_count + np.arange(
            anchor_count, dtype=np.int64
        )
        column_ids[edge_count * 2 :] = node_ids[
            anchor_array[:, 0], anchor_array[:, 1]
        ]
        data[edge_count * 2 :] = 1.0
    matrix = coo_matrix(
        (data, (row_ids, column_ids)),
        shape=(edge_count + len(anchors), node_count),
        dtype=float,
    ).tocsr()
    return matrix, node_ids, edge_a, edge_b, axes, anchors


def _phase_edge_target(
    scale: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    edge_a: np.ndarray,
    edge_b: np.ndarray,
    axes: np.ndarray,
) -> np.ndarray:
    """Build the LSQR edge target without a Python loop per grid edge."""

    first_x = nx[edge_a[:, 0], edge_a[:, 1]]
    first_y = ny[edge_a[:, 0], edge_a[:, 1]]
    second_x = nx[edge_b[:, 0], edge_b[:, 1]]
    second_y = ny[edge_b[:, 0], edge_b[:, 1]]
    first_component = np.where(axes == 0, first_x, first_y)
    second_component = np.where(axes == 0, second_x, second_y)
    first_scale = scale[edge_a[:, 0], edge_a[:, 1]]
    second_scale = scale[edge_b[:, 0], edge_b[:, 1]]
    return 0.5 * (
        first_scale * first_component + second_scale * second_component
    )


def _solve_phase_internal(
    mask: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    resolution_m: float,
    policy: ContinuousFamilyPolicy,
) -> tuple[np.ndarray, np.ndarray, dict]:
    matrix, node_ids, edge_a, edge_b, axes, anchors = _phase_matrix(mask, resolution_m)
    node_count = int(np.count_nonzero(mask))
    if node_count == 0:
        raise ValueError("mask must contain at least one valid cell")
    scale = np.ones(mask.shape, dtype=float)
    convergence: list[float] = []
    convergence_reached = False
    last_lsqr = None
    phase = np.full(mask.shape, np.nan, dtype=float)

    for iteration in range(policy.integrability_iterations + 1):
        target = _phase_edge_target(scale, nx, ny, edge_a, edge_b, axes)
        rhs = np.concatenate((target, np.zeros(len(anchors), dtype=float)))
        last_lsqr = lsqr(
            matrix,
            rhs,
            atol=policy.lsqr_tolerance,
            btol=policy.lsqr_tolerance,
            iter_lim=policy.lsqr_iteration_limit,
            show=False,
        )
        phase[mask] = last_lsqr[0][node_ids[mask]]
        if iteration == policy.integrability_iterations or convergence_reached:
            break

        gx, gy = _masked_gradient(phase, mask, resolution_m)
        projected = gx * nx + gy * ny
        finite = mask & np.isfinite(projected)
        regularization = policy.phase_scale_regularization
        proposed = np.ones(mask.shape, dtype=float)
        proposed[finite] = (projected[finite] + regularization) / (1.0 + regularization)
        proposed[finite] = np.clip(
            proposed[finite], policy.phase_scale_min, policy.phase_scale_max
        )
        median = float(np.median(proposed[finite])) if np.any(finite) else 1.0
        if math.isfinite(median) and median > _EPS:
            proposed[finite] = np.clip(
                proposed[finite] / median,
                policy.phase_scale_min,
                policy.phase_scale_max,
            )
        updated = scale.copy()
        updated[finite] = (
            (1.0 - policy.phase_scale_relaxation) * scale[finite]
            + policy.phase_scale_relaxation * proposed[finite]
        )
        delta = float(np.max(np.abs(updated[finite] - scale[finite]))) if np.any(finite) else 0.0
        convergence.append(delta)
        scale = updated
        convergence_reached = delta <= policy.integrability_convergence_tolerance

    gx, gy = _masked_gradient(phase, mask, resolution_m)
    usable = mask & np.isfinite(gx) & np.isfinite(gy)
    gradient_magnitude = np.hypot(gx, gy)
    residual = np.full(mask.shape, np.nan, dtype=float)
    residual[usable] = np.hypot(
        gx[usable] - scale[usable] * nx[usable],
        gy[usable] - scale[usable] * ny[usable],
    )
    relative_residual = np.full(mask.shape, np.nan, dtype=float)
    relative_residual[usable] = residual[usable] / np.maximum(scale[usable], _EPS)
    cosine = np.full(mask.shape, np.nan, dtype=float)
    cosine[usable] = np.clip(
        (gx[usable] * nx[usable] + gy[usable] * ny[usable])
        / np.maximum(gradient_magnitude[usable], _EPS),
        -1.0,
        1.0,
    )
    angular_error = np.full(mask.shape, np.nan, dtype=float)
    angular_error[usable] = np.degrees(np.arccos(cosine[usable]))
    eikonal_residual = np.full(mask.shape, np.nan, dtype=float)
    eikonal_residual[usable] = np.abs(gradient_magnitude[usable] - 1.0)

    target_x = scale * nx
    target_y = scale * ny
    d_target_y_dx, _ = _masked_gradient(target_y, mask, resolution_m)
    _, d_target_x_dy = _masked_gradient(target_x, mask, resolution_m)
    curl = d_target_y_dx - d_target_x_dy
    curl_values = np.abs(curl[usable]) * resolution_m
    critical = usable & (gradient_magnitude < policy.minimum_phase_gradient)
    usable_count = int(np.count_nonzero(usable))
    critical_fraction = float(np.count_nonzero(critical) / usable_count) if usable_count else 1.0
    residual_rms = (
        float(np.sqrt(np.mean(relative_residual[usable] ** 2))) if usable_count else math.inf
    )
    angle_p95 = _percentile(angular_error[usable], 95)
    eikonal_p95 = _percentile(eikonal_residual[usable], 95)
    blockers = []
    if residual_rms > policy.maximum_integrability_residual_rms + 1e-12:
        blockers.append("NONINTEGRABLE_ORIENTATION_RESIDUAL")
    if angle_p95 is None or angle_p95 > policy.maximum_integrability_angular_error_p95_deg + 1e-12:
        blockers.append("PHASE_ORIENTATION_MISFIT")
    if eikonal_p95 is None or eikonal_p95 > policy.maximum_eikonal_residual_p95 + 1e-12:
        blockers.append("EIKONAL_RESIDUAL")
    if critical_fraction > policy.maximum_critical_fraction + 1e-12:
        blockers.append("CRITICAL_PHASE_GRADIENT")
    qa = {
        "valid_cell_count": node_count,
        "usable_gradient_cell_count": usable_count,
        "sparse_equation_count": int(matrix.shape[0]),
        "sparse_unknown_count": int(matrix.shape[1]),
        "connected_component_count": len(anchors),
        "phase_gauge": {
            "method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
            "anchor_phase_value_m": 0.0,
            "anchor_grid_indices": [
                {"row": int(row), "column": int(column)}
                for row, column in anchors
            ],
        },
        "alternation_iteration_count": len(convergence),
        "alternation_maximum_iteration_count": policy.integrability_iterations,
        "alternation_convergence_tolerance": policy.integrability_convergence_tolerance,
        "alternation_converged": convergence_reached,
        "alternation_max_delta_history": convergence,
        "lsqr_stop_code": int(last_lsqr[1]) if last_lsqr else None,
        "lsqr_iteration_count": int(last_lsqr[2]) if last_lsqr else None,
        "lsqr_residual_norm": float(last_lsqr[3]) if last_lsqr else None,
        "phase_scale_min": _percentile(scale[mask], 0),
        "phase_scale_p50": _percentile(scale[mask], 50),
        "phase_scale_max": _percentile(scale[mask], 100),
        "phase_gradient_p05": _percentile(gradient_magnitude[usable], 5),
        "phase_gradient_p50": _percentile(gradient_magnitude[usable], 50),
        "phase_gradient_p95": _percentile(gradient_magnitude[usable], 95),
        "phase_gradient_min": _percentile(gradient_magnitude[usable], 0),
        "eikonal_residual_p50": _percentile(eikonal_residual[usable], 50),
        "eikonal_residual_p95": eikonal_p95,
        "relative_residual_rms": _json_float(residual_rms),
        "relative_residual_p95": _percentile(relative_residual[usable], 95),
        "integrability_residual_rms": _json_float(residual_rms),
        "integrability_residual_p95": _percentile(relative_residual[usable], 95),
        "angular_error_p50_deg": _percentile(angular_error[usable], 50),
        "angular_error_p95_deg": angle_p95,
        "orientation_misalignment_p95_deg": angle_p95,
        "curl_abs_p95_cell_inverse": _percentile(curl_values, 95),
        "critical_cell_count": int(np.count_nonzero(critical)),
        "critical_fraction": critical_fraction,
        "blocker_codes": blockers,
        "accepted": not blockers,
    }
    return phase, scale, qa


def project_integrable_phase(
    mask: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    resolution_m: float,
    policy: ContinuousFamilyPolicy,
) -> tuple[np.ndarray, dict]:
    """Project a lifted normal field onto a scalar phase with positive scale.

    The sparse least-squares system fits edge differences of ``phase`` to
    ``lambda * n``.  ``lambda`` is alternated with the phase, remains positive,
    and is weakly regularized around one so phase-level metres remain close to
    physical normal metres.

    Array row indices are interpreted as increasing mathematical ``y``.
    """

    valid = np.asarray(mask, dtype=bool)
    nx = _as_grid(nx, "nx")
    ny = _as_grid(ny, "ny")
    _validate_same_shape(valid, nx, ny)
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be finite and positive")
    if abs(resolution_m - policy.grid_resolution_m) > max(1e-9, resolution_m * 1e-9):
        raise ValueError("resolution_m must match policy.grid_resolution_m")
    if np.any(~np.isfinite(nx[valid])) or np.any(~np.isfinite(ny[valid])):
        raise ValueError("normal components must be finite inside mask")
    norm = np.hypot(nx, ny)
    if np.any(norm[valid] <= _EPS):
        raise ValueError("normal field has zero-magnitude cells inside mask")
    unit_x = np.full(valid.shape, np.nan, dtype=float)
    unit_y = np.full(valid.shape, np.nan, dtype=float)
    unit_x[valid] = nx[valid] / norm[valid]
    unit_y[valid] = ny[valid] / norm[valid]
    phase, _, qa = _solve_phase_internal(valid, unit_x, unit_y, resolution_m, policy)
    return phase, qa


def detect_critical_indicators(
    mask: np.ndarray,
    phase: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    resolution_m: float,
    policy: ContinuousFamilyPolicy,
    coherence: np.ndarray | None = None,
) -> dict:
    """Detect critical-gradient, singular-orientation and cut-locus proxies."""

    valid = np.asarray(mask, dtype=bool)
    phase = _as_grid(phase, "phase")
    nx = _as_grid(nx, "nx")
    ny = _as_grid(ny, "ny")
    _validate_same_shape(valid, phase, nx, ny)
    gx, gy = _masked_gradient(phase, valid, resolution_m)
    magnitude = np.hypot(gx, gy)
    finite_gradient = valid & np.isfinite(magnitude)
    critical = finite_gradient & (magnitude < policy.minimum_phase_gradient)

    dgx_dx, dgx_dy = _masked_gradient(gx, finite_gradient, resolution_m)
    dgy_dx, dgy_dy = _masked_gradient(gy, finite_gradient, resolution_m)
    laplacian = dgx_dx + dgy_dy
    # Scale the Laplacian by row spacing.  High values plus a weak phase
    # gradient are a useful numerical proxy for a medial-axis/cut-locus region;
    # it remains a screening indicator rather than a geomorphological proof.
    scaled_laplacian = np.abs(laplacian) * policy.row_spacing_m
    cut_locus = finite_gradient & (
        (scaled_laplacian > policy.cut_locus_laplacian_threshold)
        | (magnitude < policy.minimum_phase_gradient)
    )

    singular = np.zeros(valid.shape, dtype=bool)
    if coherence is not None:
        coherence = _as_grid(coherence, "coherence")
        _validate_same_shape(valid, coherence)
        singular |= valid & (
            ~np.isfinite(coherence) | (coherence < policy.minimum_orientation_coherence)
        )
    for row, column in np.argwhere(valid):
        row, column = int(row), int(column)
        for rr, cc in ((row, column + 1), (row + 1, column)):
            if rr >= valid.shape[0] or cc >= valid.shape[1] or not valid[rr, cc]:
                continue
            dot = abs(float(nx[row, column] * nx[rr, cc] + ny[row, column] * ny[rr, cc]))
            jump = math.degrees(math.acos(float(np.clip(dot, -1, 1))))
            if jump > policy.maximum_axial_jump_deg + 1e-9:
                singular[row, column] = True
                singular[rr, cc] = True

    count = int(np.count_nonzero(valid))
    critical_fraction = float(np.count_nonzero(critical) / count) if count else 1.0
    singular_fraction = float(np.count_nonzero(singular) / count) if count else 1.0
    cut_fraction = float(np.count_nonzero(cut_locus) / count) if count else 1.0
    blockers = []
    if critical_fraction > policy.maximum_critical_fraction + 1e-12:
        blockers.append("CRITICAL_PHASE_GRADIENT")
    if singular_fraction > policy.maximum_low_coherence_fraction + 1e-12:
        blockers.append("ORIENTATION_SINGULARITY")
    if cut_fraction > policy.maximum_cut_locus_fraction + 1e-12:
        blockers.append("CUT_LOCUS_INDICATOR")
    return {
        "critical_mask": critical,
        "singular_mask": singular,
        "cut_locus_mask": cut_locus,
        "critical_cell_count": int(np.count_nonzero(critical)),
        "critical_fraction": critical_fraction,
        "singular_cell_count": int(np.count_nonzero(singular)),
        "singular_fraction": singular_fraction,
        "cut_locus_cell_count": int(np.count_nonzero(cut_locus)),
        "cut_locus_fraction": cut_fraction,
        "scaled_laplacian_p95": _percentile(scaled_laplacian[finite_gradient], 95),
        "blocker_codes": blockers,
        "accepted": not blockers,
    }


def solve_continuous_phase(
    mask: np.ndarray,
    elevation_gx: np.ndarray,
    elevation_gy: np.ndarray,
    resolution: float,
    base_theta_deg: np.ndarray | float,
    contour_weight: np.ndarray | float | None,
    policy: ContinuousFamilyPolicy,
) -> dict:
    """Build one continuous CF0 phase from base and contour preferences.

    ``base_theta_deg`` is the preferred *row tangent*.  Terrain contours use a
    tangent perpendicular to the supplied elevation gradient.  The blend is
    performed in doubled-angle space; its boundary changes a cost only and is
    never added to the extraction mask.
    """

    valid = np.asarray(mask, dtype=bool)
    gx = _as_grid(elevation_gx, "elevation_gx")
    gy = _as_grid(elevation_gy, "elevation_gy")
    _validate_same_shape(valid, gx, gy)
    if np.any(~np.isfinite(gx[valid])) or np.any(~np.isfinite(gy[valid])):
        raise ValueError("elevation gradients must be finite inside mask")
    if abs(resolution - policy.grid_resolution_m) > max(1e-9, resolution * 1e-9):
        raise ValueError("resolution must match policy.grid_resolution_m")

    theta = np.asarray(base_theta_deg, dtype=float)
    if theta.ndim == 0:
        theta = np.full(valid.shape, float(theta), dtype=float)
    if theta.shape != valid.shape or np.any(~np.isfinite(theta[valid])):
        raise ValueError("base_theta_deg must be finite and scalar or mask-shaped")
    weight_value = policy.contour_preference if contour_weight is None else contour_weight
    weight = np.asarray(weight_value, dtype=float)
    if weight.ndim == 0:
        weight = np.full(valid.shape, float(weight), dtype=float)
    if weight.shape != valid.shape or np.any(~np.isfinite(weight[valid])):
        raise ValueError("contour_weight must be finite and scalar or mask-shaped")
    if np.any((weight[valid] < 0) | (weight[valid] > 1)):
        raise ValueError("contour_weight must be in [0, 1]")

    base_qx, base_qy = axial_from_angles(theta, degrees=True)
    terrain_magnitude = np.hypot(gx, gy)
    contour_theta = np.arctan2(gy, gx) + math.pi / 2.0
    contour_qx, contour_qy = axial_from_angles(contour_theta)
    effective_weight = weight.copy()
    effective_weight[terrain_magnitude <= policy.minimum_terrain_gradient] = 0.0
    blended_x = (1.0 - effective_weight) * base_qx + effective_weight * contour_qx
    blended_y = (1.0 - effective_weight) * base_qy + effective_weight * contour_qy
    blend_coherence = np.hypot(blended_x, blended_y)
    # Keep arrays numerically defined so smoothing can proceed, but retain zero
    # blend coherence as a blocker in QA.
    cancelled = valid & (blend_coherence <= _EPS)
    blended_x[cancelled] = base_qx[cancelled]
    blended_y[cancelled] = base_qy[cancelled]

    row_qx, row_qy, smoothing_coherence, smoothing_qa = smooth_axial_field(
        valid,
        blended_x,
        blended_y,
        resolution,
        policy=policy,
    )
    combined_coherence = np.full(valid.shape, np.nan, dtype=float)
    combined_coherence[valid] = np.minimum(
        blend_coherence[valid], smoothing_coherence[valid]
    )
    low = valid & (combined_coherence < policy.minimum_orientation_coherence)
    low_fraction = float(np.count_nonzero(low) / np.count_nonzero(valid)) if np.any(valid) else 1.0
    if np.any(cancelled) and "AXIAL_BLEND_CANCELLATION" not in smoothing_qa["blocker_codes"]:
        smoothing_qa["blocker_codes"].append("AXIAL_BLEND_CANCELLATION")
    if low_fraction > policy.maximum_low_coherence_fraction + 1e-12:
        if "LOW_ORIENTATION_COHERENCE" not in smoothing_qa["blocker_codes"]:
            smoothing_qa["blocker_codes"].append("LOW_ORIENTATION_COHERENCE")
    smoothing_qa["blend_coherence_p05"] = _percentile(blend_coherence[valid], 5)
    smoothing_qa["combined_low_coherence_fraction"] = low_fraction
    smoothing_qa["accepted"] = not smoothing_qa["blocker_codes"]

    # Rotating an axis by 90 degrees negates its doubled-angle components.
    normal_qx = -row_qx
    normal_qy = -row_qy
    nx, ny, lift_qa = lift_axial_normal(valid, normal_qx, normal_qy, policy=policy)
    phase, phase_qa = project_integrable_phase(valid, nx, ny, resolution, policy)
    indicator_qa = detect_critical_indicators(
        valid,
        phase,
        nx,
        ny,
        resolution,
        policy,
        combined_coherence,
    )
    spacing_qa = measure_normal_spacing(
        phase,
        valid,
        resolution,
        policy.row_spacing_m,
        policy,
    )

    blockers = []
    for section in (smoothing_qa, lift_qa, phase_qa, indicator_qa):
        for code in section["blocker_codes"]:
            if code not in blockers:
                blockers.append(code)
    qa = {
        "accepted": not blockers,
        "gate_status": "GEOMETRIC_PASS" if not blockers else "NO_FEASIBLE_FAMILY",
        "blocker_codes": blockers,
        "orientation": smoothing_qa,
        "axial_lift": lift_qa,
        "integrability": phase_qa,
        "critical_indicators": {
            key: value
            for key, value in indicator_qa.items()
            if not key.endswith("_mask")
        },
        "normal_spacing": spacing_qa,
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
    }
    return {
        "phase": phase,
        "nx": nx,
        "ny": ny,
        "orientation_qx": row_qx,
        "orientation_qy": row_qy,
        "orientation_coherence": combined_coherence,
        "critical_mask": indicator_qa["critical_mask"],
        "singular_mask": indicator_qa["singular_mask"],
        "cut_locus_mask": indicator_qa["cut_locus_mask"],
        "qa": qa,
    }


def _triangle_segment(points: Sequence[tuple[float, float]], values: Sequence[float], level_value: float):
    intersections: list[tuple[float, float]] = []
    scale = max(1.0, max(abs(float(value)) for value in values), abs(level_value))
    tolerance = scale * 1e-12
    for first, second in ((0, 1), (1, 2), (2, 0)):
        va = float(values[first]) - level_value
        vb = float(values[second]) - level_value
        pa, pb = points[first], points[second]
        if abs(va) <= tolerance and abs(vb) <= tolerance:
            continue
        if abs(va) <= tolerance:
            intersections.append(pa)
        elif abs(vb) <= tolerance:
            intersections.append(pb)
        elif va * vb < 0:
            fraction = va / (va - vb)
            intersections.append(
                (
                    pa[0] + fraction * (pb[0] - pa[0]),
                    pa[1] + fraction * (pb[1] - pa[1]),
                )
            )
    unique: list[tuple[float, float]] = []
    for point in intersections:
        if not any(math.dist(point, existing) <= tolerance for existing in unique):
            unique.append(point)
    if len(unique) < 2:
        return None
    if len(unique) > 2:
        first, second = max(
            ((a, b) for index, a in enumerate(unique) for b in unique[index + 1 :]),
            key=lambda pair: math.dist(pair[0], pair[1]),
        )
    else:
        first, second = unique
    if math.dist(first, second) <= tolerance:
        return None
    return LineString((first, second))


def _lines_from_merged(geometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    lines: list[LineString] = []
    for part in getattr(geometry, "geoms", ()):
        lines.extend(_lines_from_merged(part))
    return lines


def _snap_segment_to_local_grid(
    segment: LineString,
    origin_x: float,
    origin_y: float,
    tolerance: float,
) -> LineString | None:
    """Snap interpolation noise relative to the local origin, not world zero.

    LSQR phase values can put two representations of the same marching vertex
    a few micrometres apart.  Relative snapping makes their graph nodes exactly
    equal and preserves coordinate-translation invariance for large UTM values.
    """

    coordinates = []
    for x_value, y_value, *_ in segment.coords:
        x_value = origin_x + round((x_value - origin_x) / tolerance) * tolerance
        y_value = origin_y + round((y_value - origin_y) / tolerance) * tolerance
        point = (float(x_value), float(y_value))
        if not coordinates or point != coordinates[-1]:
            coordinates.append(point)
    if len(coordinates) < 2:
        return None
    return LineString(coordinates)


def _contour_segment_coordinates_by_level(
    phase: np.ndarray,
    usable: np.ndarray,
    x_coordinates: np.ndarray,
    y_coordinates: np.ndarray,
    level_values: np.ndarray,
    snap_tolerance: float,
    *,
    cell_chunk_size: int = _CONTOUR_CELL_CHUNK_SIZE,
) -> tuple[list[list[np.ndarray]], dict[str, int]]:
    """Vectorize marching triangles while preserving the scalar traversal order.

    Cells are visited in row-major chunks. Candidate ``(cell, level)`` pairs
    are expanded with NumPy, and only vertex-on-level degeneracies use the
    scalar reference primitive. Coordinate chunks are kept numeric until the
    per-level GEOS merge, avoiding one Python ``LineString`` per triangle.
    """

    if isinstance(cell_chunk_size, bool) or not isinstance(
        cell_chunk_size, (int, np.integer)
    ):
        raise ValueError("cell_chunk_size must be a positive integer")
    if int(cell_chunk_size) <= 0:
        raise ValueError("cell_chunk_size must be a positive integer")
    cell_chunk_size = int(cell_chunk_size)
    level_count = int(level_values.size)
    coordinate_chunks: list[list[np.ndarray]] = [[] for _ in range(level_count)]
    cell_valid = (
        usable[:-1, :-1]
        & usable[:-1, 1:]
        & usable[1:, 1:]
        & usable[1:, :-1]
    )
    flat_valid_cells = np.flatnonzero(cell_valid)
    cell_column_count = int(cell_valid.shape[1]) if cell_valid.ndim == 2 else 0
    stats = {
        "grid_scan_count": 1,
        "legacy_grid_scan_count": level_count,
        "grid_cell_count": int(cell_valid.size),
        "valid_cell_count": int(flat_valid_cells.size),
        "level_count": level_count,
        "chunk_count": 0,
        "candidate_cell_level_count": 0,
        "candidate_triangle_evaluation_count": 0,
        "scalar_degeneracy_count": 0,
        "emitted_segment_count": 0,
    }
    if level_count == 0 or flat_valid_cells.size == 0:
        return coordinate_chunks, stats

    triangle_corner_indices = np.asarray(((0, 1, 2), (0, 2, 3)), dtype=np.int8)
    edge_first_indices = np.asarray((0, 1, 2), dtype=np.int8)
    edge_second_indices = np.asarray((1, 2, 0), dtype=np.int8)

    for chunk_start in range(0, flat_valid_cells.size, cell_chunk_size):
        stats["chunk_count"] += 1
        flat_cells = flat_valid_cells[chunk_start : chunk_start + cell_chunk_size]
        rows = flat_cells // cell_column_count
        columns = flat_cells % cell_column_count
        corner_values = np.column_stack(
            (
                phase[rows, columns],
                phase[rows, columns + 1],
                phase[rows + 1, columns + 1],
                phase[rows + 1, columns],
            )
        )
        lower_level_indices = np.searchsorted(
            level_values, np.min(corner_values, axis=1), side="left"
        )
        upper_level_indices = np.searchsorted(
            level_values, np.max(corner_values, axis=1), side="right"
        )
        level_counts = upper_level_indices - lower_level_indices
        active_cells = np.flatnonzero(level_counts > 0)
        if active_cells.size == 0:
            continue

        active_counts = level_counts[active_cells].astype(np.int64, copy=False)
        active_lower = lower_level_indices[active_cells].astype(np.int64, copy=False)
        cumulative_counts = np.cumsum(active_counts, dtype=np.int64)
        pair_count = int(cumulative_counts[-1])
        pair_cells = np.repeat(active_cells, active_counts)
        pair_starts = np.repeat(
            np.concatenate((np.asarray([0], dtype=np.int64), cumulative_counts[:-1])),
            active_counts,
        )
        pair_levels = (
            np.arange(pair_count, dtype=np.int64)
            - pair_starts
            + np.repeat(active_lower, active_counts)
        )
        stats["candidate_cell_level_count"] += pair_count

        corner_points = np.empty((flat_cells.size, 4, 2), dtype=float)
        corner_points[:, 0, 0] = x_coordinates[columns]
        corner_points[:, 0, 1] = y_coordinates[rows]
        corner_points[:, 1, 0] = x_coordinates[columns + 1]
        corner_points[:, 1, 1] = y_coordinates[rows]
        corner_points[:, 2, 0] = x_coordinates[columns + 1]
        corner_points[:, 2, 1] = y_coordinates[rows + 1]
        corner_points[:, 3, 0] = x_coordinates[columns]
        corner_points[:, 3, 1] = y_coordinates[rows + 1]

        triangle_cells = np.repeat(pair_cells, 2)
        triangle_kinds = np.tile(np.asarray((0, 1), dtype=np.int8), pair_count)
        candidate_levels = np.repeat(pair_levels, 2)
        selected_corners = triangle_corner_indices[triangle_kinds]
        triangle_values = corner_values[
            triangle_cells[:, np.newaxis], selected_corners
        ]
        triangle_points = corner_points[
            triangle_cells[:, np.newaxis], selected_corners, :
        ]
        level_at_triangle = level_values[candidate_levels]
        differences = triangle_values - level_at_triangle[:, np.newaxis]
        tolerance = np.maximum.reduce(
            (
                np.ones(level_at_triangle.size, dtype=float),
                np.max(np.abs(triangle_values), axis=1),
                np.abs(level_at_triangle),
            )
        ) * 1e-12
        exact_vertex = np.any(
            np.abs(differences) <= tolerance[:, np.newaxis], axis=1
        )
        edge_first = differences[:, edge_first_indices]
        edge_second = differences[:, edge_second_indices]
        crossings = edge_first * edge_second < 0
        generic = ~exact_vertex & (np.count_nonzero(crossings, axis=1) == 2)
        coordinate_pairs = np.full(
            (candidate_levels.size, 2, 2), np.nan, dtype=float
        )

        generic_indices = np.flatnonzero(generic)
        if generic_indices.size:
            generic_crossings = crossings[generic_indices]
            generic_first = edge_first[generic_indices]
            generic_second = edge_second[generic_indices]
            fractions = np.zeros(generic_first.shape, dtype=float)
            np.divide(
                generic_first,
                generic_first - generic_second,
                out=fractions,
                where=generic_crossings,
            )
            generic_points = triangle_points[generic_indices]
            edge_start_points = generic_points[:, edge_first_indices, :]
            edge_end_points = generic_points[:, edge_second_indices, :]
            edge_points = edge_start_points + fractions[:, :, np.newaxis] * (
                edge_end_points - edge_start_points
            )
            crossing_order = np.argsort(
                ~generic_crossings, axis=1, kind="stable"
            )[:, :2]
            coordinate_pairs[generic_indices] = np.take_along_axis(
                edge_points, crossing_order[:, :, np.newaxis], axis=1
            )

        degeneracy_indices = np.flatnonzero(exact_vertex)
        stats["scalar_degeneracy_count"] += int(degeneracy_indices.size)
        for candidate_index in degeneracy_indices:
            segment = _triangle_segment(
                tuple(
                    (float(x_value), float(y_value))
                    for x_value, y_value in triangle_points[candidate_index]
                ),
                tuple(float(value) for value in triangle_values[candidate_index]),
                float(level_at_triangle[candidate_index]),
            )
            if segment is not None:
                coordinate_pairs[candidate_index] = np.asarray(
                    segment.coords, dtype=float
                )

        coordinate_pairs[:, :, 0] = x_coordinates[0] + np.round(
            (coordinate_pairs[:, :, 0] - x_coordinates[0]) / snap_tolerance
        ) * snap_tolerance
        coordinate_pairs[:, :, 1] = y_coordinates[0] + np.round(
            (coordinate_pairs[:, :, 1] - y_coordinates[0]) / snap_tolerance
        ) * snap_tolerance
        finite_segments = np.isfinite(coordinate_pairs).all(axis=(1, 2))
        nonzero_segments = np.any(
            coordinate_pairs[:, 0, :] != coordinate_pairs[:, 1, :], axis=1
        )
        retained = finite_segments & nonzero_segments
        retained_coordinates = coordinate_pairs[retained]
        retained_levels = candidate_levels[retained]
        stats["candidate_triangle_evaluation_count"] += int(
            candidate_levels.size
        )
        stats["emitted_segment_count"] += int(retained_levels.size)
        if retained_levels.size == 0:
            continue

        level_order = np.argsort(retained_levels, kind="stable")
        retained_levels = retained_levels[level_order]
        retained_coordinates = retained_coordinates[level_order]
        group_starts = np.flatnonzero(
            np.concatenate(
                (
                    np.asarray([True]),
                    retained_levels[1:] != retained_levels[:-1],
                )
            )
        )
        group_stops = np.concatenate(
            (group_starts[1:], np.asarray([retained_levels.size]))
        )
        for start, stop in zip(group_starts, group_stops):
            level_index = int(retained_levels[start])
            coordinate_chunks[level_index].append(
                retained_coordinates[start:stop].copy()
            )
    return coordinate_chunks, stats


def extract_phase_contours(
    phase: np.ndarray,
    mask: np.ndarray,
    xs: np.ndarray | Sequence[float],
    ys: np.ndarray | Sequence[float],
    levels: np.ndarray | Sequence[float] | float | None = None,
    policy: ContinuousFamilyPolicy | None = None,
    *,
    spacing_m: float | None = None,
    minimum_length_m: float | None = None,
    level_range_mask: np.ndarray | None = None,
    phase_offset_m: float = 0.0,
) -> list[dict]:
    """Extract phase isolines with a deterministic marching-triangle scheme."""

    if isinstance(levels, ContinuousFamilyPolicy) and policy is None:
        policy = levels
        levels = None
    phase = _as_grid(phase, "phase")
    valid = np.asarray(mask, dtype=bool)
    _validate_same_shape(valid, phase)
    x_coordinates = np.asarray(xs, dtype=float)
    y_coordinates = np.asarray(ys, dtype=float)
    if x_coordinates.ndim != 1 or x_coordinates.size != phase.shape[1]:
        raise ValueError("xs must contain one coordinate per phase column")
    if y_coordinates.ndim != 1 or y_coordinates.size != phase.shape[0]:
        raise ValueError("ys must contain one coordinate per phase row")
    if not np.isfinite(x_coordinates).all() or not np.isfinite(y_coordinates).all():
        raise ValueError("coordinate arrays must be finite")
    if np.any(np.diff(x_coordinates) <= 0) or np.any(np.diff(y_coordinates) <= 0):
        raise ValueError("coordinate arrays must be strictly increasing")
    usable = valid & np.isfinite(phase)
    if not np.any(usable):
        return []
    if level_range_mask is None:
        level_usable = usable
    else:
        level_usable = np.asarray(level_range_mask, dtype=bool)
        _validate_same_shape(level_usable, phase)
        level_usable &= usable
        if not np.any(level_usable):
            raise ValueError("level_range_mask must include at least one usable phase cell")

    if levels is not None and np.asarray(levels).ndim == 0:
        if spacing_m is not None:
            raise ValueError("spacing was supplied twice")
        spacing_m = float(levels)
        levels = None
    if spacing_m is None:
        spacing_m = policy.row_spacing_m if policy else None
    if levels is None:
        if spacing_m is None or not math.isfinite(spacing_m) or spacing_m <= 0:
            raise ValueError("positive spacing_m or explicit levels are required")
        if (
            not math.isfinite(phase_offset_m)
            or phase_offset_m < 0
            or phase_offset_m >= spacing_m
        ):
            raise ValueError("phase_offset_m must be finite and in [0, spacing_m)")
        minimum = float(np.min(phase[level_usable]))
        maximum = float(np.max(phase[level_usable]))
        # Do not publish an isoline that merely coincides with the numerical
        # minimum/maximum boundary.  Sparse-solver error is normally several
        # orders below this fraction of one row spacing.
        tolerance = max(
            spacing_m * 1e-5,
            max(1.0, abs(minimum), abs(maximum)) * 1e-10,
        )
        first = math.ceil((minimum + tolerance - phase_offset_m) / spacing_m)
        last = math.floor((maximum - tolerance - phase_offset_m) / spacing_m)
        level_values = (
            np.arange(first, last + 1, dtype=float) * spacing_m + phase_offset_m
        )
        level_ids = np.arange(first, last + 1, dtype=int)
    else:
        if phase_offset_m != 0.0:
            raise ValueError("phase_offset_m cannot be combined with explicit levels")
        level_values = np.asarray(levels, dtype=float)
        if level_values.ndim != 1 or not np.isfinite(level_values).all():
            raise ValueError("levels must be a finite one-dimensional array")
        if level_values.size and np.any(np.diff(level_values) <= 0):
            raise ValueError("levels must be strictly increasing")
        if spacing_m is not None:
            level_ids = np.rint(level_values / spacing_m).astype(int)
        else:
            level_ids = np.arange(level_values.size, dtype=int)
    minimum_length = (
        minimum_length_m
        if minimum_length_m is not None
        else policy.minimum_contour_length_m if policy else 0.0
    )
    if not math.isfinite(minimum_length) or minimum_length < 0:
        raise ValueError("minimum_length_m must be finite and non-negative")

    results: list[dict] = []
    coordinate_step = min(
        float(np.min(np.diff(x_coordinates))),
        float(np.min(np.diff(y_coordinates))),
    )
    snap_tolerance = max(coordinate_step * 1e-5, 1e-10)
    coordinate_chunks, _ = _contour_segment_coordinates_by_level(
        phase,
        usable,
        x_coordinates,
        y_coordinates,
        level_values,
        snap_tolerance,
    )
    for level_index, (level_id, level_value) in enumerate(
        zip(level_ids, level_values)
    ):
        chunks = coordinate_chunks[level_index]
        if not chunks:
            continue
        segment_coordinates = (
            chunks[0] if len(chunks) == 1 else np.concatenate(chunks, axis=0)
        )
        merged_input = unary_union(linestrings(segment_coordinates))
        try:
            merged = linemerge(merged_input)
        except ValueError:
            merged = merged_input
        parts = sorted(
            _lines_from_merged(merged),
            key=lambda line: (
                round(line.bounds[0], 9),
                round(line.bounds[1], 9),
                round(line.bounds[2], 9),
                round(line.bounds[3], 9),
                round(line.length, 9),
            ),
        )
        for segment_index, line in enumerate(parts):
            if line.length + 1e-9 < minimum_length:
                continue
            results.append(
                {
                    "level_id": int(level_id),
                    "phase_level_m": float(level_value),
                    "segment_index": int(segment_index),
                    "geometry": line,
                    "length_m": float(line.length),
                }
            )
    return results


def phase_contours(
    phase: np.ndarray,
    mask: np.ndarray,
    x_coords: np.ndarray | Sequence[float],
    y_coords: np.ndarray | Sequence[float],
    spacing: float,
    minimum_length: float = 0.0,
    *,
    level_range_mask: np.ndarray | None = None,
    phase_offset_m: float = 0.0,
) -> list[dict]:
    """Compact runner-facing alias for :func:`extract_phase_contours`."""

    return extract_phase_contours(
        phase,
        mask,
        x_coords,
        y_coords,
        spacing_m=spacing,
        minimum_length_m=minimum_length,
        level_range_mask=level_range_mask,
        phase_offset_m=phase_offset_m,
    )


def measure_normal_spacing(
    phase: np.ndarray,
    mask: np.ndarray,
    resolution_m: float,
    phase_step_m: float,
    policy: ContinuousFamilyPolicy | None = None,
) -> dict:
    """Estimate local normal spacing as ``delta phase / |grad phase|``."""

    phase = _as_grid(phase, "phase")
    valid = np.asarray(mask, dtype=bool)
    _validate_same_shape(valid, phase)
    if resolution_m <= 0 or phase_step_m <= 0:
        raise ValueError("resolution_m and phase_step_m must be positive")
    gx, gy = _masked_gradient(phase, valid, resolution_m)
    magnitude = np.hypot(gx, gy)
    usable = valid & np.isfinite(magnitude) & (magnitude > _EPS)
    spacing = np.full(phase.shape, np.nan, dtype=float)
    spacing[usable] = phase_step_m / magnitude[usable]
    tolerance = policy.spacing_tolerance_fraction if policy else 0.20
    lower = phase_step_m * (1.0 - tolerance)
    upper = phase_step_m * (1.0 + tolerance)
    outside = usable & ((spacing < lower) | (spacing > upper))
    count = int(np.count_nonzero(usable))
    return {
        "sample_count": count,
        "target_spacing_m": float(phase_step_m),
        "tolerance_fraction": float(tolerance),
        "spacing_p05_m": _percentile(spacing[usable], 5),
        "spacing_p50_m": _percentile(spacing[usable], 50),
        "spacing_p95_m": _percentile(spacing[usable], 95),
        "outside_tolerance_count": int(np.count_nonzero(outside)),
        "outside_tolerance_fraction": (
            float(np.count_nonzero(outside) / count) if count else 1.0
        ),
        "method": "LOCAL_NORMAL_DELTA_PHASE_OVER_GRADIENT_MAGNITUDE",
    }


def _geometry_from_record(record) -> LineString:
    geometry = record.get("geometry") if isinstance(record, dict) else record
    if not isinstance(geometry, LineString):
        raise ValueError("each family line must be a Shapely LineString or a geometry record")
    return geometry


def _discrete_radii(line: LineString) -> np.ndarray:
    coordinates = np.asarray(line.coords, dtype=float)[:, :2]
    radii: list[float] = []
    for first, middle, last in zip(coordinates[:-2], coordinates[1:-1], coordinates[2:]):
        side_a = float(np.linalg.norm(middle - first))
        side_b = float(np.linalg.norm(last - middle))
        side_c = float(np.linalg.norm(last - first))
        twice_area = abs(float(np.cross(middle - first, last - first)))
        if min(side_a, side_b, side_c) <= 1e-9 or twice_area <= 1e-9:
            continue
        radii.append(side_a * side_b * side_c / (2.0 * twice_area))
    return np.asarray(radii, dtype=float)


def curvature_radius_diagnostics(
    lines: Iterable[LineString | dict],
    required_minimum_radius_m: float | None = None,
) -> dict:
    """Measure discrete osculating radii along row centrelines.

    This three-vertex diagnostic is resolution-dependent.  A passing value is
    CF0 screening evidence only; it cannot establish an executive minimum
    radius until the candidate has a declared C2 smoothing/reparameterization,
    a bounded phase-deviation check and an implement swept-envelope test.
    """

    if required_minimum_radius_m is not None and required_minimum_radius_m <= 0:
        raise ValueError("required_minimum_radius_m must be positive when provided")
    radius_arrays = [_discrete_radii(_geometry_from_record(record)) for record in lines]
    finite_arrays = [values for values in radius_arrays if values.size]
    radii = np.concatenate(finite_arrays) if finite_arrays else np.asarray([], dtype=float)
    violating = (
        radii < required_minimum_radius_m - 1e-9
        if required_minimum_radius_m is not None
        else np.zeros(radii.shape, dtype=bool)
    )
    if required_minimum_radius_m is None:
        status = "NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT"
        blockers = []
    elif np.any(violating):
        status = "FAIL_STATIC_PATH_RADIUS"
        blockers = ["MINIMUM_WORK_PATH_RADIUS_VIOLATION"]
    else:
        status = "PASS_STATIC_PATH_RADIUS_ONLY"
        blockers = []
    return {
        "radius_sample_count": int(radii.size),
        "radius_min_observed_m": _percentile(radii, 0),
        "radius_p05_observed_m": _percentile(radii, 5),
        "radius_p50_observed_m": _percentile(radii, 50),
        "radius_required_m": required_minimum_radius_m,
        "radius_violation_count": int(np.count_nonzero(violating)),
        "radius_status": status,
        "method": "DISCRETE_THREE_VERTEX_CIRCUMRADIUS_CF0_SCREENING_ONLY",
        "hard_gate_eligible": False,
        "scope_note": (
            "A pass is not executive evidence; C2 smoothing, bounded phase deviation "
            "and swept-envelope validation are still required."
        ),
        "blocker_codes": blockers,
    }


def evaluate_family_geometry(
    lines: Iterable[LineString | dict],
    policy: ContinuousFamilyPolicy,
    physical_boundary=None,
    endpoint_tolerance_m: float | None = None,
) -> dict:
    """Evaluate topology, optional endpoint support and static row radius."""

    records = list(lines)
    geometries = [_geometry_from_record(record) for record in records]
    blockers: list[str] = []
    self_intersections = sum(not geometry.is_simple for geometry in geometries)
    if self_intersections:
        blockers.append("ROW_SELF_INTERSECTION")

    crossing_pairs = 0
    touching_pairs = 0
    if geometries:
        tree = STRtree(geometries)
        for first_index, first in enumerate(geometries):
            for second_index in tree.query(first):
                second_index = int(second_index)
                if second_index <= first_index:
                    continue
                intersection = first.intersection(geometries[second_index])
                if intersection.is_empty:
                    continue
                if intersection.geom_type in ("LineString", "MultiLineString"):
                    crossing_pairs += 1
                elif first.crosses(geometries[second_index]):
                    crossing_pairs += 1
                else:
                    touching_pairs += 1
    if crossing_pairs:
        blockers.append("ROW_CROSSING_OR_OVERLAP")
    if touching_pairs:
        blockers.append("ROW_TOUCH")

    unsupported_endpoints = 0
    if physical_boundary is not None:
        tolerance = endpoint_tolerance_m
        if tolerance is None:
            tolerance = max(policy.grid_resolution_m * 1.5, 0.05)
        if tolerance < 0:
            raise ValueError("endpoint_tolerance_m must be non-negative")
        for line in geometries:
            for coordinate in (line.coords[0], line.coords[-1]):
                if Point(coordinate).distance(physical_boundary) > tolerance:
                    unsupported_endpoints += 1
        if unsupported_endpoints:
            blockers.append("INTERNAL_UNSUPPORTED_ENDPOINT")

    radius = curvature_radius_diagnostics(
        geometries,
        policy.minimum_work_path_radius_m,
    )
    for code in radius["blocker_codes"]:
        if code not in blockers:
            blockers.append(code)
    return {
        "accepted": not blockers,
        "geometry_status": "GEOMETRIC_PASS" if not blockers else "NO_FEASIBLE_FAMILY",
        "line_count": len(geometries),
        "total_length_m": float(sum(line.length for line in geometries)),
        "self_intersection_count": int(self_intersections),
        "crossing_or_overlap_pair_count": int(crossing_pairs),
        "touching_pair_count": int(touching_pairs),
        "unsupported_endpoint_count": int(unsupported_endpoints),
        "radius": radius,
        "blocker_codes": blockers,
        "scope_note": (
            "Static centreline geometry only; hydraulic sizing, swept envelope and "
            "headland manoeuvres remain unconfirmed."
        ),
    }


__all__ = [
    "ContinuousFamilyPolicy",
    "axial_from_angles",
    "smooth_axial_field",
    "lift_axial_normal",
    "project_integrable_phase",
    "solve_continuous_phase",
    "extrapolate_phase_halo",
    "extract_phase_contours",
    "phase_contours",
    "measure_normal_spacing",
    "curvature_radius_diagnostics",
    "detect_critical_indicators",
    "evaluate_family_geometry",
]
