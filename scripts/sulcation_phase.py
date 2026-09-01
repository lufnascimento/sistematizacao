"""Phase-level helpers used by the E0 sulcation geometry generator.

The adaptive level step implemented here is deliberately a screening heuristic.
It improves mean row density when ``|grad(phi)|`` differs from one, but it does
not reinitialize ``phi`` as a signed-distance field and therefore cannot promise
uniform physical spacing along an entire isoline.
"""

from __future__ import annotations

import math

import numpy as np


def adaptive_phase_levels(
    phi: np.ndarray,
    gradient_magnitude: np.ndarray,
    valid: np.ndarray,
    spacing_m: float,
) -> np.ndarray:
    """Choose phase levels that target the requested *mean* physical spacing.

    Nearby isolines satisfy ``distance ~= delta_phi / |grad(phi)|``.  A single
    ``delta_phi`` is selected for each level from the median gradient magnitude
    in a phase band.  This is useful at E0, but one scalar step cannot correct
    gradient variation along the same isoline.
    """

    if not math.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError("spacing_m must be finite and positive")
    if phi.shape != gradient_magnitude.shape or phi.shape != valid.shape:
        raise ValueError("phi, gradient_magnitude and valid must have the same shape")

    usable = valid & np.isfinite(phi) & np.isfinite(gradient_magnitude)
    if np.any(gradient_magnitude[usable] < 0):
        raise ValueError("gradient_magnitude cannot be negative")
    values = phi[usable]
    magnitudes = gradient_magnitude[usable]
    if values.size == 0:
        return np.asarray([0.0, spacing_m], dtype=float)

    level_min = math.floor(float(values.min()) / spacing_m) * spacing_m
    level_max = math.ceil(float(values.max()) / spacing_m) * spacing_m
    global_median = float(np.median(magnitudes))
    if not math.isfinite(global_median) or global_median <= 1e-6:
        global_median = 1.0

    minimum_step = spacing_m * 0.05
    maximum_levels = int(math.ceil((level_max - level_min) / minimum_step)) + 2
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_magnitudes = magnitudes[order]
    levels = [level_min]
    while levels[-1] < level_max and len(levels) < maximum_levels:
        current = levels[-1]
        step_guess = spacing_m * global_median
        band_half_width = max(step_guess * 0.5, spacing_m * 0.5)
        center = current + step_guess
        left = int(np.searchsorted(sorted_values, center - band_half_width, side="left"))
        right = int(np.searchsorted(sorted_values, center + band_half_width, side="right"))
        local_median = (
            float(np.median(sorted_magnitudes[left:right]))
            if right > left
            else global_median
        )
        if not math.isfinite(local_median) or local_median <= 1e-6:
            local_median = global_median
        levels.append(current + max(spacing_m * local_median, minimum_step))

    return np.asarray(levels, dtype=float)


def physical_spacing_from_levels(
    phi: np.ndarray,
    gradient_magnitude: np.ndarray,
    valid: np.ndarray,
    levels: np.ndarray,
) -> np.ndarray:
    """Estimate local normal spacing from the phase levels to be extracted."""

    if phi.shape != gradient_magnitude.shape or phi.shape != valid.shape:
        raise ValueError("phi, gradient_magnitude and valid must have the same shape")
    levels = np.asarray(levels, dtype=float)
    if (
        levels.ndim != 1
        or levels.size < 2
        or not np.isfinite(levels).all()
        or np.any(np.diff(levels) <= 0)
    ):
        raise ValueError("levels must be a strictly increasing one-dimensional array")

    result = np.full(phi.shape, np.nan, dtype=float)
    usable = valid & np.isfinite(phi) & np.isfinite(gradient_magnitude)
    if np.any(gradient_magnitude[usable] < 0):
        raise ValueError("gradient_magnitude cannot be negative")
    indices = np.searchsorted(levels, phi[usable], side="right") - 1
    indices = np.clip(indices, 0, levels.size - 2)
    phase_steps = np.diff(levels)[indices]
    result[usable] = np.divide(
        phase_steps,
        gradient_magnitude[usable],
        out=np.full(phase_steps.shape, np.inf, dtype=float),
        where=gradient_magnitude[usable] > 1e-6,
    )
    return result
