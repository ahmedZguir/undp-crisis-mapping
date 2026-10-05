from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.admin import router as admin_router
from api.auth.routes import router as auth_router
from api.channels.ivr import router as ivr_router
from api.channels.sms import router as sms_router
from api.channels.whatsapp import router as whatsapp_router
from api.core.app_state import lifespan
from api.core.config import get_settings
from api.core.health import router as health_router
from api.core.log_filters import HealthEndpointAccessFilter
from api.core.rate_limit import limiter, rate_limited_response
from api.crises.public import router as crises_public_router
from api.crises.routes import router as crises_router
from api.heatmap.routes import router as heatmap_router
from api.reports.ai_routes import router as ai_router
from api.reports.routes import router as reports_router


def _configure_app_logging() -> None:
    """Uvicorn only configures its own loggers, so api.* INFO logs would be dropped."""
    api_logger = logging.getLogger("api")
    if not any(getattr(h, "_api_stream", False) for h in api_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._api_stream = True  # pyright: ignore[reportAttributeAccessIssue]
        api_logger.addHandler(handler)
    api_logger.setLevel(logging.INFO)
    api_logger.propagate = False

    # Hide health probes from the access log. dictConfig keeps logger filters,
    # so this survives uvicorn's logging setup.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthEndpointAccessFilter) for f in access_logger.filters):
        access_logger.addFilter(HealthEndpointAccessFilter())


_configure_app_logging()

app = FastAPI(title="RASID API", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limited_response)
app.add_middleware(SlowAPIMiddleware)

app.include_router(reports_router)
app.include_router(crises_router)
app.include_router(crises_public_router)
app.include_router(heatmap_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(health_router)
app.include_router(whatsapp_router)
app.include_router(sms_router)
app.include_router(ivr_router)
app.include_router(ai_router)
