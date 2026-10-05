"""Per-coordinator first-run milestones for /admin/onboarding.

Milestones only move forward: sending false or omitting one never clears it.
"""

from __future__ import annotations

from pydantic import BaseModel


class OnboardingState(BaseModel):
    walkthrough_completed: bool
    dashboard_explored: bool


class OnboardingUpdate(BaseModel):
    walkthrough_completed: bool | None = None
    dashboard_explored: bool | None = None
