"""Enhanced CutPlanOptions: Shot/Usage/Titel/Still + Prompt/Resolver-Verdrahtung."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    format_shot_constraints_for_prompt,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    cut_plan_options_path,
    final_cut_plan_path,
    narration_timeline_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    require_locked_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_final_cut_prompt,
    build_rough_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir()
    return Project(
        name="SettingsTest",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def test_options_roundtrip_new_fields(tmp_path: Path) -> None:
    project = _project(tmp_path)
    defaults = load_cut_plan_options(project)
    assert defaults.shot_max_sec == 8.0
    assert defaults.max_asset_usage == 2
    assert defaults.min_asset_reuse_distance_shots == 4
    assert defaults.voiceover_preroll_sec == 1.0
    assert defaults.voiceover_postroll_sec == 5.0
    assert defaults.short_asset_tolerance_sec == 1.0
    saved = save_cut_plan_options(
        project,
        CutPlanOptions(
            shot_min_sec=2.5,
            shot_max_sec=6.0,
            video_head_trim_sec=0.5,
            max_asset_usage=3,
            min_asset_reuse_distance_shots=4,
            voiceover_preroll_sec=1.5,
            voiceover_preroll_mode="llm",
            voiceover_postroll_sec=4.0,
            voiceover_postroll_mode="llm",
            short_asset_tolerance_sec=1.5,
            folder_title_enabled=True,
            still_image_zoom=0.75,
            still_image_background_style="none",
        ),
    )
    loaded = load_cut_plan_options(project)
    assert loaded.shot_min_sec == saved.shot_min_sec == 2.5
    assert loaded.shot_max_sec == 6.0
    assert loaded.video_head_trim_sec == 0.5
    assert loaded.max_asset_usage == 3
    assert loaded.min_asset_reuse_distance_shots == 4
    assert loaded.voiceover_preroll_mode == "llm"
    assert loaded.voiceover_postroll_sec == 4.0
    assert loaded.short_asset_tolerance_sec == 1.5
    assert loaded.folder_title_enabled is True
    assert loaded.still_image_zoom == 0.75
    assert loaded.still_image_background_style == "none"


def test_legacy_options_json_still_loads(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = cut_plan_options_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version":"1.0","include_middle_frames":true,'
        '"max_middle_frames_per_chapter":12,"max_candidates_per_gap":10}',
        encoding="utf-8",
    )
    loaded = load_cut_plan_options(project)
    assert loaded.include_middle_frames is True
    assert loaded.max_middle_frames_per_chapter == 12
    assert loaded.max_candidates_per_gap == 10
    assert loaded.shot_max_sec == 8.0


def test_prompt_constraints_in_rough_and_final() -> None:
    text = format_shot_constraints_for_prompt(
        CutPlanOptions(
            shot_min_sec=3.0,
            shot_max_sec=7.0,
            max_asset_usage=2,
            voiceover_preroll_sec=1.0,
            voiceover_preroll_mode="fixed",
            voiceover_postroll_sec=5.0,
            voiceover_postroll_mode="fixed",
        )
    )
    rough = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="",
        dramaturgy_text="",
        shot_constraints_text=text,
    )
    final = build_final_cut_prompt(
        locked_script_json="{}",
        narration_timeline_json="{}",
        pause_directives_json="[]",
        rough_cut_json="{}",
        local_assets_json="[]",
        accepted_supplements_json="{}",
        style_profile_text="",
        shot_constraints_text=text,
    )
    assert "7.0s" in rough and "SHOT / ASSET CONSTRAINTS" in rough
    assert "reuse gap" in rough.lower() or "4" in rough
    assert "7.0s" in final and "duration_seconds" in final
    assert "short-asset tolerance" in final.lower()
    assert "OPENING SHOT" in text and "CLOSING SHOT" in text
    assert "1.0s" in text and "5.0s" in text
    assert "OPENING SHOT" in rough and "CLOSING SHOT" in rough
    assert "OPENING SHOT" in final and "CLOSING SHOT" in final
    assert "Vorlauf" in final and "Nachlauf" in final
    assert "leading or trailing narration" in final.lower()
    assert "MUST differ from the immediately following shot" in text
    assert "COUNT toward max asset usage" in text
    assert "Never place the same non-intro asset on two consecutive shots" in text
    assert "No two consecutive shots share the same non-intro asset_id" in final


def test_resolver_clamps_to_shot_max(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Long narration segment for timing.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Long narration segment for timing.",
                    sequence_index=1,
                    folder_name="Canyon",
                )
            ],
        ),
    )
    lock_script(project)
    locked = require_locked_script(project)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            shot_min_sec=2.0,
            shot_max_sec=5.0,
            max_asset_usage=5,
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
        ),
    )

    media = Path(project.project_root) / "Canyon" / "clip.jpg"
    Image.new("RGB", (64, 64), color=(20, 40, 60)).save(media, format="JPEG")
    write_json(
        get_folder_inventory_path(project.work_dir_path, "Canyon"),
        AssetFolderAnalysis(
            folder="Canyon",
            media_files=[str(media)],
            assets=[
                AssetMediaAnalysis(
                    path=str(media),
                    asset_id="asset_still_1",
                    description="still",
                    media_type="photo",
                )
            ],
        ),
    )
    audio = Path(project.work_dir) / "seg.wav"
    audio.write_bytes(b"RIFF....WAVE")
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version=locked.script_version,
            segments=[
                SegmentTiming(
                    segment_id="Canyon_segment_001",
                    script_version=locked.script_version,
                    audio_path=str(audio),
                    duration_seconds=20.0,
                )
            ],
        ),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version=locked.script_version,
            total_duration_seconds=20.0,
            entries=[
                NarrationTimelineEntry(
                    segment_id="Canyon_segment_001",
                    start_seconds=0.0,
                    end_seconds=20.0,
                    pause_after_seconds=0.0,
                )
            ],
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version=locked.script_version,
            shots=[
                FinalShot(
                    shot_id="Canyon_shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=20.0
                    ),
                    asset_id="asset_still_1",
                )
            ],
        ),
    )

    # shot_max kürzt auf 5s; die verbleibende Narration (15s) darf nicht durch
    # Kapitelhüllen-Stretch verdeckt werden → fail-closed Rohabdeckung.
    with pytest.raises(TimelineResolveError, match="Abschließende visuelle Lücke"):
        resolve_final_timeline(project)


def test_ui_settings_markers() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "_render_cut_plan_settings" in source
    assert "Cut Plan Settings speichern" in source
    assert "enh_opt_shot_max_" in source
    assert "enh_opt_preroll_" in source
    assert "enh_opt_postroll_" in source
    assert "Asset-Reuse-Abstand" in source
    assert "Still-Style aktiv" in source
    assert "Ordner-Titel einblenden" in source


def test_resolver_applies_preroll_and_tolerance(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Long narration segment for timing.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Long narration segment for timing.",
                    sequence_index=1,
                    folder_name="Canyon",
                )
            ],
        ),
    )
    lock_script(project)
    locked = require_locked_script(project)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            shot_min_sec=1.0,
            shot_max_sec=30.0,
            max_asset_usage=5,
            voiceover_preroll_sec=1.0,
            voiceover_preroll_mode="fixed",
            voiceover_postroll_sec=2.0,
            voiceover_postroll_mode="fixed",
            short_asset_tolerance_sec=2.0,
        ),
    )
    media = Path(project.project_root) / "Canyon" / "clip.jpg"
    Image.new("RGB", (64, 64), color=(20, 40, 60)).save(media, format="JPEG")
    write_json(
        get_folder_inventory_path(project.work_dir_path, "Canyon"),
        AssetFolderAnalysis(
            folder="Canyon",
            media_files=[str(media)],
            assets=[
                AssetMediaAnalysis(
                    path=str(media),
                    asset_id="asset_still_1",
                    description="still",
                    media_type="photo",
                )
            ],
        ),
    )
    audio = Path(project.work_dir) / "seg.wav"
    audio.write_bytes(b"RIFF....WAVE")
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version=locked.script_version,
            segments=[
                SegmentTiming(
                    segment_id="Canyon_segment_001",
                    script_version=locked.script_version,
                    audio_path=str(audio),
                    duration_seconds=6.0,
                )
            ],
        ),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version=locked.script_version,
            total_duration_seconds=6.0,
            entries=[
                NarrationTimelineEntry(
                    segment_id="Canyon_segment_001",
                    start_seconds=0.0,
                    end_seconds=6.0,
                    pause_after_seconds=0.0,
                )
            ],
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version=locked.script_version,
            shots=[
                FinalShot(
                    shot_id="Canyon_shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=6.0
                    ),
                    asset_id="asset_still_1",
                )
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    assert resolved.voiceover_preroll_sec == 1.0
    assert resolved.voiceover_postroll_sec == 2.0
    assert resolved.audio_segments[0].timeline_start_seconds == 1.0
    editorial = next(s for s in resolved.shots if s.shot_id == "Canyon_shot_001")
    # Opening/Closing = derselbe Shot: Vorlauf + Nachlauf verlängern ihn in-place.
    assert editorial.timeline_start_seconds == pytest.approx(0.0, abs=1e-3)
    assert editorial.timeline_end_seconds == pytest.approx(9.0, abs=1e-3)
    assert not any(
        str(s.editorial_function or "").startswith("technical_chapter_")
        for s in resolved.shots
    )
    assert any("Opening-/Closing-Shot verlängert" in r for r in resolved.repairs)
