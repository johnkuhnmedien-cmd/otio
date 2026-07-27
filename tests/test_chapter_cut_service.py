"""Per-Kapitel Unified Cut: Pfade, Persistenz, Merge, Status."""

from __future__ import annotations

from unittest.mock import patch

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    ChapterCutError,
    chapter_resolved_matches_plan,
    concatenate_resolved_timelines,
    get_chapter_cut_status,
    list_body_chapter_names,
    load_prior_chapter_plans,
    persist_chapter_unified_plan,
    refresh_merged_unified_cut_plan,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    chapters_cut_dir,
    unified_cut_plan_path,
)


def _slot(slot_id: str, fit: str = "strong", asset: str | None = "a1") -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=asset,
        asset_fit=fit,  # type: ignore[arg-type]
        asset_fit_reason="test",
        visual_intent="valley",
        coverage_gap_id=None if fit == "strong" else f"gap_{slot_id}",
        needed_visual="valley" if fit != "strong" else "",
        search_concepts=["valley"] if fit != "strong" else [],
    )


def _bound(cut_id: str, sentence_id: str, position: str = "start") -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,  # type: ignore[arg-type]
        alignment="sentence_boundary",
    )


def _project(tmp_path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="ChapterCut",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite", "Caddo"],
        selected_asset_subdirs=["Yosemite", "Caddo"],
        fps=25.0,
    )


def _plan(slug: str, slots: int = 2) -> UnifiedCutPlanDocument:
    bounds = [
        _bound(f"{slug}_cut_{i:03d}", f"{slug}_seg__s00{i + 1}", "start" if i == 0 else "end")
        for i in range(slots + 1)
    ]
    # Fix last position to end
    bounds[-1] = bounds[-1].model_copy(update={"position": "end"})
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=bounds,
        slots=[_slot(f"{slug}_slot_{i:03d}") for i in range(1, slots + 1)],
    )


def test_chapter_paths_use_slug(tmp_path) -> None:
    project = _project(tmp_path)
    plan_path = chapter_unified_cut_plan_path(project, "Yosemite Valley")
    assert "chapters" in plan_path.parts
    assert "Yosemite_Valley" in plan_path.parts
    assert plan_path.name == "unified_cut_plan.json"
    assert chapter_resolved_timeline_path(project, "Yosemite Valley").name == (
        "resolved_timeline.json"
    )
    assert chapters_cut_dir(project).name == "chapters"


def test_persist_invalidates_resolved(tmp_path) -> None:
    project = _project(tmp_path)
    folder = "Yosemite"
    resolved_path = chapter_resolved_timeline_path(project, folder)
    write_json(
        resolved_path,
        ResolvedTimelineDocument(
            script_version="v1",
            fps=25.0,
            total_duration_seconds=1.0,
            shots=[
                ResolvedShot(
                    shot_id="s1",
                    asset_id="a1",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=1.0,
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                )
            ],
        ),
    )
    assert resolved_path.is_file()

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.refresh_merged_unified_cut_plan",
        return_value=None,
    ):
        persist_chapter_unified_plan(
            project, folder, _plan("Yosemite"), refresh_merged=True
        )

    assert chapter_unified_cut_plan_path(project, folder).is_file()
    assert not resolved_path.is_file()


def test_chapter_status_matches() -> None:
    plan = _plan("Yo", slots=2)
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="a",
                asset_id="x",
                timeline_start_seconds=0.0,
                timeline_end_seconds=1.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
            ),
            ResolvedShot(
                shot_id="b",
                asset_id="y",
                timeline_start_seconds=1.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
            ),
        ],
    )
    assert chapter_resolved_matches_plan(plan, resolved)
    stale = resolved.model_copy(update={"shots": resolved.shots[:1]})
    assert not chapter_resolved_matches_plan(plan, stale)


def test_concatenate_resolved_timelines_offsets() -> None:
    a = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=10.0,
        shots=[
            ResolvedShot(
                shot_id="intro_1",
                asset_id="i",
                timeline_start_seconds=0.0,
                timeline_end_seconds=10.0,
                source_start_seconds=0.0,
                source_end_seconds=10.0,
            )
        ],
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="intro_seg",
                audio_path="/tmp/a.mp3",
                timeline_start_seconds=4.0,
                timeline_end_seconds=9.0,
            )
        ],
    )
    b = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=5.0,
        shots=[
            ResolvedShot(
                shot_id="yo_1",
                asset_id="y",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
                source_start_seconds=0.0,
                source_end_seconds=5.0,
            )
        ],
    )
    merged = concatenate_resolved_timelines(
        [a, b], script_version="v1", fps=25.0
    )
    assert merged.total_duration_seconds == 15.0
    assert merged.shots[0].timeline_end_seconds == 10.0
    assert merged.shots[1].timeline_start_seconds == 10.0
    assert merged.shots[1].timeline_end_seconds == 15.0
    assert merged.audio_segments[0].timeline_start_seconds == 4.0


def test_load_prior_chapter_plans_order(tmp_path) -> None:
    project = _project(tmp_path)
    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_body_chapter_names",
        return_value=["Yosemite", "Caddo", "Zion"],
    ):
        write_json(
            chapter_unified_cut_plan_path(project, "Yosemite"),
            _plan("Yosemite"),
        )
        write_json(
            chapter_unified_cut_plan_path(project, "Caddo"),
            _plan("Caddo"),
        )
        prior = load_prior_chapter_plans(project, "Zion")
        assert [p.slots[0].slot_id.split("_")[0] for p in prior] == [
            "Yosemite",
            "Caddo",
        ]
        prior_mid = load_prior_chapter_plans(project, "Caddo")
        assert len(prior_mid) == 1
        assert prior_mid[0].slots[0].slot_id.startswith("Yosemite")


def test_get_chapter_cut_status_green(tmp_path) -> None:
    project = _project(tmp_path)
    folder = "Yosemite"
    plan = _plan("Yosemite", slots=1)
    write_json(chapter_unified_cut_plan_path(project, folder), plan)
    write_json(
        chapter_resolved_timeline_path(project, folder),
        ResolvedTimelineDocument(
            script_version="v1",
            fps=25.0,
            total_duration_seconds=1.0,
            shots=[
                ResolvedShot(
                    shot_id="s1",
                    asset_id="a",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=1.0,
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                )
            ],
        ),
    )
    status = get_chapter_cut_status(project, folder)
    assert status.has_plan
    assert status.has_resolved
    assert status.matches
    assert status.plan_slots == 1
    assert status.resolved_shots == 1


def test_refresh_merged_does_not_wipe_other_resolved(tmp_path) -> None:
    project = _project(tmp_path)
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
        ScriptSegment,
    )

    locked = EnhancedScriptDocument(
        script_version="v1",
        script_status="locked",
        segments=[
            ScriptSegment(
                segment_id="yo_1",
                folder_name="Yosemite",
                text="Hello Yosemite.",
                sequence_index=1,
            ),
            ScriptSegment(
                segment_id="ca_1",
                folder_name="Caddo",
                text="Hello Caddo.",
                sequence_index=2,
            ),
        ],
    )
    yo_plan = _plan("Yosemite", slots=1)
    ca_plan = _plan("Caddo", slots=1)
    write_json(chapter_unified_cut_plan_path(project, "Yosemite"), yo_plan)
    write_json(chapter_unified_cut_plan_path(project, "Caddo"), ca_plan)
    yo_resolved = chapter_resolved_timeline_path(project, "Yosemite")
    write_json(
        yo_resolved,
        ResolvedTimelineDocument(
            script_version="v1",
            fps=25.0,
            total_duration_seconds=1.0,
            shots=[
                ResolvedShot(
                    shot_id="s1",
                    asset_id="a",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=1.0,
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                )
            ],
        ),
    )

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_body_chapter_names",
        return_value=["Yosemite", "Caddo"],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.require_locked_script",
        return_value=locked,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service.require_locked_script",
        return_value=locked,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service.load_model",
        return_value=None,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.gap_search_concepts.enrich_coverage_search_concepts",
        side_effect=lambda project, coverage, plan=None: coverage,
    ):
        merged = refresh_merged_unified_cut_plan(project)

    assert merged is not None
    assert len(merged.slots) == 2
    assert yo_resolved.is_file(), "Refresh darf fertiges Timing nicht löschen"
    assert unified_cut_plan_path(project).is_file()


def test_list_body_chapter_names_skips_intro() -> None:
    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_cut_plan_chapter_names",
        return_value=["Intro", "Yosemite", "Caddo"],
    ):
        assert list_body_chapter_names(object()) == ["Yosemite", "Caddo"]  # type: ignore[arg-type]


def test_open_batch_helpers_skip_finished_chapters(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        ChapterCutStatus,
        list_chapters_needing_python_timing,
        list_chapters_needing_unified_cut,
    )

    statuses = [
        ChapterCutStatus(
            folder_name="Done",
            folder_slug="done",
            has_plan=True,
            plan_slots=3,
            has_resolved=True,
            resolved_shots=3,
            matches=True,
        ),
        ChapterCutStatus(
            folder_name="NeedLLM",
            folder_slug="needllm",
            has_plan=False,
        ),
        ChapterCutStatus(
            folder_name="NeedTiming",
            folder_slug="needtiming",
            has_plan=True,
            plan_slots=2,
            has_resolved=False,
            matches=False,
        ),
        ChapterCutStatus(
            folder_name="OpenGaps",
            folder_slug="opengaps",
            has_plan=True,
            plan_slots=2,
            has_resolved=False,
            matches=False,
            open_gap_ids=["OpenGaps_gap_001"],
        ),
    ]
    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_chapter_cut_statuses",
        return_value=statuses,
    ):
        assert list_chapters_needing_unified_cut(object()) == ["NeedLLM"]  # type: ignore[arg-type]
        assert list_chapters_needing_python_timing(object()) == ["NeedTiming"]  # type: ignore[arg-type]
        assert "OpenGaps" not in list_chapters_needing_python_timing(object())  # type: ignore[arg-type]


def test_generate_all_only_open_filters_names(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        ChapterCutGenerateResult,
        generate_all_chapter_unified_cuts,
    )

    project = _project(tmp_path)
    called: list[str] = []

    def fake_generate(project, folder_name, **kwargs):
        called.append(folder_name)
        plan = _plan(folder_name.lower(), slots=1)
        return ChapterCutGenerateResult(
            folder_name=folder_name,
            plan=plan,
            slot_count=1,
            gap_count=0,
        )

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_chapters_needing_unified_cut",
        return_value=["OpenA", "OpenB"],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.generate_chapter_unified_cut",
        side_effect=fake_generate,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.refresh_merged_unified_cut_plan",
        return_value=None,
    ):
        results = generate_all_chapter_unified_cuts(project, only_open=True)

    assert called == ["OpenA", "OpenB"]
    assert len(results) == 2


def test_generate_all_continues_after_one_chapter_fails(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        ChapterCutGenerateResult,
        generate_all_chapter_unified_cuts,
    )

    project = _project(tmp_path)
    called: list[str] = []

    def fake_generate(project, folder_name, **kwargs):
        called.append(folder_name)
        if folder_name == "Bad":
            raise ChapterCutError("Unterminated string starting at: line 1")
        plan = _plan(folder_name.lower(), slots=1)
        return ChapterCutGenerateResult(
            folder_name=folder_name,
            plan=plan,
            slot_count=1,
            gap_count=0,
        )

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_body_chapter_names",
        return_value=["Good", "Bad", "AlsoGood"],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.generate_chapter_unified_cut",
        side_effect=fake_generate,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.refresh_merged_unified_cut_plan",
        return_value=None,
    ):
        try:
            generate_all_chapter_unified_cuts(project, only_open=False)
            raise AssertionError("expected ChapterCutError")
        except ChapterCutError as exc:
            message = str(exc)
            assert "1/3" in message
            assert "2 ok" in message
            assert "Bad:" in message

    assert called == ["Good", "Bad", "AlsoGood"]


def test_resolve_all_timelines_runs_parallel_and_keeps_order(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        resolve_all_chapter_timelines,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        ResolvedTimelineDocument,
    )

    project = _project(tmp_path)
    called: list[str] = []

    def fake_resolve(project, folder_name):
        called.append(folder_name)
        return ResolvedTimelineDocument(
            script_version="v1",
            total_duration_seconds=1.0,
        )

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.list_chapters_ready_for_python_timing",
        return_value=["A", "B", "C"],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.resolve_chapter_timeline",
        side_effect=fake_resolve,
    ):
        results = resolve_all_chapter_timelines(project, max_workers=3)

    assert [name for name, _ in results] == ["A", "B", "C"]
    assert set(called) == {"A", "B", "C"}


def test_resolve_chapter_requires_plan(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        resolve_chapter_timeline,
    )

    project = _project(tmp_path)
    try:
        resolve_chapter_timeline(project, "Yosemite")
        raise AssertionError("expected ChapterCutError")
    except ChapterCutError as exc:
        assert "fehlt" in str(exc).lower() or "Plan" in str(exc)


def test_resolve_chapter_blocked_when_gaps_open(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        resolve_chapter_timeline,
    )

    project = _project(tmp_path)
    plan = _plan("yosemite", slots=2)
    # zweiter Slot mit offener Gap
    slots = list(plan.slots)
    slots[1] = _slot("yosemite_slot_002", fit="none", asset=None)
    plan = plan.model_copy(update={"slots": slots})
    write_json(chapter_unified_cut_plan_path(project, "Yosemite"), plan)

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.chapter_open_gap_ids",
        return_value=["gap_yosemite_slot_002"],
    ):
        try:
            resolve_chapter_timeline(project, "Yosemite")
            raise AssertionError("expected ChapterCutError")
        except ChapterCutError as exc:
            assert "offene Coverage Gap" in str(exc)


def test_resolve_chapter_timeline_merges_export_ready_gaps(tmp_path) -> None:
    """Python Timing muss Gap-Merge laufen — sonst bleiben Manuals als Placeholder."""
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        resolve_chapter_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.models import GapMergeReport

    project = _project(tmp_path)
    plan = _plan("yosemite", slots=1)
    write_json(chapter_unified_cut_plan_path(project, "Yosemite"), plan)

    timed = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=4.0,
        shots=[
            ResolvedShot(
                shot_id="yosemite_slot_001",
                asset_id="",
                timeline_start_seconds=0.0,
                timeline_end_seconds=4.0,
                source_start_seconds=0.0,
                source_end_seconds=4.0,
                editorial_function="evidence",
                asset_fit="none",
                coverage_gap_id="gap_yosemite_slot_001",
                open_gap=True,
                is_placeholder=True,
                folder_name="Yosemite",
            )
        ],
    )
    merged = timed.model_copy(deep=True)
    merged.shots[0] = merged.shots[0].model_copy(
        update={
            "asset_id": "manual_supp_1",
            "open_gap": False,
            "is_placeholder": False,
            "resolved_media_path": "/tmp/manual.jpg",
        }
    )
    merge_calls: list[dict] = []

    def _fake_merge(project, **kwargs):
        merge_calls.append(kwargs)
        return merged, GapMergeReport(script_version="v1", merged_shot_ids=["yosemite_slot_001"])

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.chapter_open_gap_ids",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.unified_timeline_service.resolve_unified_timeline",
        return_value=timed,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.gap_merge_service.merge_export_ready_gaps_into_timeline",
        side_effect=_fake_merge,
    ):
        out = resolve_chapter_timeline(project, "Yosemite")

    assert merge_calls, "Gap-Merge wurde nicht aufgerufen"
    assert merge_calls[0].get("persist") is False
    assert merge_calls[0].get("unified") is plan or merge_calls[0].get("unified") is not None
    assert out.shots[0].asset_id == "manual_supp_1"
    assert out.shots[0].open_gap is False
    assert chapter_resolved_timeline_path(project, "Yosemite").is_file()
