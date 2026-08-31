"""Menschlesbare Timing-Fehler-Zusammenfassung."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.timing_error_summary import (
    classify_timing_errors,
    format_grouped_timing_errors,
    format_timing_error_overview,
    match_named_chapters,
    missing_clip_chapter_names,
    split_timing_error_blob,
    timing_failure_headline,
)

_SCREENSHOT_BLOB = """
6/10 Python-Timing(s) fehlgeschlagen (4 ok):
- Kropa: slot[8]: span 5.76s > usable 5.46s (shortfall 0.30s innerhalb Toleranz 2.0s) nach Grenzen-Klemme nicht stabil. slot[9]: span 7.20s > usable 7.06s (shortfall 0.14s innerhalb Toleranz 2.0s) nach Grenzen-Klemme nicht stabil.
- Škofja Loka: Python Timing für „Škofja Loka“ nicht exportfähig: 1 Placeholder/Shortfall — das Video ist kürzer als die Sprecherzeit. Unter „Zu kurze Clips ansehen“ liegt das vorgesehene Video. Im Funnel längeres Material holen, dann Timing erneut. Betroffen: Škofja_Loka_slot_011: asset_skofja_loka_asset00001 · Škofja Loka_Asset00001.mov — nutzbar 11.2s, Slot braucht 11.3s
- Savica-Wasserfall: Python Timing für „Savica-Wasserfall“ nicht exportfähig: 1 Placeholder/Shortfall — das Video ist kürzer als die Sprecherzeit. Betroffen: Savica-Wasserfall_slot_012: asset_x · Savica-Wasserfall_Asset00002.mov — nutzbar 5.7s, Slot braucht 8.1s
- Piran: Python Timing für „Piran“ nicht exportfähig: 2 Placeholder/Shortfall — das Video ist kürzer als die Sprecherzeit. Betroffen: Piran_slot_004: asset_p · clip.mp4 — nutzbar 9.6s, Slot braucht 12.0s, Piran_slot_010: kein Asset — es fehlen 7.8s
- Triglav-Nationalpark: betroffen: Triglav-Nationalpark_slot_015: kein Asset — es fehlen 3.3s
- Ptuj: betroffen: Ptuj_slot_010: kein Asset — es fehlen 3.0s
"""


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
    blob = "; ".join(messages)
    assert len(split_timing_error_blob(blob)) >= 3

    groups = classify_timing_errors(messages)
    by_cat = {g.category: g for g in groups}
    assert "short_asset" in by_cat
    assert "visual_gap" in by_cat
    assert "chapter_bridge" in by_cat
    short = by_cat["short_asset"].items[0]
    assert "Yosemite" in short or "Slot 7" in short
    assert "6.7" in short or "6.67" in short
    assert "8s" in short or "8.0" in short or "7.96" in short
    assert "Yosemite_Asset02.mp4" in short
    assert "/Users/" not in short
    gap = by_cat["visual_gap"].items[0]
    assert "Yosemite" in gap
    assert "ohne Bild" in gap
    bridge = by_cat["chapter_bridge"].items[0]
    assert "Yosemite" in bridge and "Caddo Lake" in bridge
    overview = format_timing_error_overview(messages)
    assert "Clip zu kurz" in overview or "Asset zu kurz" in overview
    assert "Bildlücke" in overview or "Visuelle Lücke" in overview


def test_classify_empty() -> None:
    assert classify_timing_errors("") == []
    assert format_timing_error_overview([]) == ""
    assert format_grouped_timing_errors("") == ""
    assert missing_clip_chapter_names("") == []
    assert match_named_chapters(["Piran"], []) == []


def test_screenshot_blob_groups_by_kind() -> None:
    headline = timing_failure_headline(_SCREENSHOT_BLOB)
    assert headline.startswith("6 von 10")
    assert "4 ok" in headline

    groups = classify_timing_errors(_SCREENSHOT_BLOB)
    by_cat = {g.category: g for g in groups}

    assert "clamp_unstable" in by_cat
    clamp_text = " ".join(by_cat["clamp_unstable"].items)
    assert "Kropa" in clamp_text
    assert "Slot 8" in clamp_text
    assert "Slot 9" in clamp_text

    assert "short_mini" in by_cat
    mini = by_cat["short_mini"].items[0]
    assert "Škofja Loka" in mini
    assert "11.2s" in mini
    assert "11.3s" in mini

    assert "short_asset" in by_cat
    shorts = " ".join(by_cat["short_asset"].items)
    assert "Savica-Wasserfall" in shorts
    assert "Piran" in shorts
    assert "5.7s" in shorts
    assert "8.1s" in shorts

    assert "missing_asset" in by_cat
    missing = " ".join(by_cat["missing_asset"].items)
    assert "Triglav-Nationalpark" in missing
    assert "Ptuj" in missing
    assert "Piran" in missing
    assert "kein Video/Foto" in missing
    assert by_cat["missing_asset"].chapters == [
        "Piran",
        "Triglav-Nationalpark",
        "Ptuj",
    ]
    assert missing_clip_chapter_names(_SCREENSHOT_BLOB) == [
        "Piran",
        "Triglav-Nationalpark",
        "Ptuj",
    ]
    assert match_named_chapters(
        ["piran", "Ptuj", "fehlt"],
        ["Piran", "Ptuj", "Kropa"],
    ) == ["Piran", "Ptuj"]
    assert "other" not in by_cat

    grouped = format_grouped_timing_errors(_SCREENSHOT_BLOB)
    assert "Was los ist:" in grouped
    assert "Was du tun kannst:" in grouped
    assert "Funnel" in grouped


def test_unknown_asset_id_is_own_group() -> None:
    groups = classify_timing_errors(
        "2/2 Python-Timing(s) fehlgeschlagen (0 ok):\n"
        "- Vogel: Unbekannte Asset-ID: openverse_abc\n"
        "- Soča: Unbekannte Asset-ID: wikimedia_1"
    )
    by_cat = {g.category: g for g in groups}
    assert list(by_cat) == ["unknown_id"]
    assert len(by_cat["unknown_id"].items) == 2
    assert "Vogel" in by_cat["unknown_id"].items[0]
    assert "openverse_abc" in by_cat["unknown_id"].items[0]


def test_fmt_sec_keeps_tiny_shortfalls_visible() -> None:
    from otio_app.services.without_voiceover_enhanced.timing_error_summary import (
        _fmt_sec,
        classify_timing_errors,
    )

    assert _fmt_sec(0) == "0s"
    assert _fmt_sec(0.03) == "0.03s"
    assert _fmt_sec(0.3) == "0.3s"
    assert _fmt_sec(8.0) == "8s"
    assert _fmt_sec(8.1) == "8.1s"

    groups = classify_timing_errors(
        "Kropa: slot[7]: span 8.34s > usable 8.31s "
        "(shortfall 0.03s innerhalb Toleranz 2.0s) "
        "nach Grenzen-Klemme — Rest als roter Placeholder."
    )
    by_cat = {g.category: g for g in groups}
    assert "clamp_unstable" in by_cat
    assert "0.03s" in " ".join(by_cat["clamp_unstable"].items)

    placeholder_note = classify_timing_errors(
        "Python Timing für „Savica-Wasserfall“: 1 roter Platzhalter "
        "in der Timeline (OTIO exportiert sie markiert). "
        "Betroffen: Savica-Wasserfall_slot_012: asset_x · clip.mov "
        "— nutzbar 5.7s, Slot braucht 8.1s."
    )
    by_note = {g.category: g for g in placeholder_note}
    assert "short_asset" in by_note
    assert "Savica-Wasserfall" in " ".join(by_note["short_asset"].items)
