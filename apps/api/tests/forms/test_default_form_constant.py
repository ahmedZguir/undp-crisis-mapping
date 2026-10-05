"""Pins the canonical default form schema:

- structural invariants (page count, locked positions,
  enabled split);
- byte-for-byte equality between the Python constant and the JSON embedded
  in the migration column default, so the two cannot silently drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from api.crises.default_form import DEFAULT_FORM_SCHEMA, DEFAULT_FORM_VERSION


def test_default_form_version_is_one() -> None:
    assert DEFAULT_FORM_VERSION == 1


def test_default_schema_has_nine_pages_with_expected_enabled_split() -> None:
    pages = DEFAULT_FORM_SCHEMA["pages"]
    assert len(pages) == 9
    assert sum(1 for p in pages if p["enabled"]) == 6
    assert sum(1 for p in pages if not p["enabled"]) == 3


def test_default_schema_locks_photo_at_zero_and_location_at_one() -> None:
    pages = DEFAULT_FORM_SCHEMA["pages"]
    assert pages[0]["kind"] == "photo_and_damage"
    assert pages[0]["locked"] is True
    assert pages[1]["kind"] == "location"
    assert pages[1]["locked"] is True


def test_default_schema_no_other_pages_are_locked() -> None:
    pages = DEFAULT_FORM_SCHEMA["pages"]
    for page in pages[2:]:
        assert page["locked"] is False, f"page {page['kind']} unexpectedly locked"


def test_default_schema_check_in_modules_ship_disabled() -> None:
    by_kind = {p["kind"]: p for p in DEFAULT_FORM_SCHEMA["pages"]}
    for kind in ("electricity", "health_services", "pressing_needs"):
        assert by_kind[kind]["enabled"] is False


# --- migration <-> constant equality ---------------------------------------


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "supabase"
    / "migrations"
    / "20260521120000_per_crisis_form_schema.sql"
)


def _extract_form_schema_default_from_migration() -> dict:
    sql = _MIGRATION_PATH.read_text()
    # The migration embeds the default as `'{ ... }'::jsonb` on the
    # `form_schema` column. Pull the JSON literal between the first
    # single-quote after `form_schema  jsonb not null default` and the
    # closing `'::jsonb`.
    m = re.search(
        r"form_schema\s+jsonb\s+not\s+null\s+default\s+'(?P<json>.*?)'::jsonb",
        sql,
        re.DOTALL,
    )
    assert m, "could not locate form_schema default in migration"
    return json.loads(m.group("json"))


def test_default_schema_matches_migration_column_default() -> None:
    migration_default = _extract_form_schema_default_from_migration()
    assert migration_default == DEFAULT_FORM_SCHEMA
