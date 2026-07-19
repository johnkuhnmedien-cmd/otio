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
    parse_final_cut_response,
    parse_rough_cut_response,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGapsDocument,
    EnhancedScriptDocument,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    PauseDirective,
    RoughCutPlanDocument,
    RoughShot,
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
    coverage_gaps_path,
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
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    parse_enhanced_script_response,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    mark_segment_text_changed,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    FORBIDDEN_PHRASES,
    build_enhanced_script_prompt,
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


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Assets").mkdir()
    return Project(
        name="Enhanced Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
        fps=25.0,
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


def test_pause_duration_classes_central_and_deterministic() -> None:
    assert PAUSE_DURATION_SECONDS["short"] == 0.35
    assert PAUSE_DURATION_SECONDS["medium"] == 0.80
    assert PAUSE_DURATION_SECONDS["long"] == 1.50
    assert resolve_pause_duration_seconds("short") == 0.35
    assert resolve_pause_duration_seconds("medium") == 0.80
    assert resolve_pause_duration_seconds("long") == 1.50
    with pytest.raises(ValueError):
        resolve_pause_duration_seconds("huge")


def test_pause_resolver_identical_inputs_identical_outputs_and_pause_without_cut() -> None:
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
                    "narration_start_anchor": {
                        "segment_id": "segment_001",
                        "offset_seconds": 0.0,
                    },
                    "narration_end_anchor": {
                        "segment_id": "segment_002",
                        "offset_seconds": 0.5,
                    },
                    "visual_intent_id": "intent_001",
                    "asset_id": "asset_a",
                    "editorial_function": "orientation",
                    "editorial_reason": "Hold across two statements",
                    "may_overlap_pause": True,
                },
                {
                    "shot_id": "shot_002",
                    "narration_start_anchor": {
                        "segment_id": "segment_002",
                        "offset_seconds": 0.5,
                    },
                    "narration_end_anchor": {
                        "segment_id": "segment_002",
                        "offset_seconds": 2.0,
                    },
                    "visual_intent_id": "intent_002",
                    "asset_id": None,
                    "editorial_function": "detail",
                    "editorial_reason": "Detail inside same segment",
                },
            ],
            "coverage_gaps": [],
        },
        "script-v1",
    )
    assert len(rough.shots) == 2
    assert rough.shots[0].narration_start_anchor.segment_id != rough.shots[0].narration_end_anchor.segment_id
    assert rough.shots[1].narration_start_anchor.segment_id == "segment_002"
    assert any(g.related_shot_ids == ["shot_002"] for g in coverage.gaps)
    assert all("frame" not in d.model_dump_json() for d in rough.pause_directives)


def test_stock_providers_registered_and_unavailable_does_not_stop_others() -> None:
    names = [p.provider_name for p in get_stock_providers()]
    assert set(REQUIRED_PROVIDER_NAMES).issubset(set(names))

    class UnavailableProvider(MockStockProvider):
        provider_name = "mock_unavailable"

    class ReadyProvider(MockStockProvider):
        provider_name = "mock_ready"

    unavailable = UnavailableProvider(available=False)
    ready = ReadyProvider(available=True)
    candidates, status = search_all_providers(
        "Monument Valley",
        providers=[unavailable, ready],
    )
    assert status["mock_unavailable"].startswith("unavailable")
    assert status["mock_ready"].startswith("ok:")
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
                    "preview_url": str(wav1),
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
    project = _project(tmp_path)
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
    write_json(
        accepted_supplements_path(project),
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": "script-v1",
            "supplements": [
                {
                    "candidate_id": "asset_local_1",
                    "provider": "mock",
                    "preview_url": str(wav),
                    "source_page": str(wav),
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


def test_coverage_gap_has_search_queries_and_links() -> None:
    _rough, coverage = parse_rough_cut_response(
        {
            "pause_directives": [],
            "shots": [
                {
                    "shot_id": "shot_007",
                    "narration_start_anchor": {
                        "segment_id": "segment_003",
                        "offset_seconds": 1.2,
                    },
                    "narration_end_anchor": {
                        "segment_id": "segment_005",
                        "offset_seconds": 0.4,
                    },
                    "visual_intent_id": "intent_004",
                    "asset_id": None,
                    "editorial_function": "orientation",
                    "editorial_reason": "missing local",
                }
            ],
            "coverage_gaps": [
                {
                    "gap_id": "gap_008",
                    "related_shot_ids": ["shot_007"],
                    "visual_intent_id": "intent_004",
                    "subject": "Monument Valley",
                    "location": "Utah/Arizona",
                    "action": "wide establishing shot",
                    "search_queries": [
                        "Monument Valley wide landscape",
                        "Monument Valley sunset panorama",
                    ],
                    "reason": "Kein geografisch passendes lokales Asset vorhanden.",
                }
            ],
        },
        "script-v1",
    )
    gap = coverage.gaps[0]
    assert gap.related_shot_ids == ["shot_007"]
    assert gap.visual_intent_id == "intent_004"
    assert "Monument Valley wide landscape" in gap.search_queries
