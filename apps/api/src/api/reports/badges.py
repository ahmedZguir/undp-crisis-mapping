"""Badge slug to display name. Earned by the scoring worker and the coordinator verify endpoint."""

from __future__ import annotations

BADGE_CATALOG: dict[str, str] = {
    "first_report": "First Responder",
    "ground_truth": "Ground Truth",
    "area_mapper": "Area Mapper",
    "active_responder": "Active Responder",
    "community_anchor": "Community Anchor",
    "verified_by_coordinator": "Field Verified",
}

# Awarded (and revoked) by the coordinator verify endpoint, not by the
# scoring worker.
VERIFIED_BY_COORDINATOR = "verified_by_coordinator"


def badge_name(slug: str) -> str:
    """Display name for a slug, or the slug itself if unknown."""
    return BADGE_CATALOG.get(slug, slug)
