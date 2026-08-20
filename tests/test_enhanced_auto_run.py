"""Sprach-Standard-Katalog und sequenzieller Enhanced-Auto-Lauf."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.defaults import (
    CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
    DEFAULT_ENHANCED_WORK_SUBDIR,
    DRAMATURGY_DEFAULTS_FILENAME,
    ELEVENLABS_VOICE_DEFAULTS_FILENAME,
    INTRO_HOOK_DEFAULTS_FILENAME,
    PROJECT_BRIEF_DEFAULTS_FILENAME,
    STYLE_REFERENCE_DEFAULTS_FILENAME,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    DramaturgyBuildResult,
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    IntroHookBuildResult,
    save_confirmed_intro_hook,
    save_intro_hook_candidates,
)
from otio_app.services.voiceover_generation.language_defaults_catalog import (
    get_language_standard,
    list_language_standard_files,
    list_shared_library_files,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_FAIL, STATUS_PASS
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    IntroHookCandidate,
    IntroHookCandidatesDocument,
    ProjectBrief,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.project_brief_service import save_project_brief
from otio_app.services.voiceover_generation.style_reference_service import (
    save_style_references,
)
from otio_app.services.voiceover_generation.video_title_service import (
    VideoTitleGenerateResult,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
    JobStatus,
    get_enhanced_auto_run_job_manager,
)
from otio_app.services.without_voiceover_enhanced import enhanced_auto_run_service as auto_run
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AutoRunProgress,
    EnhancedAutoRunCancelled,
    EnhancedAutoRunError,
    format_auto_run_failure_message,
    item_counts_from_gap_message,
    pick_auto_intro_candidate,
    run_enhanced_auto_pipeline,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    FolderScriptBuildResult,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    load_script_draft,
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS,
)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    root = tmp_path / "Greece"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder_names = folders or ["Athens", "Győr"]
    for folder in folder_names:
        (root / folder).mkdir(exist_ok=True)
    return Project(
        id="pt-greece-auto",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="pt",
        video_place="Griechenland",
        asset_subdir_names=folder_names,
        selected_asset_subdirs=folder_names,
    )


def _seed_brief(project: Project, *, title: str = "", refs: list[str] | None = None) -> None:
    save_project_brief(
        project,
        ProjectBrief(
            project_id=project.id,
            language="PT",
            video_title=title,
            title_references=refs or ["Ref eins", "Ref zwei"],
        ),
    )


def _seed_raw_style(project: Project) -> None:
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode="raw_text",
            raw_reference_text="Ein langer Beispieltext für den Kapitelstil.",
            raw_intro_reference_text="Intro-Beispieltext.",
        ),
    )


def _seed_dramaturgy(project: Project, folders: list[str]) -> DramaturgyPlan:
    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Film",
        core_promise="Versprechen",
        narrative_arc="Bogen",
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


def _seed_scripts(project: Project, folders: list[str]) -> None:
    segments = [
        ScriptSegment(
            segment_id=f"{folder}_segment_001",
            text=f"Narration für {folder}.",
            sequence_index=index + 1,
            folder_name=folder,
            folder_order_index=index,
        )
        for index, folder in enumerate(folders)
    ]
    save_script_draft(
        project,
        EnhancedScriptDocument(
            script_status="draft",
            narration_full=" ".join(seg.text for seg in segments),
            segments=segments,
        ),
    )


def _seed_scripts_and_lock(project: Project, folders: list[str]) -> None:
    _seed_scripts(project, folders)
    lock_script(project)


def _stub_auto_run_tail(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enter=None,
    skip_all: bool = False,
    open_gaps_before: list[str] | None = None,
    open_gaps_after: list[str] | None = None,
    chapter_names: list[str] | None = None,
) -> dict[str, bool]:
    """Stock/Funnel/Timing/Music/OTIO: skip-done oder mitzählen."""
    funnel_ran = {"v": False}
    timing_done = {"intro": False, "chapters": []}
    seen_providers: dict[str, bool] = {}
    names = list(chapter_names or [])

    def _enter(label: str) -> None:
        if enter is not None:
            enter(label)

    def fake_open_ids(_project) -> list[str]:
        if funnel_ran["v"]:
            return list(open_gaps_after or [])
        if skip_all:
            return []
        return list(open_gaps_before if open_gaps_before is not None else ["gap-1"])

    def fake_save(_project, config) -> None:
        seen_providers.clear()
        seen_providers.update(config)
        _enter("providers")

    def boom(*_args, **_kwargs):
        raise AssertionError("Tail-Schritt darf bei skip-done nicht laufen")

    def fake_maps(*_args, **kwargs):
        _enter("maps")
        emit = kwargs.get("emit")
        finish = kwargs.get("finish")
        if skip_all:
            if emit is not None:
                emit("maps", "Karten übersprungen.", skipped=True)
            if finish is not None:
                finish("maps", skipped=True)
            return
        if emit is not None:
            emit("maps", "Karten fertig.")
        if finish is not None:
            finish("maps", skipped=False)

    monkeypatch.setattr(auto_run, "list_open_funnel_gap_ids", fake_open_ids)
    monkeypatch.setattr(auto_run, "save_stock_providers_config", fake_save)
    monkeypatch.setattr(auto_run, "_run_maps", fake_maps)

    if skip_all:
        monkeypatch.setattr(auto_run, "search_supplements_for_gaps", boom)
        monkeypatch.setattr(auto_run, "run_supplement_funnel_for_gaps", boom)
        monkeypatch.setattr(auto_run, "resolve_intro_timeline", boom)
        monkeypatch.setattr(auto_run, "resolve_all_chapter_timelines", boom)
        monkeypatch.setattr(auto_run, "intro_timing_complete", lambda _p: True)
        monkeypatch.setattr(auto_run, "list_chapters_needing_python_timing", lambda _p: [])
        monkeypatch.setattr(
            auto_run, "list_chapters_ready_for_python_timing", lambda _p: []
        )
        monkeypatch.setattr(
            auto_run,
            "generate_music_for_allowed_targets",
            lambda *_a, **_k: {
                "generated": [],
                "skipped": [{"label": "intro", "reason": "bereits vorhanden"}],
                "failed": [],
                "stopped": False,
            },
        )
        monkeypatch.setattr(auto_run, "export_all_chapters_otio", boom)
        monkeypatch.setattr(auto_run, "otio_export_complete", lambda _p: True)
        monkeypatch.setattr(auto_run, "youtube_publish_complete", lambda _p: True)
        monkeypatch.setattr(
            auto_run, "generate_youtube_publish_metadata_from_context", boom
        )
        monkeypatch.setattr(auto_run, "load_resolved_timeline_for_auto_run", boom)
        return seen_providers

    def fake_search(*_a, **_k):
        _enter("stock")
        return MagicMock(candidates=["c1"])

    def fake_funnel(*_a, **_k):
        _enter("funnel")
        funnel_ran["v"] = True

    def fake_intro_timing(_p):
        _enter("timing:intro")
        timing_done["intro"] = True

    def fake_chapter_timing(_p, name, **_k):
        _enter(f"timing:{name}")
        timing_done["chapters"].append(name)

    def fake_all_timelines(
        _p,
        *,
        progress_callback=None,
        chapter_names=None,
        only_open=False,
        max_workers=None,
        **_k,
    ):
        selected = list(chapter_names or fake_needing(_p))
        results = []
        total = len(selected)
        for index, name in enumerate(selected, start=1):
            fake_chapter_timing(_p, name)
            if progress_callback is not None:
                progress_callback(name, index, total)
            results.append((name, MagicMock()))
        return results

    def fake_needing(_p):
        return [name for name in names if name not in timing_done["chapters"]]

    def fake_music(*_a, **_k):
        _enter("music")
        return {
            "generated": [{"label": "intro"}],
            "skipped": [],
            "failed": [],
            "stopped": False,
        }

    def fake_otio(*_a, **_k):
        _enter("otio")
        return Path("/tmp/out.otio")

    yt_done = {"v": False}

    def fake_yt_complete(_p) -> bool:
        return yt_done["v"]

    def fake_resolved(_p):
        return MagicMock()

    def fake_yt_context(*_a, **_k):
        return MagicMock(chapters=["c1"], quiz_count=1)

    def fake_yt_meta(*_a, **_k):
        _enter("youtube")
        yt_done["v"] = True
        return MagicMock(status="PASS", document=object(), error=None)

    monkeypatch.setattr(auto_run, "search_supplements_for_gaps", fake_search)
    monkeypatch.setattr(auto_run, "run_supplement_funnel_for_gaps", fake_funnel)
    monkeypatch.setattr(auto_run, "resolve_intro_timeline", fake_intro_timing)
    monkeypatch.setattr(auto_run, "resolve_all_chapter_timelines", fake_all_timelines)
    monkeypatch.setattr(
        auto_run, "intro_timing_complete", lambda _p: timing_done["intro"]
    )
    monkeypatch.setattr(auto_run, "list_chapters_needing_python_timing", fake_needing)
    monkeypatch.setattr(
        auto_run, "list_chapters_ready_for_python_timing", fake_needing
    )
    monkeypatch.setattr(auto_run, "generate_music_for_allowed_targets", fake_music)
    monkeypatch.setattr(auto_run, "export_all_chapters_otio", fake_otio)
    monkeypatch.setattr(auto_run, "otio_export_complete", lambda _p: False)
    monkeypatch.setattr(auto_run, "youtube_publish_complete", fake_yt_complete)
    monkeypatch.setattr(auto_run, "load_resolved_timeline_for_auto_run", fake_resolved)
    monkeypatch.setattr(
        auto_run, "build_youtube_publish_context_from_resolved", fake_yt_context
    )
    monkeypatch.setattr(
        auto_run, "generate_youtube_publish_metadata_from_context", fake_yt_meta
    )
    return seen_providers


def test_language_standard_files_live_under_data_dir() -> None:
    files = list_language_standard_files()
    names = {item.filename for item in files}
    assert names == {
        PROJECT_BRIEF_DEFAULTS_FILENAME,
        STYLE_REFERENCE_DEFAULTS_FILENAME,
        DRAMATURGY_DEFAULTS_FILENAME,
        INTRO_HOOK_DEFAULTS_FILENAME,
        ELEVENLABS_VOICE_DEFAULTS_FILENAME,
        CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
    }
    for item in files:
        assert item.path.name == item.filename
        assert item.path.parent.name == "data"
        assert item.per_language is True
        assert item.stores
        assert item.not_stored
    assert get_language_standard("intro").filename == INTRO_HOOK_DEFAULTS_FILENAME
    shared = {item.filename for item in list_shared_library_files()}
    assert "raw_style_library.json" in shared
    assert "style_profile_library.json" in shared


def test_pick_auto_intro_prefers_first_without_risks() -> None:
    document = IntroHookCandidatesDocument(
        project_id="p",
        language="PT",
        candidates=[
            IntroHookCandidate(hook_id="a", hook_text="risk", risks=["WORD_COUNT"]),
            IntroHookCandidate(hook_id="b", hook_text="clean", risks=[]),
            IntroHookCandidate(hook_id="c", hook_text="also", risks=[]),
        ],
    )
    picked = pick_auto_intro_candidate(document)
    assert picked.hook_id == "b"


def test_pick_auto_intro_falls_back_to_first() -> None:
    document = IntroHookCandidatesDocument(
        project_id="p",
        candidates=[
            IntroHookCandidate(hook_id="only", hook_text="x", risks=["r1"]),
        ],
    )
    assert pick_auto_intro_candidate(document).hook_id == "only"


def test_auto_run_skips_completed_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folders = list(project.selected_asset_subdirs)
    _seed_brief(project, title="Schon da")
    _seed_raw_style(project)
    _seed_dramaturgy(project, folders)
    _seed_scripts_and_lock(project, folders)
    save_confirmed_intro_hook(
        project,
        ConfirmedIntroHook(
            project_id=project.id,
            language="PT",
            hook_id="h1",
            hook_text="Intro-Text ohne Ziffern.",
        ),
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("LLM/TTS darf bei skip-done nicht laufen")

    monkeypatch.setattr(auto_run, "generate_video_title", boom)
    monkeypatch.setattr(auto_run, "build_dramaturgy_plan", boom)
    monkeypatch.setattr(auto_run, "generate_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "revise_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", boom)
    monkeypatch.setattr(auto_run, "synthesize_open_chapters_audio", boom)
    monkeypatch.setattr(auto_run, "generate_intro_unified_cut", boom)
    monkeypatch.setattr(auto_run, "generate_chapter_unified_cut", boom)
    monkeypatch.setattr(
        auto_run,
        "list_chapter_audio_statuses",
        lambda _p: [MagicMock(is_open=False)],
    )
    monkeypatch.setattr(auto_run, "list_chapters_needing_unified_cut", lambda _p: [])

    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )

    write_json(
        intro_unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="script-v1",
            boundaries=[
                CutBoundary(
                    cut_id="intro_cut_000",
                    sentence_id="s1",
                    position="start",
                ),
                CutBoundary(
                    cut_id="intro_cut_001",
                    sentence_id="s1",
                    position="end",
                ),
            ],
            slots=[
                CutSlot(
                    slot_id="intro_slot_001",
                    local_asset_id="a1",
                    asset_fit="strong",
                    asset_fit_reason="test",
                    visual_intent="open",
                )
            ],
        ),
    )

    _stub_auto_run_tail(monkeypatch, skip_all=True)

    report = run_enhanced_auto_pipeline(project, skip_done=True)
    assert report.stopped is False
    assert report.error is None
    assert "brief" in report.skipped
    assert "scripts" in report.skipped
    assert "script_revise" in report.skipped
    assert "script_lock" in report.skipped
    assert "intro" in report.skipped
    assert "tts" in report.skipped
    assert "intro_cut" in report.skipped
    assert "chapter_cuts" in report.skipped
    assert "stock" in report.skipped
    assert "funnel" in report.skipped
    assert "maps" in report.skipped
    assert "timing" in report.skipped
    assert "music" in report.skipped
    assert "otio" in report.skipped
    assert "youtube" in report.skipped


def test_auto_run_stop_after_funnel_skips_timing_and_youtube(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folders = list(project.selected_asset_subdirs)
    _seed_brief(project, title="Schon da")
    _seed_raw_style(project)
    _seed_dramaturgy(project, folders)
    _seed_scripts_and_lock(project, folders)
    save_confirmed_intro_hook(
        project,
        ConfirmedIntroHook(
            project_id=project.id,
            language="PT",
            hook_id="h1",
            hook_text="Intro-Text ohne Ziffern.",
        ),
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("LLM/TTS darf bei skip-done nicht laufen")

    monkeypatch.setattr(auto_run, "generate_video_title", boom)
    monkeypatch.setattr(auto_run, "build_dramaturgy_plan", boom)
    monkeypatch.setattr(auto_run, "generate_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "revise_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", boom)
    monkeypatch.setattr(auto_run, "synthesize_open_chapters_audio", boom)
    monkeypatch.setattr(auto_run, "generate_intro_unified_cut", boom)
    monkeypatch.setattr(auto_run, "generate_chapter_unified_cut", boom)
    monkeypatch.setattr(
        auto_run,
        "list_chapter_audio_statuses",
        lambda _p: [MagicMock(is_open=False)],
    )
    monkeypatch.setattr(auto_run, "list_chapters_needing_unified_cut", lambda _p: [])

    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )

    write_json(
        intro_unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="script-v1",
            boundaries=[
                CutBoundary(
                    cut_id="intro_cut_000",
                    sentence_id="s1",
                    position="start",
                ),
                CutBoundary(
                    cut_id="intro_cut_001",
                    sentence_id="s1",
                    position="end",
                ),
            ],
            slots=[
                CutSlot(
                    slot_id="intro_slot_001",
                    local_asset_id="a1",
                    asset_fit="strong",
                    asset_fit_reason="test",
                    visual_intent="open",
                )
            ],
        ),
    )
    _stub_auto_run_tail(monkeypatch, skip_all=True)

    def too_far(*_args, **_kwargs):
        raise AssertionError("nach Funnel darf nichts mehr laufen")

    monkeypatch.setattr(auto_run, "_run_maps", too_far)
    monkeypatch.setattr(auto_run, "_run_timing", too_far)
    monkeypatch.setattr(auto_run, "_run_music", too_far)
    monkeypatch.setattr(auto_run, "_run_otio", too_far)
    monkeypatch.setattr(auto_run, "_run_youtube", too_far)
    report = run_enhanced_auto_pipeline(
        project, skip_done=True, stop_after="funnel"
    )
    assert report.stopped is False
    assert "funnel" in report.skipped
    assert "maps" not in report.skipped
    assert "maps" not in report.completed
    assert "timing" not in report.skipped
    assert "youtube" not in report.skipped
    assert "youtube" not in report.completed


def test_auto_run_is_strictly_sequential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folders = list(project.selected_asset_subdirs)
    _seed_brief(project, title="")
    _seed_raw_style(project)

    order: list[str] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _enter(label: str) -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            order.append(label)
        time.sleep(0.01)
        with lock:
            active -= 1

    def fake_title(*_a, **_k):
        _enter("title")
        return VideoTitleGenerateResult(status=STATUS_PASS, title="Auto Titel")

    def fake_dram(*_a, **_k):
        _enter("dramaturgy")
        plan = DramaturgyPlan(
            project_id=project.id,
            project_title="Film",
            core_promise="P",
            narrative_arc="A",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name=folder,
                    order_index=index,
                    enabled=True,
                    dramaturgy_role="hook" if index == 0 else "development",
                    reason="r",
                    recommended_word_count=150,
                    recommended_min_words=120,
                    recommended_max_words=180,
                )
                for index, folder in enumerate(folders)
            ],
        )
        return DramaturgyBuildResult(
            status=STATUS_PASS,
            plan=plan,
            error=None,
            llm_run_id="run",
            provider="openai",
            model="test",
        )

    def fake_script(project_arg, folder_name, **_k):
        _enter(f"script:{folder_name}")
        draft = load_script_draft(project_arg)
        segments = list(draft.segments) if draft is not None else []
        segments.append(
            ScriptSegment(
                segment_id=f"{folder_name}_segment_001",
                text=f"Narration für {folder_name}.",
                sequence_index=len(segments) + 1,
                folder_name=folder_name,
                folder_order_index=len(segments),
            )
        )
        save_script_draft(
            project_arg,
            EnhancedScriptDocument(
                script_status="draft",
                narration_full=" ".join(seg.text for seg in segments),
                segments=segments,
            ),
        )
        return FolderScriptBuildResult(
            folder_name=folder_name, status="PASS", segment_count=1
        )

    def fake_revise(_project_arg, folder_name, *, editor_instructions, **_k):
        _enter(f"revise:{folder_name}")
        assert editor_instructions == DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS.strip()
        return FolderScriptBuildResult(
            folder_name=folder_name, status="PASS", segment_count=1
        )

    def fake_intro(project_arg, **_k):
        _enter("intro")
        document = IntroHookCandidatesDocument(
            project_id=project_arg.id,
            language="PT",
            candidates=[
                IntroHookCandidate(
                    hook_id="risky", hook_text="alt", risks=["WEAK"]
                ),
                IntroHookCandidate(
                    hook_id="clean", hook_text="Gutes Intro.", risks=[]
                ),
            ],
        )
        save_intro_hook_candidates(project_arg, document)
        return IntroHookBuildResult(
            status=STATUS_PASS,
            document=document,
            error=None,
            llm_run_id="i",
            provider="openai",
            model="test",
        )

    def fake_tts(*_a, **_k):
        _enter("tts")
        return MagicMock()

    def fake_intro_cut(*_a, **_k):
        _enter("intro_cut")
        return MagicMock(slot_count=2, gap_count=0)

    def fake_chapter_cut(_project, folder_name, **_k):
        _enter(f"cut:{folder_name}")
        return MagicMock()

    monkeypatch.setattr(auto_run, "generate_video_title", fake_title)
    monkeypatch.setattr(auto_run, "build_dramaturgy_plan", fake_dram)
    monkeypatch.setattr(auto_run, "generate_enhanced_script_for_folder", fake_script)
    monkeypatch.setattr(auto_run, "revise_enhanced_script_for_folder", fake_revise)
    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", fake_intro)
    monkeypatch.setattr(auto_run, "synthesize_open_chapters_audio", fake_tts)
    monkeypatch.setattr(auto_run, "list_chapter_audio_statuses", lambda _p: [])
    monkeypatch.setattr(auto_run, "generate_intro_unified_cut", fake_intro_cut)
    monkeypatch.setattr(auto_run, "generate_chapter_unified_cut", fake_chapter_cut)
    monkeypatch.setattr(
        auto_run, "list_chapters_needing_unified_cut", lambda _p: list(folders)
    )
    monkeypatch.setattr(auto_run, "refresh_merged_unified_cut_plan", lambda _p: None)
    seen_providers = _stub_auto_run_tail(
        monkeypatch, enter=_enter, chapter_names=list(folders)
    )

    report = run_enhanced_auto_pipeline(project, skip_done=True)
    assert report.stopped is False
    assert max_active == 1
    assert order[0] == "title"
    assert order[1] == "dramaturgy"
    assert order[2].startswith("script:")
    assert order[3].startswith("script:")
    assert order[2] != order[3]
    assert order[4].startswith("revise:")
    assert order[5].startswith("revise:")
    assert [item for item in order if item.startswith("revise:")] == [
        f"revise:{folders[0]}",
        f"revise:{folders[1]}",
    ]
    assert "intro" in order
    assert "tts" in order
    assert "intro_cut" in order
    assert [item for item in order if item.startswith("cut:")] == [
        f"cut:{folders[0]}",
        f"cut:{folders[1]}",
    ]
    assert seen_providers["pexels"] is False
    assert seen_providers["pixabay"] is False
    assert seen_providers["wikimedia"] is True
    assert seen_providers["openverse"] is True
    assert seen_providers["archive_org"] is True
    assert "stock" in order
    assert "funnel" in order
    assert "maps" in order
    assert order.index("funnel") < order.index("maps") < order.index("timing:intro")
    assert "timing:intro" in order
    assert [item for item in order if item.startswith("timing:") and item != "timing:intro"] == [
        f"timing:{folders[0]}",
        f"timing:{folders[1]}",
    ]
    assert "music" in order
    assert "otio" in order
    assert "youtube" in order
    locked = load_locked_script(project)
    assert locked is not None
    from otio_app.services.voiceover_generation.intro_hook_service import (
        load_confirmed_intro_hook,
    )

    confirmed = load_confirmed_intro_hook(project)
    assert confirmed is not None
    assert confirmed.hook_id == "clean"


def test_auto_run_python_timing_runs_chapters_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.defaults import ENHANCED_CHAPTER_TIMING_MAX_WORKERS
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
        _run_timing,
    )

    project = _project(tmp_path)
    folders = [f"Place {index}" for index in range(5)]
    current = 0
    max_seen = 0
    lock = threading.Lock()
    finished: list[str] = []

    def fake_resolve(_project, name):
        nonlocal current, max_seen
        with lock:
            current += 1
            max_seen = max(max_seen, current)
        time.sleep(0.12)
        with lock:
            current -= 1
            finished.append(name)
        return MagicMock()

    needing_calls = {"n": 0}

    def fake_needing(_p):
        needing_calls["n"] += 1
        if needing_calls["n"] == 1:
            return list(folders)
        return []

    monkeypatch.setattr(auto_run, "intro_timing_complete", lambda _p: True)
    monkeypatch.setattr(auto_run, "list_chapters_needing_python_timing", fake_needing)
    monkeypatch.setattr(
        auto_run, "list_chapters_ready_for_python_timing", lambda _p: list(folders)
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.resolve_chapter_timeline",
        fake_resolve,
    )

    _run_timing(
        project,
        skip_done=True,
        emit=lambda *_a, **_k: None,
        checkpoint=lambda _step: None,
        finish=lambda *_a, **_k: None,
    )
    assert set(finished) == set(folders)
    assert max_seen > 1
    assert max_seen <= ENHANCED_CHAPTER_TIMING_MAX_WORKERS
    assert max_seen <= len(folders)


def test_auto_run_revises_existing_scripts_before_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folders = list(project.selected_asset_subdirs)
    _seed_brief(project, title="Schon da")
    _seed_raw_style(project)
    _seed_dramaturgy(project, folders)
    _seed_scripts(project, folders)

    order: list[str] = []

    def boom(*_a, **_k):
        raise AssertionError("Skripterzeugung darf nicht nochmal laufen")

    def fake_revise(_project, folder_name, *, editor_instructions, **_k):
        order.append(folder_name)
        assert editor_instructions == DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS.strip()
        return FolderScriptBuildResult(
            folder_name=folder_name, status="PASS", segment_count=1
        )

    monkeypatch.setattr(auto_run, "generate_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "revise_enhanced_script_for_folder", fake_revise)
    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", boom)
    monkeypatch.setattr(auto_run, "synthesize_open_chapters_audio", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(auto_run, "list_chapter_audio_statuses", lambda _p: [])
    monkeypatch.setattr(auto_run, "generate_intro_unified_cut", boom)
    monkeypatch.setattr(auto_run, "generate_chapter_unified_cut", boom)
    monkeypatch.setattr(auto_run, "list_chapters_needing_unified_cut", lambda _p: [])
    monkeypatch.setattr(auto_run, "refresh_merged_unified_cut_plan", lambda _p: None)

    def fake_intro(project_arg, **_k):
        document = IntroHookCandidatesDocument(
            project_id=project_arg.id,
            language="PT",
            candidates=[IntroHookCandidate(hook_id="h", hook_text="Intro.", risks=[])],
        )
        save_intro_hook_candidates(project_arg, document)
        return IntroHookBuildResult(
            status=STATUS_PASS,
            document=document,
            error=None,
            llm_run_id="i",
            provider="openai",
            model="test",
        )

    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", fake_intro)
    monkeypatch.setattr(
        auto_run, "list_chapter_audio_statuses", lambda _p: [MagicMock(is_open=False)]
    )
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )

    write_json(
        intro_unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="script-v1",
            boundaries=[
                CutBoundary(cut_id="intro_cut_000", sentence_id="s1", position="start"),
                CutBoundary(cut_id="intro_cut_001", sentence_id="s1", position="end"),
            ],
            slots=[
                CutSlot(
                    slot_id="intro_slot_001",
                    local_asset_id="a1",
                    asset_fit="strong",
                    asset_fit_reason="test",
                    visual_intent="open",
                )
            ],
        ),
    )

    _stub_auto_run_tail(monkeypatch, skip_all=True)

    report = run_enhanced_auto_pipeline(project, skip_done=True)
    assert report.stopped is False
    assert "scripts" in report.skipped
    assert "script_revise" in report.completed
    assert "script_lock" in report.completed
    assert order == folders
    assert load_locked_script(project) is not None


def test_auto_run_errors_when_funnel_leaves_open_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folders = list(project.selected_asset_subdirs)
    _seed_brief(project, title="Schon da")
    _seed_raw_style(project)
    _seed_dramaturgy(project, folders)
    _seed_scripts_and_lock(project, folders)
    save_confirmed_intro_hook(
        project,
        ConfirmedIntroHook(
            project_id=project.id,
            language="PT",
            hook_id="h1",
            hook_text="Intro-Text ohne Ziffern.",
        ),
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("LLM/TTS darf vor dem Funnel nicht laufen")

    monkeypatch.setattr(auto_run, "generate_video_title", boom)
    monkeypatch.setattr(auto_run, "build_dramaturgy_plan", boom)
    monkeypatch.setattr(auto_run, "generate_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "revise_enhanced_script_for_folder", boom)
    monkeypatch.setattr(auto_run, "build_intro_hook_candidates", boom)
    monkeypatch.setattr(auto_run, "synthesize_open_chapters_audio", boom)
    monkeypatch.setattr(auto_run, "generate_intro_unified_cut", boom)
    monkeypatch.setattr(auto_run, "generate_chapter_unified_cut", boom)
    monkeypatch.setattr(
        auto_run,
        "list_chapter_audio_statuses",
        lambda _p: [MagicMock(is_open=False)],
    )
    monkeypatch.setattr(auto_run, "list_chapters_needing_unified_cut", lambda _p: [])
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )

    write_json(
        intro_unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="script-v1",
            boundaries=[
                CutBoundary(cut_id="intro_cut_000", sentence_id="s1", position="start"),
                CutBoundary(cut_id="intro_cut_001", sentence_id="s1", position="end"),
            ],
            slots=[
                CutSlot(
                    slot_id="intro_slot_001",
                    local_asset_id="a1",
                    asset_fit="strong",
                    asset_fit_reason="test",
                    visual_intent="open",
                )
            ],
        ),
    )
    _stub_auto_run_tail(
        monkeypatch,
        open_gaps_before=["gap-a", "gap-b"],
        open_gaps_after=["gap-a"],
    )

    with pytest.raises(EnhancedAutoRunError, match="Coverage Gap") as exc_info:
        run_enhanced_auto_pipeline(project, skip_done=True)
    message = str(exc_info.value)
    assert "gap-a" in message
    assert "Schritt ⑧ Supplement-Funnel" in message


def test_auto_run_stock_providers_are_free_only() -> None:
    assert auto_run.AUTO_RUN_STOCK_PROVIDERS["pexels"] is False
    assert auto_run.AUTO_RUN_STOCK_PROVIDERS["pixabay"] is False
    assert auto_run.AUTO_RUN_STOCK_PROVIDERS["wikimedia"] is True
    assert auto_run.AUTO_RUN_STOCK_PROVIDERS["openverse"] is True
    assert auto_run.AUTO_RUN_STOCK_PROVIDERS["archive_org"] is True


def test_auto_run_steps_include_tail_through_youtube() -> None:
    ids = [step_id for step_id, _label in auto_run.AUTO_RUN_STEPS]
    for step_id in ("stock", "funnel", "maps", "timing", "music", "otio", "youtube"):
        assert step_id in ids
    assert ids.index("funnel") < ids.index("maps") < ids.index("timing")
    assert ids[-1] == "youtube"
    assert ids[-2] == "otio"


def test_auto_run_stop_after_helpers() -> None:
    assert auto_run.normalize_auto_run_stop_after("supplement_funnel") == "funnel"
    assert auto_run.normalize_auto_run_stop_after(None) == "youtube"
    funnel_ids = auto_run.auto_run_steps_through("funnel")
    assert funnel_ids[-1] == "funnel"
    assert "maps" not in funnel_ids
    assert "timing" not in funnel_ids
    assert "youtube" not in funnel_ids
    youtube_ids = auto_run.auto_run_steps_through("youtube")
    assert youtube_ids[-1] == "youtube"
    assert "maps" in youtube_ids


def test_youtube_publish_complete_without_quiz(tmp_path: Path) -> None:
    from otio_app.services.youtube_publish_models import YouTubeMetadataDocument
    from otio_app.services.youtube_publish_service import save_youtube_metadata

    project = _project(tmp_path)
    save_youtube_metadata(
        project,
        YouTubeMetadataDocument(
            project_id=project.id,
            title="Titel ohne Quiz",
            description="Beschreibung reicht für den Auto-Lauf.",
            quizzes=[],
        ),
    )
    assert auto_run.youtube_publish_complete(project) is True


def test_auto_run_youtube_does_not_generate_quiz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from otio_app.services.youtube_publish_models import YouTubeMetadataDocument
    from otio_app.services.youtube_publish_service import save_youtube_metadata
    from otio_app.services import youtube_publish_service as yt_service

    project = _project(tmp_path)
    calls: list[str] = []

    def fake_meta(*_args, **_kwargs):
        calls.append("meta")
        document = YouTubeMetadataDocument(
            project_id=project.id,
            title="Auto-Titel",
            description="Auto-Beschreibung",
            quizzes=[],
        )
        save_youtube_metadata(project, document)
        return MagicMock(status=STATUS_PASS, document=document, error=None)

    def fake_quiz(*_args, **_kwargs):
        calls.append("quiz")
        raise AssertionError("Quiz-Generierung darf im Auto-Lauf nicht laufen")

    monkeypatch.setattr(auto_run, "load_resolved_timeline_for_auto_run", lambda _p: MagicMock())
    monkeypatch.setattr(
        auto_run,
        "build_youtube_publish_context_from_resolved",
        lambda *_a, **_k: MagicMock(chapters=["c1"]),
    )
    monkeypatch.setattr(
        auto_run, "generate_youtube_publish_metadata_from_context", fake_meta
    )
    monkeypatch.setattr(yt_service, "generate_youtube_quizzes_from_context", fake_quiz)
    monkeypatch.setattr(
        auto_run, "generate_youtube_quizzes_from_context", fake_quiz, raising=False
    )

    emitted: list[str] = []

    def emit(step_id: str, message: str, **_kwargs) -> None:
        emitted.append(message)

    auto_run._run_youtube(
        project,
        skip_done=True,
        emit=emit,
        provider="anthropic",
        model="claude",
        finish=lambda *_a, **_k: None,
    )
    assert calls == ["meta"]
    assert auto_run.youtube_publish_complete(project) is True
    assert any("Quiz bleibt manuell" in line for line in emitted)
    service_source = Path(auto_run.__file__).read_text(encoding="utf-8")
    assert "generate_youtube_quizzes_from_context" not in service_source


def test_auto_run_status_overview_covers_every_step(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rows = auto_run.list_auto_run_step_statuses(project)
    assert [row.step_id for row in rows] == [
        step_id for step_id, _label in auto_run.AUTO_RUN_STEPS
    ]
    by_id = {row.step_id: row for row in rows}
    assert by_id["youtube"].short_label == "YouTube"
    assert by_id["otio"].short_label == "OTIO"
    assert by_id["funnel"].short_label == "Funnel"
    assert by_id["maps"].short_label == "Karten"
    assert by_id["youtube"].done is False
    assert by_id["otio"].done is False


def test_stock_funnel_status_waits_for_chapter_cuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alte erfüllte Gaps dürfen Stock/Funnel nicht abhaken, solange Cuts laufen."""
    project = _project(tmp_path)
    monkeypatch.setattr(auto_run, "list_open_funnel_gap_ids", lambda _p: [])
    monkeypatch.setattr(
        auto_run, "list_chapters_needing_unified_cut", lambda _p: ["Lake Hévíz"]
    )
    by_id = {row.step_id: row for row in auto_run.list_auto_run_step_statuses(project)}
    assert by_id["chapter_cuts"].done is False
    assert by_id["stock"].done is False
    assert by_id["funnel"].done is False


def test_format_auto_run_status_caption_is_one_line() -> None:
    rows = [
        auto_run.AutoRunStepStatus("brief", "①", "Brief", True),
        auto_run.AutoRunStepStatus("stock", "⑧", "Stock", False),
    ]
    assert auto_run.format_auto_run_status_caption(rows) == "Brief ✓ · Stock —"


def test_summarize_auto_run_stage_does_not_scan_later_checkers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        auto_run,
        "maps_complete",
        lambda _project: (_ for _ in ()).throw(AssertionError("maps should not run")),
    )
    monkeypatch.setattr(
        auto_run,
        "timing_complete",
        lambda _project: (_ for _ in ()).throw(AssertionError("timing should not run")),
        raising=False,
    )
    monkeypatch.setattr(
        auto_run,
        "_music_targets_complete",
        lambda _project: (_ for _ in ()).throw(AssertionError("music should not run")),
    )
    monkeypatch.setattr(
        auto_run,
        "otio_export_complete",
        lambda _project: (_ for _ in ()).throw(AssertionError("otio should not run")),
    )
    monkeypatch.setattr(
        auto_run,
        "youtube_publish_complete",
        lambda _project: (_ for _ in ()).throw(AssertionError("youtube should not run")),
    )
    summary = auto_run.summarize_auto_run_stage(project)
    assert summary.next_label == "Brief"
    assert summary.funnel_done is False


def test_list_auto_run_step_statuses_full_scan_still_runs_later_checkers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    called = {"maps": 0}
    monkeypatch.setattr(
        auto_run,
        "maps_complete",
        lambda _project: called.__setitem__("maps", called["maps"] + 1) or False,
    )
    rows = auto_run.list_auto_run_step_statuses(project)
    assert called["maps"] == 1
    assert any(row.step_id == "maps" and row.done is False for row in rows)


def test_summarize_auto_run_stage_cache_skips_second_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    calls = {"n": 0}
    real_list = auto_run.list_auto_run_step_statuses

    def counting_list(item, **kwargs):
        calls["n"] += 1
        return real_list(item, **kwargs)

    monkeypatch.setattr(auto_run, "list_auto_run_step_statuses", counting_list)
    first = auto_run.summarize_auto_run_stage(project)
    second = auto_run.summarize_auto_run_stage(project)
    assert first == second
    assert calls["n"] == 1


def test_auto_run_cancel_between_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _seed_brief(project, title="")
    _seed_raw_style(project)
    titled = {"done": False}

    def fake_title(*_a, **_k):
        titled["done"] = True
        return VideoTitleGenerateResult(status=STATUS_PASS, title="Titel")

    monkeypatch.setattr(auto_run, "generate_video_title", fake_title)
    monkeypatch.setattr(
        auto_run,
        "build_dramaturgy_plan",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("zu weit")),
    )

    with pytest.raises(EnhancedAutoRunCancelled):
        run_enhanced_auto_pipeline(
            project,
            should_cancel=lambda: titled["done"],
            skip_done=True,
        )


def test_auto_run_in_flight_llm_cancel_is_stopped_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.services.plan_llm_client import PlanLlmCancelledError

    project = _project(tmp_path)
    _seed_brief(project, title="")
    _seed_raw_style(project)

    def fake_title(*_a, **_k):
        raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.")

    monkeypatch.setattr(auto_run, "generate_video_title", fake_title)

    with pytest.raises(EnhancedAutoRunCancelled):
        run_enhanced_auto_pipeline(project, skip_done=True)


def test_auto_run_job_cancel_closes_llm_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    manager = get_enhanced_auto_run_job_manager()
    aborted = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def fake_pipeline(*_a, **_k):
        started.set()
        release.wait(timeout=2)
        return auto_run.EnhancedAutoRunReport(stopped=True, log_lines=["stop"])

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.get_project_by_id",
        lambda _pid: project,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.run_enhanced_auto_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.abort_registered_llm_http",
        lambda: aborted.__setitem__("n", aborted["n"] + 1),
    )
    assert manager.start(project) is True
    assert started.wait(timeout=2)
    assert manager.request_cancel(project.id) is True
    release.set()
    for _ in range(80):
        state = manager.get_state(project.id)
        if state is not None and state.status != JobStatus.RUNNING:
            break
        time.sleep(0.05)
    state = manager.get_state(project.id)
    assert state is not None
    assert aborted["n"] >= 1
    assert state.status == JobStatus.CANCELLED
    manager.dismiss(project.id)


def test_format_auto_run_failure_message_prefixes_step_and_item() -> None:
    truncated = (
        "Die Antwort wurde nach 16384 von max_tokens=16384 Output-Tokens "
        "abgeschnitten (stop_reason=max_tokens)."
    )
    named = format_auto_run_failure_message(truncated, "③ Dramaturgie")
    assert named.startswith("Schritt ③ Dramaturgie: ")
    assert "max_tokens=16384" in named
    assert format_auto_run_failure_message(named, "③ Dramaturgie", "Naxos") == named
    chapter = format_auto_run_failure_message("boom", "④ Kapitel-Skripte", "Naxos")
    assert chapter == "Schritt ④ Kapitel-Skripte · Naxos: boom"


def test_auto_run_fails_without_title_references(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_project_brief(
        project,
        ProjectBrief(project_id=project.id, language="PT", video_title=""),
    )
    with pytest.raises(EnhancedAutoRunError, match="Titel-Referenzen") as exc_info:
        run_enhanced_auto_pipeline(project)
    assert "Schritt ① Project Brief" in str(exc_info.value)


def test_auto_run_truncation_names_dramaturgy_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _seed_brief(project, title="Titel")
    _seed_raw_style(project)
    truncated = (
        "Die Antwort wurde nach 16384 von max_tokens=16384 Output-Tokens "
        "abgeschnitten (stop_reason=max_tokens). Das Output-Token-Limit "
        "dieses einen LLM-Aufrufs war voll."
    )

    monkeypatch.setattr(
        auto_run,
        "build_dramaturgy_plan",
        lambda *_a, **_k: DramaturgyBuildResult(
            status=STATUS_FAIL,
            plan=None,
            error=truncated,
            llm_run_id="run",
            provider="anthropic",
            model="claude",
        ),
    )

    with pytest.raises(EnhancedAutoRunError, match=r"Schritt ③ Dramaturgie") as exc_info:
        run_enhanced_auto_pipeline(project, skip_done=True)
    message = str(exc_info.value)
    assert truncated in message
    assert "max_tokens=16384" in message


def test_auto_run_job_completes_in_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    manager = get_enhanced_auto_run_job_manager()

    def fake_pipeline(*_a, **_k):
        time.sleep(0.02)
        return auto_run.EnhancedAutoRunReport(completed=["brief"], log_lines=["ok"])

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.get_project_by_id",
        lambda _pid: project,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.run_enhanced_auto_pipeline",
        fake_pipeline,
    )
    assert manager.start(project) is True
    for _ in range(80):
        state = manager.get_state(project.id)
        if state is not None and state.status != JobStatus.RUNNING:
            break
        time.sleep(0.05)
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.COMPLETED
    manager.dismiss(project.id)


def test_auto_run_job_failure_names_step_from_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path).model_copy(update={"id": "pt-greece-auto-fail"})
    manager = get_enhanced_auto_run_job_manager()

    def fake_pipeline(*_a, **kwargs):
        kwargs["on_progress"](
            AutoRunProgress(
                step_id="dramaturgy",
                step_label="③ Dramaturgie",
                message="Dramaturgie wird geplant…",
                step_index=3,
                step_total=10,
            )
        )
        raise RuntimeError(
            "Die Antwort wurde nach 16384 von max_tokens=16384 Output-Tokens "
            "abgeschnitten (stop_reason=max_tokens)."
        )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.get_project_by_id",
        lambda _pid: project,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.run_enhanced_auto_pipeline",
        fake_pipeline,
    )
    assert manager.start(project) is True
    for _ in range(80):
        state = manager.get_state(project.id)
        if state is not None and state.status != JobStatus.RUNNING:
            break
        time.sleep(0.05)
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.FAILED
    assert "③ Dramaturgie" in (state.error or "")
    assert "max_tokens=16384" in (state.error or "")
    manager.dismiss(project.id)


def test_auto_run_ui_exports_page_and_banner() -> None:
    """Regression: page_panel darf banner nicht umbenennen und dann undefiniert aufrufen."""
    from otio_app.ui.without_voiceover_enhanced import auto_run_ui as module

    assert callable(module.render_enhanced_auto_run_banner)
    assert callable(module.render_enhanced_auto_run_page)
    assert callable(module.render_enhanced_auto_run_page_panel)
    assert callable(module.render_enhanced_auto_run_embedded)
    assert callable(module.auto_run_progress_fraction)
    assert callable(module.running_auto_run_detail)
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert 'key_scope="auto_page"' in source
    assert 'key_scope="auto_panel"' in source
    assert "_render_running_auto_run_status" in source
    assert 'key_scope == "auto_page"' in source
    assert "_render_auto_run_status_overview" in source
    assert "Statusübersicht" in source
    assert "format_auto_run_status_caption" in source
    assert "st.metric(item.short_label" not in source
    assert "YouTube Publish" in source
    assert "bis Funnel" in source
    assert "bis YouTube" in source
    assert "Auto-Lauf fehlgeschlagen —" in source
    assert "Quiz bleibt manuell" in source
    assert "Karten (Plan, Koordinaten, Rendern)" in source
    assert "Metadaten + Quiz" not in source
    assert "any_job_running(project.id, reconcile=False)" in source
    routing = Path("otio_app/ui/routing.py").read_text(encoding="utf-8")
    assert "begin_ui_script_run" in routing
    assert "reconcile_all_jobs()" in routing


def test_auto_run_progress_fraction_includes_chapter_item() -> None:
    from otio_app.ui.without_voiceover_enhanced.auto_run_ui import (
        auto_run_progress_fraction,
    )
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
        EnhancedAutoRunJobState,
        JobStatus,
    )

    state = EnhancedAutoRunJobState(
        project_id="p",
        status=JobStatus.RUNNING,
        step_index=4,
        step_total=10,
        item_index=1,
        item_total=18,
    )
    value = auto_run_progress_fraction(state)
    assert value == pytest.approx((3 + 1 / 18) / 10)
    assert value < 0.4


def test_item_counts_from_gap_message() -> None:
    assert item_counts_from_gap_message(
        "Gap 3/27 · Thumbnail-Ranking"
    ) == (3, 27)
    assert item_counts_from_gap_message(
        "Gap 2/27 · Query 3/10: gap-a · „cave“"
    ) == (2, 27)
    assert item_counts_from_gap_message("Stocksuche startet") == (0, 0)


def test_running_auto_run_detail_keeps_message_with_plan_total() -> None:
    from otio_app.ui.without_voiceover_enhanced.auto_run_ui import (
        running_auto_run_detail,
    )
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job import (
        EnhancedAutoRunJobState,
        JobStatus,
    )

    state = EnhancedAutoRunJobState(
        project_id="p",
        status=JobStatus.RUNNING,
        step_label="Karten",
        message="Karte 1/16 von 27: Baradla Cave",
        item_label="Baradla Cave",
        item_index=1,
        item_total=16,
    )
    assert running_auto_run_detail(state) == "Karte 1/16 von 27: Baradla Cave"
    fallback = EnhancedAutoRunJobState(
        project_id="p",
        status=JobStatus.RUNNING,
        step_label="Karten",
        message="",
        item_label="Baradla Cave",
        item_index=1,
        item_total=16,
    )
    assert running_auto_run_detail(fallback) == "Karten: Baradla Cave (1/16)"


def test_stock_and_funnel_forward_intra_gap_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
        FunnelProgressEvent,
    )

    project = _project(tmp_path)
    events: list[dict] = []
    open_ids = {"v": ["gap-a"]}

    def emit(step_id: str, message: str, **kwargs) -> None:
        events.append({"step_id": step_id, "message": message, **kwargs})

    def fake_open(_project) -> list[str]:
        return list(open_ids["v"])

    def fake_search(_project, progress_callback=None, **_kwargs):
        assert progress_callback is not None
        progress_callback(0.4, "Gap 2/27 · Query 3/10: gap-a · „cave“")
        return MagicMock(candidates=["c1"])

    def fake_funnel(_project, progress_callback=None, **_kwargs):
        assert progress_callback is not None
        progress_callback(
            FunnelProgressEvent(
                phase="thumbnails",
                gap_id="gap-a",
                gap_index=3,
                gap_total=27,
                message="Gap 3/27 · Thumbnail-Ranking",
            )
        )
        open_ids["v"] = []
        return MagicMock(stopped=False)

    monkeypatch.setattr(auto_run, "list_open_funnel_gap_ids", fake_open)
    monkeypatch.setattr(auto_run, "save_stock_providers_config", lambda *_a, **_k: None)
    monkeypatch.setattr(auto_run, "search_supplements_for_gaps", fake_search)
    monkeypatch.setattr(auto_run, "run_supplement_funnel_for_gaps", fake_funnel)

    auto_run._run_stock_and_funnel(
        project,
        skip_done=True,
        emit=emit,
        checkpoint=lambda _sid: None,
        cancelled=lambda: False,
        funnel_model="",
        finish=lambda _sid, *, skipped: None,
    )

    stock = next(
        event
        for event in events
        if event["step_id"] == "stock" and event.get("item_total") == 27
    )
    funnel = next(
        event
        for event in events
        if event["step_id"] == "funnel" and event.get("item_total") == 27
    )
    assert stock["item_index"] == 2
    assert "Query 3/10" in stock["message"]
    assert funnel["item_index"] == 3
    assert funnel["item_label"] == "gap-a"
    assert "Thumbnail-Ranking" in funnel["message"]


def test_enhanced_navigation_includes_auto_run_page() -> None:
    from otio_app.ui.navigation import (
        PAGE_AUTO_RUN,
        VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
    )

    assert PAGE_AUTO_RUN == "▶ Auto-Lauf"
    assert PAGE_AUTO_RUN in VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS
    assert PAGE_AUTO_RUN not in VOICEOVER_GEN_NAVIGATION_OPTIONS
