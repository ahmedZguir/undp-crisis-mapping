"""Unit tests for the pure spatial/stats heart (`api.analysis.spatial`).

No DB: every test builds a tiny synthetic mesh and asserts the trust-critical
behaviours — EB shrinkage, the *local* Poisson residual (silent
amid active vs silent amid silence), the two-condition blind spot, and Gi*.
"""

from __future__ import annotations

import numpy as np

from api.analysis.spatial import (
    MeshGrid,
    blind_spot_mask,
    build_knn_weights,
    eb_smoothed_rate,
    getis_ord_gi_star,
    hot_cold_counts,
    local_poisson_residual,
    run_spatial_pipeline,
)


def _grid_coords(n: int) -> tuple[np.ndarray, np.ndarray]:
    """A simple n x n lattice of cell centroids."""
    xs, ys = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    return xs.ravel(), ys.ravel()


# --- EB smoothing ---------------------------------------------------------


def test_eb_shrinks_small_stock_toward_mean() -> None:
    # Two cells: a big one running at the global rate, a tiny one with one lucky
    # report (raw rate 1.0). The tiny cell must be pulled toward the mean.
    observed = np.array([10.0, 1.0], dtype=float)
    stock = np.array([100.0, 1.0], dtype=float)
    smoothed = eb_smoothed_rate(observed, stock)
    raw_tiny = 1.0
    global_mean = 11.0 / 101.0
    # smoothed tiny rate is between the global mean and its raw rate, closer to mean
    assert global_mean < smoothed[1] < raw_tiny
    assert abs(smoothed[1] - global_mean) < abs(raw_tiny - global_mean)


def test_eb_zero_stock_is_safe() -> None:
    smoothed = eb_smoothed_rate(np.array([0.0, 5.0]), np.array([0.0, 10.0]))
    assert np.all(np.isfinite(smoothed))


def test_eb_empty() -> None:
    assert eb_smoothed_rate(np.zeros(0), np.zeros(0)).shape == (0,)


# --- local Poisson residual: the key behaviour ----------------------------


def test_silent_amid_active_is_large_negative_residual() -> None:
    # 5x5 lattice. All cells have equal stock. One central cell is silent while
    # every neighbour is very active → it must score a strongly negative residual.
    lon, lat = _grid_coords(5)
    n = lon.size
    stock = np.full(n, 50.0)
    observed = np.full(n, 20.0)
    centre = 12  # middle of the 5x5
    observed[centre] = 0.0

    smoothed = eb_smoothed_rate(observed, stock)
    w = build_knn_weights(lon, lat, k=8)
    _, residual = local_poisson_residual(observed, stock, smoothed, w)

    assert residual[centre] < -2.0
    # an active cell amid active cells is near zero
    assert abs(residual[0]) < abs(residual[centre])


def test_silent_amid_silence_is_near_zero_residual() -> None:
    # Same lattice, but the whole region is silent: a silent cell among silent
    # neighbours is "outside the event", NOT under-reported → residual ~ 0.
    lon, lat = _grid_coords(5)
    n = lon.size
    stock = np.full(n, 50.0)
    observed = np.zeros(n)

    smoothed = eb_smoothed_rate(observed, stock)
    w = build_knn_weights(lon, lat, k=8)
    _, residual = local_poisson_residual(observed, stock, smoothed, w)

    assert np.all(np.abs(residual) < 1.0)


# --- two-condition blind spot ---------------------------------------------


def test_blind_spot_requires_both_conditions() -> None:
    # Region split: left half active+damaged, right half silent+undamaged.
    # A silent cell embedded in the active-damaged left should flag; a silent
    # cell in the silent right (silent-amid-silence) must NOT.
    lon, lat = _grid_coords(6)
    n = lon.size
    stock = np.full(n, 40.0)
    observed = np.zeros(n)
    damage = np.zeros(n)
    for i in range(n):
        col = lon[i]
        if col <= 2:  # left band: active + damaged
            observed[i] = 25.0
            damage[i] = 25.0
    # carve a silent hole in the active-damaged band
    hole = int(np.where((lon == 1) & (lat == 2))[0][0])
    observed[hole] = 0.0

    smoothed = eb_smoothed_rate(observed, stock)
    w = build_knn_weights(lon, lat, k=8)
    _, residual = local_poisson_residual(observed, stock, smoothed, w)
    smoothed_damage = eb_smoothed_rate(damage, stock)
    blind = blind_spot_mask(residual, smoothed_damage, w)

    assert blind[hole]  # under-reported AND embedded in damage
    # a silent cell deep in the silent-undamaged right half must not flag
    right_silent = int(np.where((lon == 5) & (lat == 5))[0][0])
    assert not blind[right_silent]


def test_blind_spot_all_silent_flags_nothing() -> None:
    lon, lat = _grid_coords(5)
    n = lon.size
    w = build_knn_weights(lon, lat, k=8)
    residual = np.full(n, -5.0)  # even if "under-reported" everywhere...
    damage = np.zeros(n)  # ...no damage context anywhere
    blind = blind_spot_mask(residual, damage, w)
    assert not blind.any()


# --- Getis-Ord Gi* --------------------------------------------------------


def test_gi_star_marks_hot_cluster() -> None:
    # A high-rate cluster in one corner should produce positive Gi* z-scores
    # there and negative ones in the opposite low corner.
    lon, lat = _grid_coords(6)
    n = lon.size
    rate = np.full(n, 0.01)
    for i in range(n):
        if lon[i] <= 1 and lat[i] <= 1:
            rate[i] = 1.0
    w = build_knn_weights(lon, lat, k=8)
    gi = getis_ord_gi_star(rate, w)
    hot_corner = int(np.where((lon == 0) & (lat == 0))[0][0])
    cold_corner = int(np.where((lon == 5) & (lat == 5))[0][0])
    assert gi[hot_corner] > 0
    assert gi[cold_corner] < gi[hot_corner]
    hot, _cold = hot_cold_counts(gi, threshold=1.0)
    assert hot >= 1


def test_gi_star_constant_input_is_all_zero() -> None:
    lon, lat = _grid_coords(4)
    w = build_knn_weights(lon, lat, k=4)
    gi = getis_ord_gi_star(np.full(lon.size, 0.3), w)
    assert np.allclose(gi, 0.0)


# --- full pipeline + degenerate meshes ------------------------------------


def test_pipeline_smoke_and_alignment() -> None:
    lon, lat = _grid_coords(5)
    n = lon.size
    mesh = MeshGrid(
        lon=lon,
        lat=lat,
        stock=np.full(n, 30.0),
        observed=np.linspace(0, 10, n),
        damage_weight=np.linspace(0, 5, n),
    )
    res = run_spatial_pipeline(mesh)
    assert res.residual_z.shape == (n,)
    assert res.gi_z.shape == (n,)
    assert res.blind_spot.shape == (n,)
    assert np.all(np.isfinite(res.residual_z))


def test_pipeline_single_cell_does_not_crash() -> None:
    mesh = MeshGrid(
        lon=np.array([0.0]),
        lat=np.array([0.0]),
        stock=np.array([10.0]),
        observed=np.array([3.0]),
        damage_weight=np.array([2.0]),
    )
    res = run_spatial_pipeline(mesh)
    assert res.residual_z.shape == (1,)
    assert not res.blind_spot.any()


def test_pipeline_empty_mesh() -> None:
    mesh = MeshGrid(
        lon=np.zeros(0),
        lat=np.zeros(0),
        stock=np.zeros(0),
        observed=np.zeros(0),
        damage_weight=np.zeros(0),
    )
    res = run_spatial_pipeline(mesh)
    assert res.residual_z.shape == (0,)
