"""Fix 3: Kapitel-Bridge füllen / nie in coverage_gaps."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
    fill_chapter_bridges,
    is_bridge_shot,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    GapMergeReport,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import CutPlanOptions
from otio_app.services.without_voiceover_enhanced.timeline_resolver import AssetCatalog
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def test_unified_to_rough_excludes_bridge_from_coverage_gaps() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
            CutBoundary(cut_id="b2", sentence_id="b__s001", position="start"),
            CutBoundary(cut_id="b3", sentence_id="b__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="A_slot_001",
                local_asset_id="asset_a",
                asset_fit="strong",
            ),
            CutSlot(
                slot_id="bridge_001",
                asset_fit="none",
                narrative_function="chapter_transition",
                needed_visual="chapter transition",
            ),
            CutSlot(
                slot_id="B_slot_001",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_b",
                needed_visual="lake detail",
            ),
        ],
    )
    _rough, coverage = unified_to_rough(plan)
    gap_ids = [g.gap_id for g in coverage.gaps]
    assert "gap_b" in gap_ids
    assert not any("bridge" in g for g in gap_ids)
    bridge_shot = next(s for s in _rough.shots if s.shot_id == "bridge_001")
    assert bridge_shot.coverage_gap_id is None


def test_is_bridge_shot_detection() -> None:
    assert is_bridge_shot(
        ResolvedShot(
            shot_id="bridge_001",
            asset_id="",
            timeline_start_seconds=0,
            timeline_end_seconds=1,
            source_start_seconds=0,
            source_end_seconds=1,
        )
    )
    assert is_bridge_shot(
        ResolvedShot(
            shot_id="x",
            asset_id="",
            timeline_start_seconds=0,
            timeline_end_seconds=1,
            source_start_seconds=0,
            source_end_seconds=1,
            editorial_function="chapter_transition",
        )
    )


def test_fill_bridge_extends_previous_when_remaining_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "close.mp4"
    media.write_bytes(b"fake")
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
    )
    catalog = AssetCatalog()
    catalog.by_id["asset_close"] = {
        "path": str(media),
        "duration_seconds": 20.0,
        "usable_in_s": 0.0,
        "available_start_seconds": 0.0,
        "media_kind": "video",
        "folder": "Yosemite",
        "canonical_id": "asset_close",
    }
    timeline = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        shots=[
            ResolvedShot(
                shot_id="Yosemite_slot_close",
                asset_id="asset_close",
                timeline_start_seconds=0.0,
                timeline_end_seconds=8.0,
                source_start_seconds=0.0,
                source_end_seconds=8.0,
                resolved_media_path=str(media),
                resolved_media_kind="video",
                resolved_media_duration_seconds=20.0,
                folder_name="Yosemite",
                editorial_function="chapter_close",
                open_gap=False,
            ),
            ResolvedShot(
                shot_id="bridge_001",
                asset_id="",
                timeline_start_seconds=8.0,
                timeline_end_seconds=10.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                resolved_media_path="",
                editorial_function="chapter_transition",
                open_gap=True,
                is_placeholder=True,
            ),
        ],
    )
    report = GapMergeReport(script_version="v1")
    repairs: list[str] = []
    out = fill_chapter_bridges(
        project,
        timeline,
        unified=None,
        catalog=catalog,
        options=CutPlanOptions(short_asset_tolerance_sec=1.0, video_head_trim_sec=0.0),
        repairs=repairs,
        report=report,
    )
    assert len(out) == 1
    assert out[0].shot_id == "Yosemite_slot_close"
    assert out[0].timeline_end_seconds == pytest.approx(10.0)
    assert out[0].source_end_seconds == pytest.approx(10.0)
    assert any(s.status == "bridge_extended" for s in report.slots)


def test_fill_bridge_uses_candidate_when_extend_impossible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close = tmp_path / "close.mp4"
    alt = tmp_path / "alt.mp4"
    close.write_bytes(b"a")
    alt.write_bytes(b"b")
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
    )
    catalog = AssetCatalog()
    catalog.by_id["asset_close"] = {
        "path": str(close),
        "duration_seconds": 8.0,
        "usable_in_s": 0.0,
        "available_start_seconds": 0.0,
        "media_kind": "video",
        "folder": "Yosemite",
        "canonical_id": "asset_close",
    }
    catalog.by_id["asset_alt"] = {
        "path": str(alt),
        "duration_seconds": 12.0,
        "usable_in_s": 0.0,
        "available_start_seconds": 0.0,
        "media_kind": "video",
        "folder": "Yosemite",
        "canonical_id": "asset_alt",
    }

    def _fake_resolve(project, *, shot_id, asset_id, entry, timeline_start, timeline_end, **kwargs):
        dur = timeline_end - timeline_start
        return ResolvedShot(
            shot_id=shot_id,
            asset_id=asset_id,
            timeline_start_seconds=timeline_start,
            timeline_end_seconds=timeline_end,
            source_start_seconds=0.0,
            source_end_seconds=dur,
            resolved_media_path=str(entry["path"]),
            resolved_media_kind="video",
            resolved_media_duration_seconds=float(entry["duration_seconds"]),
            folder_name=str(entry.get("folder") or ""),
            open_gap=False,
            is_placeholder=False,
        )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.gap_merge_service._resolve_shot_media",
        _fake_resolve,
    )

    unified = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
            CutBoundary(cut_id="b2", sentence_id="b__s001", position="start"),
        ],
        slots=[
            CutSlot(slot_id="Yosemite_slot_close", local_asset_id="asset_close", asset_fit="strong"),
            CutSlot(
                slot_id="bridge_001",
                asset_fit="none",
                narrative_function="chapter_transition",
                bridge_candidate_asset_ids=["asset_alt"],
            ),
        ],
    )
    timeline = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        shots=[
            ResolvedShot(
                shot_id="Yosemite_slot_close",
                asset_id="asset_close",
                timeline_start_seconds=0.0,
                timeline_end_seconds=8.0,
                source_start_seconds=0.0,
                source_end_seconds=8.0,
                resolved_media_path=str(close),
                resolved_media_kind="video",
                resolved_media_duration_seconds=8.0,
                folder_name="Yosemite",
                editorial_function="chapter_close",
            ),
            ResolvedShot(
                shot_id="bridge_001",
                asset_id="",
                timeline_start_seconds=8.0,
                timeline_end_seconds=11.0,
                source_start_seconds=0.0,
                source_end_seconds=3.0,
                editorial_function="chapter_transition",
                open_gap=True,
                is_placeholder=True,
            ),
        ],
    )
    report = GapMergeReport(script_version="v1")
    out = fill_chapter_bridges(
        project,
        timeline,
        unified=unified,
        catalog=catalog,
        options=CutPlanOptions(short_asset_tolerance_sec=0.0, video_head_trim_sec=0.0),
        repairs=[],
        report=report,
    )
    assert len(out) == 2
    assert out[1].shot_id == "bridge_001"
    assert out[1].asset_id == "asset_alt"
    assert out[1].open_gap is False
    assert any(s.status == "bridge_filled" for s in report.slots)
