"""Coordinator-only routes, combined under one `/admin` router.

The `require_coordinator` gate and the `/admin` prefix live on the combined
router, so sub-routers declare neither. tests/admin/test_admin_routes_under_prefix.py
checks that no route ends up under `/admin/admin/`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.admin.area_routes import router as _areas_router
from api.admin.building_routes import router as _buildings_router
from api.admin.coordinator_routes import router as _coordinators_router
from api.admin.crisis_routes import router as _crises_router
from api.admin.form_routes import router as _forms_router
from api.admin.job_routes import router as _jobs_router
from api.admin.map_routes import router as _map_router
from api.admin.name_search_routes import router as _divisions_router
from api.admin.onboarding_routes import router as _onboarding_router
from api.admin.photo_export_routes import router as _photo_export_router
from api.admin.report_generation_routes import router as _report_generation_router
from api.admin.report_routes import router as _reports_router
from api.admin.saved_view_routes import router as _saved_views_router
from api.admin.search_routes import router as _search_router
from api.auth.dependency import require_coordinator
from api.heatmap.routes import admin_router as _heat_router

# Sub-routers inherit this gate. Per-route Depends(require_coordinator) is only
# for handlers that need the principal; FastAPI caches it per request.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_coordinator)])
router.include_router(_areas_router)
router.include_router(_buildings_router)
router.include_router(_coordinators_router)
router.include_router(_crises_router)
router.include_router(_divisions_router)
router.include_router(_forms_router)
router.include_router(_heat_router)
router.include_router(_jobs_router)
router.include_router(_map_router)
router.include_router(_onboarding_router)
router.include_router(_photo_export_router)
router.include_router(_report_generation_router)
router.include_router(_reports_router)
router.include_router(_saved_views_router)
router.include_router(_search_router)


__all__ = ["router"]
