"""MVP-Regressionstests für without_voiceover_enhanced."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    measure_audio_duration_seconds,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    accept_supplement_candidates,
    parse_rough_cut_response,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    PauseDirective,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
    StockCandidate,
    StockSearchResultsDocument,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    assert_enhanced_work_root,
    final_cut_plan_path,
    narration_timeline_path,
    resolved_timeline_path,
    script_locked_path,
    segment_timings_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    PAUSE_DURATION_SECONDS,
    resolve_pause_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    chapter_narration_text,
    folders_present_in_script,
    generate_all_enhanced_scripts,
    generate_enhanced_script_for_folder,
    group_segments_by_folder,
    list_enabled_dramaturgy_folders,
    merge_folder_script_into_document,
    parse_enhanced_script_response,
    revise_enhanced_script_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_script_draft,
    lock_script,
    mark_segment_text_changed,
    save_script_draft,
    update_folder_chapter_narration,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    FORBIDDEN_PHRASES,
    build_enhanced_folder_script_prompt,
    build_enhanced_script_prompt,
    build_enhanced_script_revision_prompt,
)
from otio_app.services.without_voiceover_enhanced.stock.mock import MockStockProvider
from otio_app.services.without_voiceover_enhanced.stock.registry import (
    REQUIRED_PROVIDER_NAMES,
    get_stock_providers,
    search_all_providers,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    detect_one_to_one_sentence_asset,
    resolve_final_timeline,
)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder_names = folders or ["Assets"]
    for folder in folder_names:
        (root / folder).mkdir(exist_ok=True)
    return Project(
        name="Enhanced Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=folder_names,
        selected_asset_subdirs=folder_names,
        fps=25.0,
    )


def _confirm_dramaturgy(project: Project, folders: list[str]) -> DramaturgyPlan:
    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Enhanced Test Film",
        core_promise="Atmosphäre und Geschichte",
        narrative_arc="Hook → Entwicklung → Payoff",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder,
                order_index=index,
                enabled=True,
                dramaturgy_role="hook" if index == 0 else "development",
                reason=f"Kapitel {folder}",
                recommended_word_count=150,
                recommended_min_words=120,
                recommended_max_words=180,
            )
            for index, folder in enumerate(folders)
        ],
    )
    return save_confirmed_dramaturgy(project, plan)


def _fake_folder_llm_response(folder_name: str) -> str:
    slug = folder_name.lower().replace(" ", "_")
    return (
        "{"
        f'"narration_full": "Narration für {folder_name}.",'
        f'"segments": [{{'
        f'"segment_id": "{slug}_segment_001",'
        f'"text": "Narration für {folder_name}.",'
        f'"sequence_index": 1,'
        f'"semantic_function": "atmosphere",'
        f'"visual_intent_ids": ["{slug}_intent_001"],'
        f'"fact_check_required": false,'
        f'"folder_name": "{folder_name}"'
        "}],"
        f'"visual_intents": [{{'
        f'"intent_id": "{slug}_intent_001",'
        f'"description": "Wide establishing for {folder_name}",'
        f'"subject": "{folder_name}",'
        f'"location": "{folder_name}",'
        f'"preferred_media_type": "video",'
        f'"folder_name": "{folder_name}"'
        "}],"
        '"visual_beats": [],'
        '"coverage_needs": [],'
        '"fact_check_hints": []'
        "}"
    )


def _write_silent_wav(path: Path, duration_seconds: float = 0.5, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n_frames)


def test_isolation_work_dirs_and_modes() -> None:
    assert ProjectMode.WITH_VOICEOVER.value == "with_voiceover"
    assert ProjectMode.WITHOUT_VOICEOVER.value == "without_voiceover"
    assert ProjectMode.WITHOUT_VOICEOVER_ENHANCED.value == "without_voiceover_enhanced"
    assert DEFAULT_WORK_SUBDIR == "_otio"
    assert DEFAULT_ENHANCED_WORK_SUBDIR == "_otio_enhanced"
    assert "_otio_v2" != DEFAULT_ENHANCED_WORK_SUBDIR


def test_assert_enhanced_work_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert assert_enhanced_work_root(project).name == DEFAULT_ENHANCED_WORK_SUBDIR
    bad = project.model_copy(update={"work_dir": str(tmp_path / "_otio")})
    with pytest.raises(ValueError):
        assert_enhanced_work_root(bad)


def test_script_prompt_forbids_image_caption_phrases() -> None:
    prompt = build_enhanced_script_prompt(
        project_brief_text="Brief",
        dramaturgy_text="Dramaturgy",
        style_profile_text="Style",
        verified_facts_text="Facts",
        asset_inventory_summary="Assets",
    )
    for phrase in FORBIDDEN_PHRASES:
        assert phrase in prompt
    assert "VISUAL RESOURCE" in prompt
    assert "must NOT fully constrain" in prompt


def test_parse_script_separates_visual_intents_and_marks_unverified() -> None:
    raw = {
        "narration_full": (
            "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet, "
            "beginnen die Felsen beinahe wie Kristalle zu funkeln."
        ),
        "segments": [
            {
                "segment_id": "segment_001",
                "text": "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet,",
                "sequence_index": 1,
                "semantic_function": "atmosphere",
                "visual_intent_ids": ["intent_001"],
                "fact_check_required": False,
            },
            {
                "segment_id": "segment_002",
                "text": "beginnen die Felsen beinahe wie Kristalle zu funkeln.",
                "sequence_index": 2,
                "semantic_function": "atmosphere",
                "visual_intent_ids": ["intent_001"],
                "fact_check_required": True,
            },
        ],
        "visual_beats": [
            {
                "beat_id": "beat_001",
                "description": "Wide sunset ridges",
                "related_segment_ids": ["segment_001", "segment_002"],
                "visual_intent_ids": ["intent_001"],
            }
        ],
        "visual_intents": [
            {
                "intent_id": "intent_001",
                "description": "Crystal-like rock glow at sunset",
                "subject": "ridges",
                "location": "mountains",
                "preferred_media_type": "video",
            }
        ],
        "coverage_needs": [],
        "fact_check_hints": [],
    }
    doc = parse_enhanced_script_response(raw)
    assert "Das Bild zeigt" not in doc.narration_full
    assert doc.visual_intents[0].intent_id == "intent_001"
    assert doc.segments[0].text != doc.visual_intents[0].description
    assert any(h.related_segment_id == "segment_002" for h in doc.fact_check_hints)


def test_script_lock_version_and_text_change_invalidates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    draft = EnhancedScriptDocument(
        narration_full="Hallo Welt. Zweiter Satz.",
        segments=[
            ScriptSegment(
                segment_id="segment_001",
                text="Hallo Welt.",
                sequence_index=1,
                semantic_function="history",
            ),
            ScriptSegment(
                segment_id="segment_002",
                text="Zweiter Satz.",
                sequence_index=2,
                semantic_function="transition",
            ),
        ],
        visual_intents=[
            VisualIntent(intent_id="intent_001", description="wide landscape")
        ],
    )
    save_script_draft(project, draft)
    locked = lock_script(project)
    assert locked.script_version == "script-v1"
    assert locked.script_status == "locked"
    assert script_locked_path(project).is_file()

    mark_segment_text_changed(project, "segment_001", "Hallo veränderte Welt.")
    assert not script_locked_path(project).is_file()


def test_pause_duration_classes_central_and_deterministic(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.pause_config."
        "ENHANCED_VOICEOVER_PAUSES_ENABLED",
        True,
    )
    assert PAUSE_DURATION_SECONDS["short"] == 0.35
    assert PAUSE_DURATION_SECONDS["medium"] == 0.80
    assert PAUSE_DURATION_SECONDS["long"] == 1.50
    assert resolve_pause_duration_seconds("short") == 0.35
    assert resolve_pause_duration_seconds("medium") == 0.80
    assert resolve_pause_duration_seconds("long") == 1.50
    with pytest.raises(ValueError):
        resolve_pause_duration_seconds("huge")


def test_voiceover_pauses_disabled_resolve_to_zero() -> None:
    from otio_app.services.without_voiceover_enhanced.pause_config import (
        ENHANCED_VOICEOVER_PAUSES_ENABLED,
    )

    assert ENHANCED_VOICEOVER_PAUSES_ENABLED is False
    assert resolve_pause_duration_seconds("medium") == 0.0
    assert resolve_pause_duration_seconds("huge") == 0.0


def test_pause_resolver_identical_inputs_identical_outputs_and_pause_without_cut(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.pause_config."
        "ENHANCED_VOICEOVER_PAUSES_ENABLED",
        True,
    )
    timings = [
        SegmentTiming(
            segment_id="segment_001",
            script_version="script-v1",
            audio_path="a.mp3",
            duration_seconds=2.0,
        ),
        SegmentTiming(
            segment_id="segment_002",
            script_version="script-v1",
            audio_path="b.mp3",
            duration_seconds=3.0,
        ),
    ]
    directives = [
        PauseDirective(
            after_segment_id="segment_001",
            pause_function="anticipation",
            duration_class="medium",
            visual_behavior="next_shot_may_start_during_pause",
        )
    ]
    first = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=directives,
    )
    second = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=directives,
    )
    assert first.model_dump() == second.model_dump()
    assert first.entries[0].pause_after_seconds == 0.80
    assert first.entries[1].start_seconds == pytest.approx(2.80)
    # Pause without picture cut is representable: directive has visual_behavior hold.
    hold = PauseDirective(
        after_segment_id="segment_001",
        pause_function="breath",
        duration_class="short",
        visual_behavior="hold_current_shot",
    )
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=timings,
        pause_directives=[hold],
    )
    assert timeline.entries[0].pause_after_seconds == 0.35


def test_audio_duration_measured_from_file(tmp_path: Path) -> None:
    wav = tmp_path / "seg.wav"
    _write_silent_wav(wav, duration_seconds=0.5)
    duration = measure_audio_duration_seconds(wav)
    assert duration == pytest.approx(0.5, abs=0.05)


def test_wrong_script_version_detected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    draft = EnhancedScriptDocument(
        narration_full="Eins.",
        segments=[
            ScriptSegment(segment_id="segment_001", text="Eins.", sequence_index=1)
        ],
    )
    save_script_draft(project, draft)
    locked = lock_script(project)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v999",
            segments=[
                SegmentTiming(
                    segment_id="segment_001",
                    script_version="script-v999",
                    audio_path="missing.wav",
                    duration_seconds=1.0,
                )
            ],
        ),
    )
    errors = validate_timings_against_script(project)
    assert any("Skriptversion" in err for err in errors)
    assert locked.script_version == "script-v1"


def test_shot_freedom_multi_segment_and_multi_shot() -> None:
    rough, coverage = parse_rough_cut_response(
        {
            "pause_directives": [
                {
                    "after_segment_id": "segment_001",
                    "pause_function": "anticipation",
                    "duration_class": "medium",
                    "visual_behavior": "next_shot_may_start_during_pause",
                    "editorial_reason": "Spannung",
                }
            ],
            "shots": [
                {
                    "shot_id": "shot_001",
                    "start_anchor": {
                        "type": "segment",
                        "segment_id": "segment_001",
                        "after_segment_id": None,
                        "position": "start",
                    },
                    "end_anchor": {
                        "type": "segment",
                        "segment_id": "segment_002",
                        "after_segment_id": None,
                        "position": "middle",
                    },
                    "narrative_function": "orientation",
                    "visual_intent": "Hold across two statements",
                    "local_asset_id": "asset_a",
                    "asset_fit": "strong",
                    "asset_fit_reason": "Clear establishing landscape",
                    "continuity_notes": "",
                    "coverage_gap_id": None,
                },
                {
                    "shot_id": "shot_002",
                    "start_anchor": {
                        "type": "segment",
                        "segment_id": "segment_002",
                        "after_segment_id": None,
                        "position": "middle",
                    },
                    "end_anchor": {
                        "type": "segment",
                        "segment_id": "segment_002",
                        "after_segment_id": None,
                        "position": "end",
                    },
                    "narrative_function": "evidence",
                    "visual_intent": "Detail inside same segment",
                    "local_asset_id": None,
                    "asset_fit": "none",
                    "asset_fit_reason": "No matching local detail",
                    "continuity_notes": "",
                    "coverage_gap_id": "gap_002",
                },
            ],
            "coverage_gaps": [
                {
                    "coverage_gap_id": "gap_002",
                    "shot_id": "shot_002",
                    "needed_visual": "Close rock texture detail",
                    "editorial_purpose": "Detail insert",
                    "preferred_media_type": "photo",
                    "search_concepts": ["red sandstone close up"],
                    "must_include": ["rock texture"],
                    "must_avoid": ["city skyline"],
                    "fact_check_required": False,
                }
            ],
        },
        "script-v1",
    )
    assert len(rough.shots) == 2
    assert rough.shots[0].start_anchor.segment_id != rough.shots[0].end_anchor.segment_id
    assert rough.shots[1].start_anchor.segment_id == "segment_002"
    assert rough.shots[1].local_asset_id is None
    assert rough.shots[1].coverage_gap_id == "gap_002"
    assert any(g.gap_id == "gap_002" and g.related_shot_ids == ["shot_002"] for g in coverage.gaps)
    assert coverage.gaps[0].search_concepts == ["red sandstone close up"]
    assert all("frame" not in d.model_dump_json() for d in rough.pause_directives)


def test_rough_cut_prompt_uses_editorial_anchors_not_seconds() -> None:
    from otio_app.services.without_voiceover_enhanced.script_prompts import (
        build_rough_cut_prompt,
    )

    prompt = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="drama",
    )
    assert "You are LLM 2" in prompt
    assert "start | early | middle | late | end" in prompt
    assert "Do not output seconds" in prompt
    assert "offset_seconds" not in prompt
    assert "coverage_gap_id" in prompt
    assert "local_asset_id" in prompt


def test_stock_providers_registered_and_unavailable_does_not_stop_others() -> None:
    names = [p.provider_name for p in get_stock_providers()]
    assert list(REQUIRED_PROVIDER_NAMES) == [
        "pexels",
        "pixabay",
        "wikimedia",
        "openverse",
        "archive_org",
    ]
    assert set(REQUIRED_PROVIDER_NAMES) == set(names)
    assert "adobe_stock" not in names

    class UnavailableProvider(MockStockProvider):
        provider_name = "mock_unavailable"

    class ReadyProvider(MockStockProvider):
        provider_name = "mock_ready"

    unavailable = UnavailableProvider(available=False)
    ready = ReadyProvider(available=True)
    candidates, status = search_all_providers(
        "Monument Valley",
        providers=[unavailable, ready],
        enabled_names=["mock_unavailable", "mock_ready"],
    )
    assert status["mock_unavailable"] == "unavailable"
    assert status["mock_ready"] == "completed"
    assert candidates
    assert candidates[0].license == "CC0"
    assert candidates[0].width == 4000


def test_only_accepted_supplements_persisted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    draft = EnhancedScriptDocument(
        narration_full="Text",
        segments=[ScriptSegment(segment_id="segment_001", text="Text", sequence_index=1)],
    )
    save_script_draft(project, draft)
    lock_script(project)
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=[
                StockCandidate(
                    candidate_id="stock_001",
                    provider="wikimedia",
                    title="A",
                    license="CC BY-SA 4.0",
                ),
                StockCandidate(
                    candidate_id="stock_002",
                    provider="pexels",
                    title="B",
                    license="Pexels License",
                ),
            ],
        ),
    )
    accepted = accept_supplement_candidates(project, ["stock_001"])
    assert [s.candidate_id for s in accepted.supplements] == ["stock_001"]
    assert accepted_supplements_path(project).is_file()


def test_final_plan_rejects_unknown_ids_and_resolves_deterministically(tmp_path: Path) -> None:
    project = _project(tmp_path)
    draft = EnhancedScriptDocument(
        narration_full="Eins. Zwei.",
        segments=[
            ScriptSegment(segment_id="segment_001", text="Eins.", sequence_index=1),
            ScriptSegment(segment_id="segment_002", text="Zwei.", sequence_index=2),
        ],
    )
    save_script_draft(project, draft)
    lock_script(project)

    wav1 = project.work_dir_path / "a1.wav"
    wav2 = project.work_dir_path / "a2.wav"
    _write_silent_wav(wav1, 1.0)
    _write_silent_wav(wav2, 1.0)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v1",
            segments=[
                SegmentTiming(
                    segment_id="segment_001",
                    script_version="script-v1",
                    audio_path=str(wav1),
                    duration_seconds=1.0,
                ),
                SegmentTiming(
                    segment_id="segment_002",
                    script_version="script-v1",
                    audio_path=str(wav2),
                    duration_seconds=1.0,
                ),
            ],
        ),
    )
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=[
            SegmentTiming(
                segment_id="segment_001",
                script_version="script-v1",
                audio_path=str(wav1),
                duration_seconds=1.0,
            ),
            SegmentTiming(
                segment_id="segment_002",
                script_version="script-v1",
                audio_path=str(wav2),
                duration_seconds=1.0,
            ),
        ],
        pause_directives=[
            PauseDirective(
                after_segment_id="segment_001",
                pause_function="breath",
                duration_class="short",
                visual_behavior="hold_current_shot",
            )
        ],
    )
    write_json(narration_timeline_path(project), timeline)

    # Create local asset catalog via accepted supplement (avoid inventory dependency).
    # R2: echte kleine Videodatei (ffprobe muss Videospur erkennen).
    import subprocess

    media_file = project.work_dir_path / "local_media.mp4"
    created = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=16x16:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            "1",
            str(media_file),
        ],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0 or not media_file.is_file():
        pytest.skip("ffmpeg konnte keine Test-Videodatei erzeugen")
    write_json(
        accepted_supplements_path(project),
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": "script-v1",
            "supplements": [
                {
                    "candidate_id": "asset_local_1",
                    "provider": "mock",
                    "title": "wide",
                    "media_type": "video",
                    "preview_url": "https://example.com/preview.jpg",
                    "source_page": "https://example.com/source",
                    "local_media_path": str(media_file),
                    "media_validation_status": "export_ready",
                    "duration_seconds": 10.0,
                    "license": "CC0",
                    "selected": True,
                }
            ],
        },
    )
    final = FinalCutPlanDocument(
        script_version="script-v1",
        shots=[
            FinalShot(
                shot_id="shot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="segment_002", offset_seconds=0.5
                ),
                asset_id="asset_local_1",
                editorial_function="orientation",
                may_overlap_pause=True,
            ),
            FinalShot(
                shot_id="shot_002",
                narration_start_anchor=NarrationAnchor(
                    segment_id="segment_002", offset_seconds=0.5
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="segment_002", offset_seconds=1.0
                ),
                asset_id="asset_local_1",
                editorial_function="detail",
            ),
        ],
    )
    write_json(final_cut_plan_path(project), final)
    assert not detect_one_to_one_sentence_asset(final, segment_count=2)

    resolved_a = resolve_final_timeline(project)
    resolved_b = resolve_final_timeline(project)
    assert resolved_a.model_dump() == resolved_b.model_dump()
    assert resolved_a.shots[0].source_end_seconds <= 10.0

    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="shot_bad",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=0.5
                    ),
                    asset_id="unknown_asset",
                )
            ],
        ),
    )
    with pytest.raises(TimelineResolveError, match="Unbekannte Asset-ID"):
        resolve_final_timeline(project)

    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="shot_bad_seg",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="segment_999", offset_seconds=0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="segment_999", offset_seconds=0.5
                    ),
                    asset_id="asset_local_1",
                )
            ],
        ),
    )
    with pytest.raises(TimelineResolveError, match="Unbekannte Segment-ID"):
        resolve_final_timeline(project)


def test_otio_from_resolved_only(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )

    project = _project(tmp_path)
    save_cut_plan_options(
        project, CutPlanOptions(still_image_style_enabled=False)
    )
    wav = project.work_dir_path / "voice.wav"
    _write_silent_wav(wav, 1.0)
    write_json(
        resolved_timeline_path(project),
        {
            "schema_version": "enhanced-resolved-timeline-v1",
            "script_version": "script-v1",
            "fps": 25.0,
            "total_duration_seconds": 1.35,
            "audio_segments": [
                {
                    "segment_id": "segment_001",
                    "audio_path": str(wav),
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 1.0,
                    "pause_after_seconds": 0.35,
                }
            ],
            "shots": [
                {
                    "shot_id": "shot_001",
                    "asset_id": "asset_local_1",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 0.8,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 0.8,
                },
                {
                    "shot_id": "shot_002",
                    "asset_id": "asset_local_1",
                    "timeline_start_seconds": 0.8,
                    "timeline_end_seconds": 1.35,
                    "source_start_seconds": 1.0,
                    "source_end_seconds": 1.55,
                },
            ],
            "repairs": [],
            "errors": [],
        },
    )
    from PIL import Image

    media_file = project.work_dir_path / "local_clip.jpg"
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(media_file, format="JPEG")
    write_json(
        accepted_supplements_path(project),
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": "script-v1",
            "supplements": [
                {
                    "candidate_id": "asset_local_1",
                    "provider": "mock",
                    "media_type": "photo",
                    "preview_url": "https://example.com/preview.jpg",
                    "source_page": "https://example.com/source",
                    "local_media_path": str(media_file),
                    "media_validation_status": "export_ready",
                    "duration_seconds": 10.0,
                    "selected": True,
                }
            ],
        },
    )
    out = export_otio_from_resolved_timeline(project, basename="test_enhanced")
    assert out.is_file()
    payload = out.read_text(encoding="utf-8")
    assert "segment_001" in payload
    assert "shot_001" in payload
    assert "shot_002" in payload
    assert "https://" not in payload
    assert "http://" not in payload
    assert str(media_file) in payload


def test_coverage_gap_has_search_queries_and_links() -> None:
    _rough, coverage = parse_rough_cut_response(
        {
            "pause_directives": [],
            "shots": [
                {
                    "shot_id": "shot_007",
                    "start_anchor": {
                        "type": "segment",
                        "segment_id": "segment_003",
                        "position": "early",
                    },
                    "end_anchor": {
                        "type": "segment",
                        "segment_id": "segment_005",
                        "position": "middle",
                    },
                    "narrative_function": "orientation",
                    "visual_intent": "Monument Valley establish",
                    "local_asset_id": None,
                    "asset_fit": "none",
                    "asset_fit_reason": "missing local",
                    "coverage_gap_id": "gap_008",
                }
            ],
            "coverage_gaps": [
                {
                    "coverage_gap_id": "gap_008",
                    "shot_id": "shot_007",
                    "needed_visual": "Monument Valley wide establishing shot",
                    "editorial_purpose": "Kein geografisch passendes lokales Asset vorhanden.",
                    "preferred_media_type": "video",
                    "search_concepts": [
                        "Monument Valley wide landscape",
                        "Monument Valley sunset panorama",
                    ],
                    "must_include": ["buttes"],
                    "must_avoid": ["highway traffic"],
                    "fact_check_required": True,
                }
            ],
        },
        "script-v1",
    )
    gap = coverage.gaps[0]
    assert gap.related_shot_ids == ["shot_007"]
    assert gap.needed_visual.startswith("Monument Valley")
    assert "Monument Valley wide landscape" in gap.search_concepts
    assert gap.fact_check_required is True


def test_chapter_narration_groups_segments_like_classic_folder_vos() -> None:
    doc = EnhancedScriptDocument(
        segments=[
            ScriptSegment(
                segment_id="a1",
                text="Kapitel A Satz eins.",
                sequence_index=1,
                folder_name="Canyon",
                folder_order_index=0,
            ),
            ScriptSegment(
                segment_id="b1",
                text="Kapitel B.",
                sequence_index=2,
                folder_name="Desert",
                folder_order_index=1,
            ),
            ScriptSegment(
                segment_id="a2",
                text="Kapitel A Satz zwei.",
                sequence_index=3,
                folder_name="Canyon",
                folder_order_index=0,
            ),
        ]
    )
    assert chapter_narration_text(doc, "Canyon") == (
        "Kapitel A Satz eins. Kapitel A Satz zwei."
    )
    groups = group_segments_by_folder(doc, folder_order=["Canyon", "Desert"])
    assert [name for name, _ in groups] == ["Canyon", "Desert"]
    assert len(groups[0][1]) == 2


def test_update_folder_chapter_narration_collapses_to_one_script(tmp_path: Path) -> None:
    project = _project(tmp_path, folders=["Canyon", "Desert"])
    draft = EnhancedScriptDocument(
        narration_full="A1 A2 B1",
        segments=[
            ScriptSegment(
                segment_id="c_a1",
                text="A1",
                sequence_index=1,
                folder_name="Canyon",
                folder_order_index=0,
                visual_intent_ids=["ia"],
            ),
            ScriptSegment(
                segment_id="c_a2",
                text="A2",
                sequence_index=2,
                folder_name="Canyon",
                folder_order_index=0,
            ),
            ScriptSegment(
                segment_id="d_b1",
                text="B1",
                sequence_index=3,
                folder_name="Desert",
                folder_order_index=1,
            ),
        ],
    )
    save_script_draft(project, draft)
    lock_script(project)

    updated = update_folder_chapter_narration(
        project, "Canyon", "Neuer Canyon-Text über die Schlucht."
    )
    assert not script_locked_path(project).is_file()
    canyon = [s for s in updated.segments if s.folder_name == "Canyon"]
    desert = [s for s in updated.segments if s.folder_name == "Desert"]
    assert len(canyon) == 1
    assert canyon[0].text == "Neuer Canyon-Text über die Schlucht."
    assert canyon[0].visual_intent_ids == ["ia"]
    assert len(desert) == 1
    assert desert[0].text == "B1"
    assert chapter_narration_text(updated, "Canyon").startswith("Neuer Canyon")


def test_revision_prompt_contains_only_instructions_and_script() -> None:
    prompt = build_enhanced_script_revision_prompt(
        editor_instructions="Make it calmer.",
        current_script="The canyon glows red at dusk.",
        folder_name="Antelope Canyon",
        language="en",
    )
    assert "Make it calmer." in prompt
    assert "The canyon glows red at dusk." in prompt
    assert "Antelope Canyon" in prompt
    assert "Project Brief" not in prompt
    assert "DRAMATURGY" not in prompt
    assert "inventory" not in prompt.lower()
    assert "Style Profile" not in prompt


def test_revise_enhanced_script_for_folder_uses_only_freetext_and_script(
    tmp_path: Path,
) -> None:
    folders = ["Canyon"]
    project = _project(tmp_path, folders=folders)
    _confirm_dramaturgy(project, folders)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            segments=[
                ScriptSegment(
                    segment_id="c1",
                    text="Old canyon narration.",
                    sequence_index=1,
                    folder_name="Canyon",
                    folder_order_index=0,
                )
            ]
        ),
    )

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        assert "Old canyon narration." in prompt
        assert "shorter please" in prompt
        assert "Project Brief" not in prompt
        return "Shorter canyon narration."

    result = revise_enhanced_script_for_folder(
        project,
        "Canyon",
        editor_instructions="shorter please",
        llm_callable=fake_llm,
    )
    assert result.status == "PASS"
    draft = load_script_draft(project)
    assert draft is not None
    assert chapter_narration_text(draft, "Canyon") == "Shorter canyon narration."


def test_folder_script_prompt_binds_to_dramaturgy_chapter() -> None:
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text="core_promise: promise",
        chapter_dramaturgy_text="folder_name: Canyon\ndramaturgy_role: hook",
        style_profile_text="Style",
        verified_facts_text="Facts",
        asset_inventory_summary="- Canyon: 3 assets",
        folder_name="Canyon",
        folder_slug="canyon",
        dramaturgy_role="hook",
        target_words=150,
        min_words=120,
        max_words=180,
        previous_folder_name=None,
        next_folder_name="Desert",
        language="de",
    )
    assert 'folder_name (EXACT): Canyon' in prompt
    assert "dramaturgy_role: hook" in prompt
    assert "target_words: 150" in prompt
    assert "120-180" in prompt
    assert "next chapter in the film: Desert" in prompt
    assert "THIS CHAPTER DRAMATURGY:" in prompt
    for phrase in FORBIDDEN_PHRASES:
        assert phrase in prompt


def test_merge_folder_script_keeps_other_chapters() -> None:
    base = EnhancedScriptDocument(
        segments=[
            ScriptSegment(
                segment_id="a_001",
                text="old A",
                sequence_index=1,
                folder_name="A",
                folder_order_index=0,
            ),
            ScriptSegment(
                segment_id="b_001",
                text="old B",
                sequence_index=2,
                folder_name="B",
                folder_order_index=1,
            ),
        ],
        visual_intents=[
            VisualIntent(
                intent_id="ia",
                description="A",
                folder_name="A",
            ),
            VisualIntent(
                intent_id="ib",
                description="B",
                folder_name="B",
            ),
        ],
    )
    incoming = EnhancedScriptDocument(
        segments=[
            ScriptSegment(
                segment_id="a_new",
                text="new A",
                sequence_index=1,
                folder_name="A",
                folder_order_index=0,
            )
        ],
        visual_intents=[
            VisualIntent(intent_id="ia_new", description="new A", folder_name="A")
        ],
    )
    merged = merge_folder_script_into_document(
        base,
        incoming,
        folder_name="A",
        folder_order_index=0,
        folder_order=["A", "B"],
    )
    assert [seg.text for seg in merged.segments] == ["new A", "old B"]
    assert [intent.intent_id for intent in merged.visual_intents] == ["ib", "ia_new"]
    assert folders_present_in_script(merged) == {"A", "B"}


def test_generate_scripts_follow_dramaturgy_order_sequentially(tmp_path: Path) -> None:
    folders = ["Canyon", "Desert", "Coast"]
    project = _project(tmp_path, folders=folders)
    _confirm_dramaturgy(project, folders)

    enabled = list_enabled_dramaturgy_folders(project)
    assert [entry.folder_name for entry in enabled] == folders

    seen: list[str] = []
    token_caps: list[int | None] = []

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        assert "openai:gpt-5.4-mini" in model or model.endswith("gpt-5.4-mini")
        token_caps.append(max_output_tokens)
        for folder in folders:
            marker = f'folder_name (EXACT): {folder}'
            if marker in prompt:
                seen.append(folder)
                assert f"dramaturgy_role:" in prompt
                return _fake_folder_llm_response(folder)
        raise AssertionError(f"unexpected prompt without chapter marker:\n{prompt[:400]}")

    results = generate_all_enhanced_scripts(
        project,
        provider="openai",
        model="gpt-5.4-mini",
        max_output_tokens=50_000,
        llm_callable=fake_llm,
    )
    assert seen == folders
    assert all(cap == 50_000 for cap in token_caps)
    assert all(result.status == "PASS" for result in results)

    draft = load_script_draft(project)
    assert draft is not None
    assert [seg.folder_name for seg in draft.segments] == folders
    assert folders_present_in_script(draft) == set(folders)


def test_generate_single_folder_merges_into_existing_draft(tmp_path: Path) -> None:
    folders = ["Canyon", "Desert"]
    project = _project(tmp_path, folders=folders)
    _confirm_dramaturgy(project, folders)

    def fake_llm(*, prompt: str, model: str, max_output_tokens: int | None = None) -> str:
        del model, max_output_tokens
        if "Canyon" in prompt and 'folder_name (EXACT): Canyon' in prompt:
            return _fake_folder_llm_response("Canyon")
        if "Desert" in prompt and 'folder_name (EXACT): Desert' in prompt:
            return _fake_folder_llm_response("Desert")
        raise AssertionError("unexpected folder prompt")

    first = generate_enhanced_script_for_folder(
        project, "Canyon", llm_callable=fake_llm, max_output_tokens=20_000
    )
    assert first.status == "PASS"
    assert folders_present_in_script(first.document) == {"Canyon"}

    second = generate_enhanced_script_for_folder(
        project, "Desert", llm_callable=fake_llm, max_output_tokens=20_000
    )
    assert second.status == "PASS"
    assert [seg.folder_name for seg in second.document.segments] == ["Canyon", "Desert"]

    # Regenerating Canyon must keep Desert
    regen = generate_enhanced_script_for_folder(
        project, "Canyon", llm_callable=fake_llm, max_output_tokens=20_000
    )
    assert regen.status == "PASS"
    assert [seg.folder_name for seg in regen.document.segments] == ["Canyon", "Desert"]
    assert regen.document.segments[0].text == "Narration für Canyon."
