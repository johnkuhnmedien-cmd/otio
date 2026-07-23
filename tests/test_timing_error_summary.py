"""Menschlesbare Timing-Fehler-Zusammenfassung."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.timing_error_summary import (
    classify_timing_errors,
    format_timing_error_overview,
    split_timing_error_blob,
)


def test_split_and_classify_short_and_gap_errors() -> None:
    messages = [
        (
            "Yosemite_slot_007: Asset asset__yosemite__yosemite_asset02__cdb5cccb zu kurz "
            "(nutzbar 6.67s < nötig 7.96s; Toleranz 1.0s). Kein Video-Hold: kürzeren Shot "
            "planen. Pfad /Users/x/Yosemite/Yosemite_Asset02.mp4."
        ),
        (
            "bridge_001: Start-/Endanker in unterschiedlichen Kapiteln "
            "(Yosemite vs Caddo Lake)."
        ),
        (
            "Abschließende visuelle Lücke während der Narration in Kapitel Yosemite: "
            "letzter Shot endet 134.800s, Audio bis 139.880s."
        ),
    ]
    # Auch als Alt-Blob mit "; " (inkl. Semikolon in der Kurz-Meldung).
    blob = "; ".join(messages)
    assert len(split_timing_error_blob(blob)) >= 3

    groups = classify_timing_errors(messages)
    by_cat = {g.category: g for g in groups}
    assert "short_asset" in by_cat
    assert "visual_gap" in by_cat
    assert "chapter_bridge" in by_cat
    short = by_cat["short_asset"].items[0]
    assert "Yosemite_slot_007" in short
    assert "6.67" in short and "7.96" in short
    assert "Yosemite_Asset02.mp4" in short
    assert "/Users/" not in short
    overview = format_timing_error_overview(messages)
    assert "Asset zu kurz" in overview
    assert "Visuelle Lücke" in overview


def test_classify_empty() -> None:
    assert classify_timing_errors("") == []
    assert format_timing_error_overview([]) == ""
