"""Unit tests for the synthetic-document assembly used by `embed_report`.

The job itself is exercised end-to-end alongside the enrichment finalize
in `test_enrichment_jobs.py`; here we keep a pure-function check on the
document layout because the prompt-shape is load-bearing for retrieval.
"""

from __future__ import annotations

from api.workers.embed_report import (
    _assemble_document,  # pyright: ignore[reportPrivateUsage]
    _Snapshot,  # pyright: ignore[reportPrivateUsage]
)


def _snap(**overrides: object) -> _Snapshot:
    defaults: dict[str, object] = {
        "description_en": None,
        "caption": None,
        "building_name": None,
        "infra_type": None,
        "damage_class": None,
        "debris": None,
    }
    defaults.update(overrides)
    return _Snapshot(**defaults)  # type: ignore[arg-type]


def test_assemble_document_returns_empty_when_no_text() -> None:
    """Sanity check: no description, no caption → no embedding."""
    doc = _assemble_document(_snap(infra_type=["residential"], damage_class="partial"))
    assert doc == ""


def test_assemble_document_uses_description_when_present() -> None:
    doc = _assemble_document(
        _snap(
            description_en="Building cracked through the second floor.",
            infra_type=["residential"],
            damage_class="partial",
            debris=True,
        )
    )
    assert "Building cracked through the second floor." in doc
    assert "Infra: residential" in doc
    assert "Damage: partial" in doc
    assert "Debris: yes" in doc


def test_assemble_document_falls_back_to_caption_alone() -> None:
    doc = _assemble_document(
        _snap(caption="A collapsed roof with debris on the street.", damage_class="complete")
    )
    assert "collapsed roof" in doc
    assert "Damage: complete" in doc


def test_assemble_document_combines_description_and_caption() -> None:
    doc = _assemble_document(
        _snap(
            description_en="School wall is leaning.",
            caption="A leaning concrete wall next to a playground.",
            building_name="Al-Falah Primary",
            infra_type=["school"],
            damage_class="partial",
            debris=False,
        )
    )
    lines = doc.split("\n")
    assert lines[0] == "School wall is leaning."
    assert lines[1] == "A leaning concrete wall next to a playground."
    assert "Building: Al-Falah Primary" in lines
    assert "Debris: no" in lines
