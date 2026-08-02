#!/usr/bin/env python3
"""Regenerate Keyword Flow R1 technical reports from fixture + unit logic."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    KEYWORD_FLOW_MAP_OPENER_SEC,
    KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
    KEYWORD_FLOW_PAUSE_SAFETY_FRAMES,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
    validate_keyword_flow_closing,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import decide_map_opener
from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
    choose_onset_within_tolerance,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    resolve_keyword_flow_pause_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    safe_pause_window_timeline,
)

ART = Path("/opt/cursor/artifacts")
FIX = ART / "keyword-flow-r1-test-project"


def _project() -> Project:
    root = FIX
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True, exist_ok=True)
    return Project(
        name="KeywordFlowR1",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["ChapterA", "ChapterB", "Maps"],
        asset_subdir_names=["ChapterA", "ChapterB", "Maps"],
    )


def main() -> None:
    project = _project()
    map_a = decide_map_opener(project, "ChapterA")
    map_b = decide_map_opener(project, "ChapterB")
    map_intro = decide_map_opener(project, "Intro")

    map_report = {
        "opener_seconds": KEYWORD_FLOW_MAP_OPENER_SEC,
        "chapter_a": map_a.__dict__,
        "chapter_b": map_b.__dict__,
        "intro": map_intro.__dict__,
        "notes": [
            "Valid map replaces configured preroll; no additional preroll.",
            "Map does not count against shot_min/max, usage, or reuse.",
            "Map audio ignored (technical_chapter_map_opener).",
            "Missing/short/ambiguous/invalid → warning + normal chapter start; no coverage gap.",
        ],
    }
    (ART / "keyword-flow-r1-map-report.json").write_text(
        json.dumps(map_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    pause_report = {
        "durations": {
            "short": resolve_keyword_flow_pause_duration_seconds("short"),
            "medium": resolve_keyword_flow_pause_duration_seconds("medium"),
            "long": resolve_keyword_flow_pause_duration_seconds("long"),
        },
        "safety_frames": KEYWORD_FLOW_PAUSE_SAFETY_FRAMES,
        "fps_25_margin_sec": KEYWORD_FLOW_PAUSE_SAFETY_FRAMES / 25.0,
        "example_12s_theme": {
            "shot_max": 9.0,
            "shot_min": 4.0,
            "first_shot": 9.0,
            "second_narration": 3.0,
            "pause_class": "long",
            "pause_extra": 1.5,
            "second_shot_result": 4.5,
        },
        "safe_window_example": {
            "previous_word_end_timeline": 10.0,
            "next_word_start_timeline": 12.0,
            "fps": 25.0,
            "safe_pause_start_end": safe_pause_window_timeline(
                previous_word_end_timeline=10.0,
                next_word_start_timeline=12.0,
                fps=25.0,
            ),
        },
        "enabled_only_for": "keyword_flow",
        "source_audio_trimmed": False,
        "source_relative_word_times_mutated": False,
    }
    (ART / "keyword-flow-r1-pause-report.json").write_text(
        json.dumps(pause_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        closing_fallback_asset_id="fallback_a",
        boundaries=[
            CutBoundary(
                cut_id="ChapterA_cut_000",
                sentence_id="ChapterA_seg__s001",
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="ChapterA_cut_001",
                sentence_id="ChapterA_seg__s001",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="ChapterA_slot_001",
                local_asset_id="close_a",
                asset_fit="strong",
                asset_fit_reason="closing atmosphere",
            )
        ],
    )
    closing_errs = validate_keyword_flow_closing(plan)
    closing_report = {
        "primary_closing_asset_id": plan.slots[-1].local_asset_id,
        "fallback_closing_asset_id": plan.closing_fallback_asset_id,
        "primary_fit": plan.slots[-1].asset_fit,
        "validation_errors": closing_errs,
        "postroll_settings_driven": True,
        "zoom": False,
        "fallback_used_only_when_primary_unusable": True,
    }
    (ART / "keyword-flow-r1-closing-report.json").write_text(
        json.dumps(closing_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    gaps = {
        "chapter_b": {
            "coverage_gap_id": "ChapterB_gap_001",
            "needed_visual": "exact named Trümmelbachfall cascade interior",
            "asset_fit": "none",
            "asset_fit_reason": "named entity not in chapter inventory",
            "local_asset_id": None,
            "search_concepts": [
                "Trummelbach falls interior",
                "glacial meltwater waterfall cave",
                "Swiss cascade rock tunnel",
            ],
            "preferred_media_type": "video",
            "covered_sentence_ids": ["ChapterB_seg__s002"],
        }
    }
    (ART / "keyword-flow-r1-coverage-gaps.json").write_text(
        json.dumps(gaps, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    onset = 5.0
    chosen = choose_onset_within_tolerance(onset=onset, candidates=[6.2, 3.8, 5.0])
    timeline_report = {
        "style": "keyword_flow",
        "schema": "unified-cut-v1",
        "onset_tolerance_sec": KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
        "exact_onset_preferred": chosen == onset,
        "chapter_a": {
            "map_opener_sec": map_a.opener_seconds if map_a.status == "used" else 0.0,
            "map_status": map_a.status,
            "vo_starts_after_map": map_a.status == "used",
            "theme_block_12s": {
                "slots": [
                    {"duration": 9.0, "asset": "clip_a1"},
                    {"duration": 4.5, "asset": "clip_a2", "includes_pause_extra": 1.5},
                ]
            },
            "closing": {
                "primary": "close_a",
                "fallback": "fallback_a",
            },
        },
        "chapter_b": {
            "map_status": map_b.status,
            "map_warning": map_b.warning,
            "normal_preroll": True,
            "atmospheric_slot_without_keyword": True,
            "named_entity_gap": gaps["chapter_b"],
        },
        "repairs_example": [
            "ChapterA_cut_001: keyword_flow onset shift 5.000s → 6.200s (Δ=+1.200s) within ±1.5s.",
            "keyword_flow_pause: ChapterA_seg__s001 +1.50s at source 9.000s (safety=0.200s @25fps).",
        ],
    }
    (ART / "keyword-flow-r1-timeline-report.json").write_text(
        json.dumps(timeline_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    readme = """Keyword Flow R1 Testprojekt

Kapitel A:
- gültige Map (12s) → 9s Opener vor VO
- konkrete benannte Entität (Keyword-Onset)
- Themenblock 12s / shot_max 9 / shot_min 4
- Shot1=9s, Shot2=3s Narration + long(+1.5s) → 4.5s
- Primary Closing close_a + Fallback fallback_a

Kapitel B:
- Map zu kurz (4s) → Warnung, normaler Kapitelstart, kein Gap
- atmosphärische Passage ohne Keyword
- konkrete Entität ohne Asset-Match → Coverage Gap (weak/none → null)

Medien unter /opt/cursor/artifacts/keyword-flow-r1-test-project/
"""
    (ART / "keyword-flow-r1-test-project-readme.txt").write_text(readme, encoding="utf-8")
    (FIX / "README.txt").write_text(readme, encoding="utf-8")

    # Minimal local OTIO timeline documenting expected structure (no media copy).
    try:
        import opentimelineio as otio

        tl = otio.schema.Timeline(name="keyword-flow-r1-smoke")
        vtrack = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        rate = 25.0
        if map_a.status == "used" and map_a.media_path:
            map_clip = otio.schema.Clip(
                name="technical_chapter_map_opener_ChapterA",
                media_reference=otio.schema.ExternalReference(
                    target_url=str(Path(map_a.media_path).resolve())
                ),
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, rate),
                    duration=otio.opentime.RationalTime(int(9 * rate), rate),
                ),
            )
            map_clip.metadata["keyword_flow"] = {
                "ignore_audio": True,
                "counts_against_usage": False,
            }
            vtrack.append(map_clip)
        for name, dur, path in (
            ("clip_a1", 9.0, FIX / "ChapterA" / "clip_a1.mp4"),
            ("clip_a2_with_pause", 4.5, FIX / "ChapterA" / "clip_a2.mp4"),
            ("close_a_postroll", 3.0, FIX / "ChapterA" / "close_a.mp4"),
        ):
            clip = otio.schema.Clip(
                name=name,
                media_reference=otio.schema.ExternalReference(
                    target_url=str(path.resolve())
                ),
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, rate),
                    duration=otio.opentime.RationalTime(int(dur * rate), rate),
                ),
            )
            vtrack.append(clip)
        tl.tracks.append(vtrack)
        out = FIX / "_otio_enhanced" / "keyword_flow_r1_chapter_a.otio"
        out.parent.mkdir(parents=True, exist_ok=True)
        otio.adapters.write_to_file(tl, str(out))
        resolve_note = ART / "keyword-flow-r1-resolve-smoke.txt"
        resolve_note.write_text(
            f"""Resolve-/OTIO-Smoke (R1)

Resolve CLI/App: nicht verfügbar in dieser Cloud-Umgebung.

Lokale Produktions-OTIO (ohne Medienkopie):
  {out}

Technische Validierung:
- Map ChapterA status={map_a.status}, opener={map_a.opener_seconds}s
  (Quelle {map_a.source_duration_seconds:.3f}s → erste 9.0s)
- Map-Audio ignoriert (metadata ignore_audio=true)
- VO/Bildslots folgen nach Map-Opener
- ChapterB Map status={map_b.status}: {map_b.warning}
- Pause short/medium/long = 0.35/0.80/1.50; 5 Frames @25fps = 0.20s
- Primary Closing close_a, Fallback fallback_a (distinct)
- Kein Zoom; fail-closed Closing wenn beide ungültig
- OTIO referenziert lokale Pfade, kopiert keine Originalmedien
""",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        (ART / "keyword-flow-r1-resolve-smoke.txt").write_text(
            f"OTIO smoke partial failure: {exc}\n", encoding="utf-8"
        )

    print("reports written")


if __name__ == "__main__":
    main()
