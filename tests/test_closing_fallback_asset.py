"""Closing-Fallback: Reserve-Closer bei abschließender Narrations-Lücke."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    format_shot_constraints_for_prompt,
)
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedShot,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_keyword_sync_unified_cut_prompt,
    build_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    AssetCatalog,
    _apply_chapter_envelopes,
    _canonical_plan_shot_id,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    parse_unified_cut_response,
)


class _Locked:
    def __init__(self, segments: list) -> None:
        self.segments = segments


class _Seg:
    def __init__(self, segment_id: str, folder_name: str, sequence_index: int) -> None:
        self.segment_id = segment_id
        self.folder_name = folder_name
        self.sequence_index = sequence_index


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Ireland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="closing-fallback",
        name="closing-fallback",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Glendalough"],
        selected_asset_subdirs=["Glendalough"],
        fps=25.0,
        width=1920,
        height=1080,
    )


def test_canonical_plan_shot_id_strips_closing_fallback() -> None:
    assert (
        _canonical_plan_shot_id("Glendalough_slot_011__closing_fallback")
        == "Glendalough_slot_011"
    )


def test_parse_closing_fallback_asset_id() -> None:
    payload = {
        "closing_fallback_asset_id": "asset_glendalough_still_01",
        "boundaries": [
            {
                "cut_id": "cut_000",
                "sentence_id": "seg__s001",
                "position": "start",
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_001",
                "sentence_id": "seg__s001",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "slot_001",
                "local_asset_id": "asset_glendalough_motion_01",
                "asset_fit": "strong",
                "asset_fit_reason": "match",
                "narrative_function": "chapter_close",
            }
        ],
    }
    plan = parse_unified_cut_response(payload, "script-v1")
    assert plan.closing_fallback_asset_id == "asset_glendalough_still_01"


def test_prompts_require_closing_fallback_asset_id() -> None:
    options = CutPlanOptions()
    constraints = format_shot_constraints_for_prompt(options)
    assert "closing_fallback_asset_id" in constraints

    rhythm = build_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="[]",
        local_assets_json="[]",
        style_profile_text="",
        dramaturgy_text="",
        folder_name="Glendalough",
        folder_slug="Glendalough",
        shot_constraints_text=constraints,
    )
    assert "closing_fallback_asset_id" in rhythm
    keyword = build_keyword_sync_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="[]",
        local_assets_json="[]",
        style_profile_text="",
        dramaturgy_text="",
        folder_name="Glendalough",
        folder_slug="Glendalough",
        shot_constraints_text=constraints,
    )
    assert "closing_fallback_asset_id" in keyword


def test_closing_fallback_fills_trailing_narration_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letzter Shot endet 2s vor Audio → Fallback-Shot bis Audio-Ende."""
    project = _project(tmp_path)
    folder = "Glendalough"
    (Path(project.project_root) / folder).mkdir(parents=True)
    still = Path(project.project_root) / folder / "fallback.jpg"
    Image.new("RGB", (64, 48), color=(40, 120, 80)).save(still, format="JPEG")

    vo_end = 40.0
    last_shot_end = 38.0  # 2s Lücke > 0.25s Ausklang

    catalog = AssetCatalog()
    catalog.by_id["asset_fallback_still"] = {
        "path": str(still),
        "canonical_id": "asset_fallback_still",
        "duration_seconds": None,
        "media_kind": "image",
        "media_type": "photo",
        "folder": folder,
        "available_start_seconds": 0.0,
    }

    def _fake_resolve(project_arg, **kwargs):  # noqa: ANN001
        return ResolvedShot(
            shot_id=kwargs["shot_id"],
            asset_id=kwargs["asset_id"],
            timeline_start_seconds=float(kwargs["timeline_start"]),
            timeline_end_seconds=float(kwargs["timeline_end"]),
            source_start_seconds=0.0,
            source_end_seconds=float(kwargs["timeline_end"])
            - float(kwargs["timeline_start"]),
            resolved_media_path=str(still),
            resolved_media_kind="image",
            editorial_function=kwargs.get("editorial_function") or "",
            folder_name=folder,
            hold_mode="freeze_video",
        )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_resolve_shot_media",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_reapply_hold_for_timeline_span",
        lambda *args, **kwargs: None,
    )

    shots = [
        ResolvedShot(
            shot_id="Glendalough_slot_001",
            asset_id="asset_open",
            timeline_start_seconds=0.0,
            timeline_end_seconds=20.0,
            source_start_seconds=0.0,
            source_end_seconds=20.0,
            resolved_media_path="/tmp/a.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=30.0,
            editorial_function="chapter_open",
            folder_name=folder,
        ),
        ResolvedShot(
            shot_id="Glendalough_slot_011",
            asset_id="asset_close",
            timeline_start_seconds=20.0,
            timeline_end_seconds=last_shot_end,
            source_start_seconds=0.0,
            source_end_seconds=18.0,
            resolved_media_path="/tmp/b.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=18.0,
            editorial_function="chapter_close",
            folder_name=folder,
        ),
    ]
    audio = [
        ResolvedAudioSegment(
            segment_id="Glendalough_segment_001",
            audio_path="/tmp/g.mp3",
            timeline_start_seconds=0.0,
            timeline_end_seconds=vo_end,
            source_start_seconds=0.0,
            source_end_seconds=vo_end,
        )
    ]
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=vo_end,
        entries=[
            NarrationTimelineEntry(
                segment_id="Glendalough_segment_001",
                start_seconds=0.0,
                end_seconds=vo_end,
                pause_after_seconds=0.0,
                audio_duration_seconds=vo_end,
            )
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Glendalough_slot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=20.0
                ),
                asset_id="asset_open",
            ),
            FinalShot(
                shot_id="Glendalough_slot_011",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=20.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=last_shot_end
                ),
                asset_id="asset_close",
            ),
        ],
    )
    locked = _Locked([_Seg("Glendalough_segment_001", folder, 1)])
    repairs: list[str] = []
    errors: list[str] = []
    envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=shots,
        audio_segments=audio,
        preroll=1.0,
        postroll=2.0,
        fps=25.0,
        repairs=repairs,
        errors=errors,
        narration_timeline=narration,
        catalog=catalog,
        closing_fallback_asset_id="asset_fallback_still",
    )
    assert not errors, errors
    assert any("Closing-Fallback" in r for r in repairs)
    fallback = next(s for s in shots if s.shot_id.endswith("__closing_fallback"))
    assert fallback.asset_id == "asset_fallback_still"
    assert envelopes[0].last_shot_id == fallback.shot_id
    assert envelopes[0].postroll_hold_shot_id == fallback.shot_id


def test_trailing_gap_without_fallback_still_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folder = "Glendalough"
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_reapply_hold_for_timeline_span",
        lambda *args, **kwargs: None,
    )
    vo_end = 40.0
    shots = [
        ResolvedShot(
            shot_id="Glendalough_slot_011",
            asset_id="asset_close",
            timeline_start_seconds=0.0,
            timeline_end_seconds=38.0,
            source_start_seconds=0.0,
            source_end_seconds=38.0,
            resolved_media_path="/tmp/b.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=38.0,
            folder_name=folder,
        )
    ]
    audio = [
        ResolvedAudioSegment(
            segment_id="Glendalough_segment_001",
            audio_path="/tmp/g.mp3",
            timeline_start_seconds=0.0,
            timeline_end_seconds=vo_end,
        )
    ]
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=vo_end,
        entries=[
            NarrationTimelineEntry(
                segment_id="Glendalough_segment_001",
                start_seconds=0.0,
                end_seconds=vo_end,
                audio_duration_seconds=vo_end,
            )
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Glendalough_slot_011",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Glendalough_segment_001", offset_seconds=38.0
                ),
                asset_id="asset_close",
            )
        ],
    )
    errors: list[str] = []
    _apply_chapter_envelopes(
        project,
        locked=_Locked([_Seg("Glendalough_segment_001", folder, 1)]),
        final=final,
        ordered=shots,
        audio_segments=audio,
        preroll=1.0,
        postroll=2.0,
        fps=25.0,
        repairs=[],
        errors=errors,
        narration_timeline=narration,
        catalog=AssetCatalog(),
        closing_fallback_asset_id=None,
    )
    assert any("Kein closing_fallback_asset_id" in e for e in errors)
    assert any("Abschließende visuelle Lücke" in e for e in errors)
