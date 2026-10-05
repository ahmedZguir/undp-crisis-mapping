"""Pure numpy spatial statistics for the analysis report: EB smoothing, kNN
weights, local Poisson residuals, Getis-Ord Gi* and blind spots. No I/O."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from esda.getisord import G_Local as _G_Local_typed  # pyright: ignore[reportMissingTypeStubs]
from libpysal import weights as _pysal_weights_typed  # pyright: ignore[reportMissingTypeStubs]

from api.analysis.constants import (
    BLIND_SPOT_DAMAGE_CONTEXT_PCTL,
    BLIND_SPOT_RESIDUAL_Z,
    HOTSPOT_Z_THRESHOLD,
    SPATIAL_NEIGHBOURS_K,
)

# libpysal and esda ship without stubs; route them through `Any` aliases.
_pysal_weights: Any = _pysal_weights_typed
_G_Local: Any = _G_Local_typed
Weights = Any

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

# Floor for the Poisson-residual denominator so a near-zero expectation can't blow up z.
_EPS = 1e-9


@dataclass(frozen=True)
class MeshGrid:
    """Per-cell parallel arrays: centroids, building stock, report counts and damage weight."""

    lon: FloatArray
    lat: FloatArray
    stock: FloatArray
    observed: FloatArray
    damage_weight: FloatArray

    def __len__(self) -> int:
        return int(self.lon.shape[0])


@dataclass(frozen=True)
class SpatialResult:
    """Per-cell outputs of the spatial pipeline, aligned to the input mesh."""

    smoothed_rate: FloatArray  # EB-smoothed reporting rate r_i
    lag_rate: FloatArray  # spatial lag of the EB rate (local expected rate)
    expected: FloatArray  # lambda_i = stock_i * lag_rate_i
    residual_z: FloatArray  # local Poisson residual
    gi_z: FloatArray  # Getis-Ord Gi* analytical z-score
    blind_spot: BoolArray


def eb_smoothed_rate(observed: FloatArray, stock: FloatArray) -> FloatArray:
    """Global Empirical-Bayes (Marshall) shrinkage of per-cell rates.

    Raw rate is `r_i = observed_i / stock_i`. The method-of-moments global EB
    estimator shrinks each `r_i` toward the global rate `m = Σobserved / Σstock`
    by a weight `stock_i / (stock_i + m / b)`, where `b` is the method-of-moments
    estimate of the prior variance:

        b = (Σ stock_i (r_i - m)^2 / Σ stock_i) - m / mean(stock)

    `b` is clamped at 0, which shrinks everything to `m`. Zero-stock cells smooth to `m`.
    """
    n: int = observed.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    total_stock = float(stock.sum())
    if total_stock <= 0.0:
        return np.zeros(n, dtype=np.float64)

    m: float = float(observed.sum()) / total_stock  # global mean rate

    safe_stock = np.where(stock > 0.0, stock, np.nan)
    raw = observed / safe_stock  # nan where stock == 0

    mean_stock = total_stock / n
    # weighted variance of raw rates around m, ignoring zero-stock cells
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        weighted_sq = np.nansum(stock * (raw - m) ** 2)
    b = weighted_sq / total_stock - m / mean_stock
    b = max(b, 0.0)

    if b <= 0.0:
        # No between-cell variance, so everything shrinks to the mean.
        return np.full(n, m, dtype=np.float64)

    # zero-stock cells get weight 0, i.e. exactly m
    weight = np.where(stock > 0.0, stock / (stock + m / b), 0.0)
    raw_filled = np.where(stock > 0.0, observed / np.where(stock > 0.0, stock, 1.0), 0.0)
    return weight * raw_filled + (1.0 - weight) * m


def build_knn_weights(lon: FloatArray, lat: FloatArray, k: int = SPATIAL_NEIGHBOURS_K) -> Weights:
    """Row-standardised kNN weights over cell centroids; `k` is clamped to `n-1`."""
    coords = np.column_stack([lon, lat])
    n: int = coords.shape[0]
    k_eff = max(1, min(k, n - 1))
    w = _pysal_weights.KNN.from_array(coords, k=k_eff)
    w.transform = "r"  # row-standardise
    return w


def spatial_lag(weights: Weights, values: FloatArray) -> FloatArray:
    """Each cell's row-standardised weighted mean of its neighbours."""
    return np.asarray(_pysal_weights.lag_spatial(weights, values), dtype=np.float64)


def local_poisson_residual(
    observed: FloatArray,
    stock: FloatArray,
    smoothed_rate: FloatArray,
    weights: Weights,
) -> tuple[FloatArray, FloatArray]:
    """Poisson residual against the neighbourhood's EB rate, not the global mean.

        lambda_i  = stock_i * lag_rate_i
        residual  = (observed_i - lambda_i) / sqrt(max(lambda_i, eps))

    A silent cell among active neighbours scores strongly negative; a silent cell
    among silent neighbours scores about 0. Returns `(expected_lambda, residual_z)`.
    """
    lag_rate = spatial_lag(weights, smoothed_rate)
    lam = stock * lag_rate
    residual = (observed - lam) / np.sqrt(np.maximum(lam, _EPS))
    return lam, residual


def getis_ord_gi_star(smoothed_rate: FloatArray, weights: Weights) -> FloatArray:
    """Getis-Ord Gi* analytical z-scores on the EB-smoothed rate.

    Uses the analytical `Zs`, which are deterministic. Constant input yields zeros.
    """
    if float(np.ptp(smoothed_rate)) == 0.0:
        return np.zeros_like(smoothed_rate)

    with warnings.catch_warnings():
        # `star=True` on a row-standardised W emits a benign diagonal warning.
        warnings.simplefilter("ignore")
        g = _G_Local(smoothed_rate, weights, star=True)
    return np.asarray(g.Zs, dtype=np.float64)


def blind_spot_mask(
    residual_z: FloatArray,
    smoothed_damage_rate: FloatArray,
    weights: Weights,
    *,
    residual_threshold: float = BLIND_SPOT_RESIDUAL_Z,
    damage_context_pctl: float = BLIND_SPOT_DAMAGE_CONTEXT_PCTL,
) -> BoolArray:
    """Flag cells that are under-reported and sit in a damaged neighbourhood.

    Under-reported means `residual_z < residual_threshold`; damaged neighbourhood
    means the lagged damage rate is above its `damage_context_pctl` percentile.
    """
    n: int = residual_z.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.bool_)

    under_reported = residual_z < residual_threshold
    damage_context = spatial_lag(weights, smoothed_damage_rate)
    # If no damage anywhere, the percentile is 0 and nothing is "above" it in a
    # meaningful sense; guard so an all-zero context never flags.
    if float(damage_context.max()) <= 0.0:
        return np.zeros(n, dtype=np.bool_)
    cutoff = float(np.percentile(damage_context, damage_context_pctl))
    embedded_in_damage = damage_context > cutoff
    return under_reported & embedded_in_damage


def run_spatial_pipeline(mesh: MeshGrid, *, k: int = SPATIAL_NEIGHBOURS_K) -> SpatialResult:
    """Run smoothing, residuals, Gi* and blind spots over a mesh.

    Degenerate meshes (<2 cells or all-zero stock) return zeros.
    """
    n = len(mesh)
    if n == 0:
        empty = np.zeros(0, dtype=np.float64)
        return SpatialResult(empty, empty, empty, empty, empty, np.zeros(0, dtype=np.bool_))

    smoothed = eb_smoothed_rate(mesh.observed, mesh.stock)

    if n < 2:
        z = np.zeros(n, dtype=np.float64)
        return SpatialResult(
            smoothed, z.copy(), z.copy(), z.copy(), z.copy(), np.zeros(n, np.bool_)
        )

    weights = build_knn_weights(mesh.lon, mesh.lat, k=k)

    lam, residual_z = local_poisson_residual(mesh.observed, mesh.stock, smoothed, weights)
    gi_z = getis_ord_gi_star(smoothed, weights)

    # Smooth damage against the same stock so it is comparable to the report rate.
    smoothed_damage = eb_smoothed_rate(mesh.damage_weight, mesh.stock)
    blind = blind_spot_mask(residual_z, smoothed_damage, weights)

    lag_rate = spatial_lag(weights, smoothed)
    return SpatialResult(
        smoothed_rate=smoothed,
        lag_rate=lag_rate,
        expected=lam,
        residual_z=residual_z,
        gi_z=gi_z,
        blind_spot=blind,
    )


def hot_cold_counts(gi_z: FloatArray, *, threshold: float = HOTSPOT_Z_THRESHOLD) -> tuple[int, int]:
    """Count significant hot (`Zs > t`) and cold (`Zs < -t`) cells."""
    if gi_z.shape[0] == 0:
        return 0, 0
    hot = int(np.count_nonzero(gi_z > threshold))
    cold = int(np.count_nonzero(gi_z < -threshold))
    return hot, cold


__all__ = [
    "MeshGrid",
    "SpatialResult",
    "blind_spot_mask",
    "build_knn_weights",
    "eb_smoothed_rate",
    "getis_ord_gi_star",
    "hot_cold_counts",
    "local_poisson_residual",
    "run_spatial_pipeline",
    "spatial_lag",
]
