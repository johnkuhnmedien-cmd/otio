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

    monkeypatch.setattr(auto_run, "list_open_funnel_gap_ids", fake_open_ids)
    monkeypatch.setattr(auto_run, "save_stock_providers_config", fake_save)

    if skip_all:
        monkeypatch.setattr(auto_run, "search_supplements_for_gaps", boom)
        monkeypatch.setattr(auto_run, "run_supplement_funnel_for_gaps", boom)
        monkeypatch.setattr(auto_run, "resolve_intro_timeline", boom)
        monkeypatch.setattr(auto_run, "resolve_chapter_timeline", boom)
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
        monkeypatch.setattr(auto_run, "generate_youtube_quizzes_from_context", boom)
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
        return MagicMock(status="PASS", document=object(), error=None)

    def fake_yt_quiz(*_a, **_k):
        yt_done["v"] = True
        return MagicMock(status="PASS", document=object(), error=None)

    monkeypatch.setattr(auto_run, "search_supplements_for_gaps", fake_search)
    monkeypatch.setattr(auto_run, "run_supplement_funnel_for_gaps", fake_funnel)
    monkeypatch.setattr(auto_run, "resolve_intro_timeline", fake_intro_timing)
    monkeypatch.setattr(auto_run, "resolve_chapter_timeline", fake_chapter_timing)
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
    monkeypatch.setattr(
        auto_run, "generate_youtube_quizzes_from_context", fake_yt_quiz
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
    assert "timing" in report.skipped
    assert "music" in report.skipped
    assert "otio" in report.skipped
    assert "youtube" in report.skipped


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
    for step_id in ("stock", "funnel", "timing", "music", "otio", "youtube"):
        assert step_id in ids
    assert ids[-1] == "youtube"
    assert ids[-2] == "otio"


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
    assert by_id["youtube"].done is False
    assert by_id["otio"].done is False


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
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert 'key_scope="auto_page"' in source
    assert 'key_scope="auto_panel"' in source
    assert "_render_running_auto_run_status" in source
    assert "_render_auto_run_status_overview" in source
    assert "Statusübersicht" in source
    assert "YouTube Publish" in source
    assert "Auto-Lauf fehlgeschlagen —" in source


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


def test_enhanced_navigation_includes_auto_run_page() -> None:
    from otio_app.ui.navigation import (
        PAGE_AUTO_RUN,
        VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
    )

    assert PAGE_AUTO_RUN == "▶ Auto-Lauf"
    assert PAGE_AUTO_RUN in VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS
    assert PAGE_AUTO_RUN not in VOICEOVER_GEN_NAVIGATION_OPTIONS
