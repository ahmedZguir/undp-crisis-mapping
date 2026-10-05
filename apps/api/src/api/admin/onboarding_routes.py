"""Per-coordinator first-run walkthrough state.

api_service bypasses RLS, so every query is scoped by coordinator_id here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text

from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.app_state import AppState, get_app_state
from api.schemas.onboarding import OnboardingState, OnboardingUpdate

router = APIRouter(prefix="/onboarding")


_GET_SQL = text(
    """
    select walkthrough_completed_at, dashboard_explored_at
      from public.coordinator_onboarding
     where coordinator_id = cast(:coordinator_id as uuid)
    """
)

# Set-once: a milestone keeps its first timestamp, and a false flag never
# clears it, so replaying the walkthrough does not undo completion.
_UPSERT_SQL = text(
    """
    insert into public.coordinator_onboarding
        (coordinator_id, walkthrough_completed_at, dashboard_explored_at, updated_at)
    values (
        cast(:coordinator_id as uuid),
        case when :set_walkthrough then now() else null end,
        case when :set_dashboard then now() else null end,
        now()
    )
    on conflict (coordinator_id) do update set
        walkthrough_completed_at = case when :set_walkthrough
            then coalesce(public.coordinator_onboarding.walkthrough_completed_at, now())
            else public.coordinator_onboarding.walkthrough_completed_at end,
        dashboard_explored_at = case when :set_dashboard
            then coalesce(public.coordinator_onboarding.dashboard_explored_at, now())
            else public.coordinator_onboarding.dashboard_explored_at end,
        updated_at = now()
    returning walkthrough_completed_at, dashboard_explored_at
    """
)


def _row_to_state(row: Any) -> OnboardingState:
    return OnboardingState(
        walkthrough_completed=row.walkthrough_completed_at is not None,
        dashboard_explored=row.dashboard_explored_at is not None,
    )


@router.get("", response_model=OnboardingState)
async def get_onboarding_state(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
) -> OnboardingState:
    async with state.sessionmaker() as session:
        row = (await session.execute(_GET_SQL, {"coordinator_id": str(coordinator.id)})).first()
    if row is None:
        return OnboardingState(walkthrough_completed=False, dashboard_explored=False)
    return _row_to_state(row)


@router.patch("", response_model=OnboardingState)
async def update_onboarding_state(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    payload: OnboardingUpdate,
) -> OnboardingState:
    async with state.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                _UPSERT_SQL,
                {
                    "coordinator_id": str(coordinator.id),
                    "set_walkthrough": payload.walkthrough_completed is True,
                    "set_dashboard": payload.dashboard_explored is True,
                },
            )
        ).first()
    assert row is not None  # upsert always returns the row
    return _row_to_state(row)
