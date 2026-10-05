"""Regression: every admin route must live under `/admin/`.

The `/admin` prefix is hoisted to the combined admin router in
`api.admin.__init__`; sub-routers (`crisis_routes`, `report_routes`,
`coordinators`, `areas`, `jobs`, and the admin heatmap router) carry
only a sub-resource prefix (or none). A future contributor who adds a
sub-router and forgets to drop a leading `/admin` would mount routes
under `/admin/admin/...`. A contributor who adds a route to the wrong
*combined* router (e.g. attaches a handler to `_areas_router` instead of
`router`) would mount the route without the auth gate.

This test walks the FastAPI app's route table and asserts:

1. Every path defined by a route under `api.admin` is under `/admin/`.
2. None of those paths is under `/admin/admin/` (catches a missed
   `prefix` trim in a sub-router).
3. The admin heat-tile route is under `/admin/` (the heatmap module
   defines both a public and an admin router; only the admin one is
   mounted here).

The check is structural — it filters by inspecting the route handler's
module — so the assertion fires when any admin route mounts at the
wrong path, regardless of which file declared it.
"""

from __future__ import annotations

from starlette.routing import Route

from api.main import app


def _admin_route_paths() -> list[str]:
    """All route paths whose handler lives in `api.admin.*` or is the
    admin heat-tile handler from `api.heatmap.routes`."""
    paths: list[str] = []
    for route in app.routes:
        if not isinstance(route, Route):
            continue
        endpoint = route.endpoint
        module = getattr(endpoint, "__module__", "") or ""
        name = getattr(endpoint, "__name__", "") or ""
        is_admin_module = module.startswith("api.admin.")
        is_admin_heat = module == "api.heatmap.routes" and name == "get_admin_heat_tile"
        if is_admin_module or is_admin_heat:
            paths.append(route.path)
    return paths


def test_every_admin_route_is_under_admin_prefix() -> None:
    paths = _admin_route_paths()
    # Sanity: the suite should have found a non-trivial number of admin
    # routes. If this drops to zero the structural filter has rotted.
    assert len(paths) >= 10, f"expected many admin routes, found {len(paths)}: {paths}"

    misrouted = [p for p in paths if not p.startswith("/admin/")]
    assert not misrouted, (
        "These admin routes are not mounted under `/admin/`. A sub-router "
        "is probably attached to the wrong combined router, or `prefix='/admin'` "
        f"was not hoisted correctly: {misrouted}"
    )


def test_no_admin_route_is_under_double_admin_prefix() -> None:
    """`/admin/admin/...` means a sub-router still carries its own `/admin`
    prefix on top of the combined router's hoisted prefix.
    """
    paths = _admin_route_paths()
    doubled = [p for p in paths if p.startswith("/admin/admin/")]
    assert not doubled, (
        "These admin routes are mounted under `/admin/admin/...`. A sub-router "
        f"is still declaring its own `/admin` prefix: {doubled}"
    )


def test_admin_heat_tile_route_is_under_admin_prefix() -> None:
    """The heatmap module defines two routers (public + admin) with the same
    path shape. Make sure the admin one ends up under `/admin/`.
    """
    paths = _admin_route_paths()
    heat_paths = [p for p in paths if "/tiles/heat/" in p]
    assert heat_paths, "expected at least one admin heat-tile route"
    for path in heat_paths:
        assert path.startswith("/admin/"), f"admin heat-tile route not under /admin/: {path}"
