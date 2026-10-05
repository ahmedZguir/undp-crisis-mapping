"""Unit tests for `filter_signature` — the cache key for summary / chat /
saved-views surfaces.

The signature must be stable when the request is unchanged and must
respond to every filter that can change the result set, including the
new `building_id` field added in this pass.
"""

from __future__ import annotations

import uuid

from api.admin.search_filters import filter_signature
from api.schemas.admin_search import SearchRequest


def test_signature_stable_across_calls() -> None:
    crisis_id = uuid.uuid4()
    request = SearchRequest(query="cracked wall")
    assert filter_signature(crisis_id, request) == filter_signature(crisis_id, request)


def test_signature_changes_when_building_filter_changes() -> None:
    crisis_id = uuid.uuid4()
    bid = uuid.uuid4()
    a = SearchRequest()
    b = SearchRequest(building_id=bid)
    assert filter_signature(crisis_id, a) != filter_signature(crisis_id, b)


def test_signature_changes_when_query_changes() -> None:
    crisis_id = uuid.uuid4()
    a = SearchRequest(query="cracked wall")
    b = SearchRequest(query="flooded basement")
    assert filter_signature(crisis_id, a) != filter_signature(crisis_id, b)
