"""Sequenzielle Enhanced-Pipeline: Brief → … → LLM Cuts → Funnel → Timing → Musik → OTIO → YouTube.

Pipeline-Schritte laufen nacheinander. Innerhalb eines Schritts bleiben
LLM-/TTS-Calls strikt einzeln; Python Timing der Körper-Kapitel läuft
parallel (bis ``ENHANCED_CHAPTER_TIMING_MAX_WORKERS``). Bereits erledigte
Schritte werden übersprungen (skip-done). Kapitel-Skripte laufen zuerst
komplett durch, danach die Freitext-Nachbearbeitung aller Kapitel, erst
dann Script Lock. Der Aufruf über den Auto-Lauf-Button gilt als explizite
Bestätigung für Dramaturgie, Script Lock und Intro (erste gültige Variante).
Clean Media, Analysen und SFX bleiben manuell. Offene Coverage-Gaps nach
dem Funnel sind ein Fehler (die Sprachen-Queue macht mit der nächsten
Sprache weiter).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from otio_app.defaults import ENHANCED_CHAPTER_TIMING_MAX_WORKERS
from otio_app.models import Project
from otio_app.project_layout import (
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_settings_path,
    get_intro_hook_confirmed_path,
    get_intro_hook_settings_path,
    get_project_brief_path,
    get_voiceover_style_references_path,
    get_youtube_metadata_path,
)
from otio_app.services.plan_llm_client import DEFAULT_MAX_OUTPUT_TOKENS
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    auto_run_dramaturgy_planning_mode,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    build_dramaturgy_plan,
    confirm_dramaturgy_plan,
    load_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.dramaturgy_settings_service import (
    default_dramaturgy_settings,
    save_dramaturgy_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    build_intro_hook_candidates,
    confirm_intro_hook,
    load_confirmed_intro_hook,
    load_intro_hook_candidates,
    missing_intro_source_folder_names,
)
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    default_intro_hook_settings,
    save_intro_hook_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    split_llm_model_id,
)
from otio_app.services.voiceover_generation.models import (
    IntroHookCandidate,
    IntroHookCandidatesDocument,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    load_project_brief,
    save_project_brief,
)
from otio_app.services.voiceover_generation.style_profile_service import (
    build_style_profile,
    load_style_profile,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    apply_language_style_defaults_to_project,
    is_raw_style_mode,
    load_style_references,
)
from otio_app.services.voiceover_generation.video_title_service import (
    generate_video_title,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    synthesize_open_chapters_audio,
    list_chapter_audio_statuses,
)
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    ChapterCutError,
    export_all_chapters_otio,
    generate_chapter_unified_cut,
    list_body_chapter_names,
    list_chapters_needing_python_timing,
    list_chapters_needing_unified_cut,
    list_chapters_ready_for_python_timing,
    refresh_merged_unified_cut_plan,
    resolve_all_chapter_timelines,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_service import (
    generate_music_for_allowed_targets,
    list_music_generation_targets,
    music_ui_status_chapter,
    music_ui_status_intro,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    IntroCutError,
    generate_intro_unified_cut,
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
    resolve_intro_timeline,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ensure_confirmed_intro_in_locked_script,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.maps.auto_run_maps import (
    maps_complete,
    run_maps_for_auto_run,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import MapPlanError
from otio_app.services.without_voiceover_enhanced.maps.render_service import (
    MapRenderCancelled,
    MapRenderError,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    resolve_llm_cut_model_id,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    UNIFIED_CUT_PLAN_FILENAME,
    accepted_supplements_path,
    chapter_resolved_timeline_path,
    chapters_cut_dir,
    coverage_gaps_path,
    exports_dir,
    map_plan_path,
    resolved_timeline_path,
    script_draft_path,
    script_locked_path,
    segment_timings_path,
    supplement_funnel_report_path,
)
from otio_app.services.youtube_publish_service import (
    build_youtube_publish_context_from_resolved,
    generate_youtube_publish_metadata_from_context,
    load_youtube_metadata,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    chapter_narration_text,
    folders_present_in_script,
    generate_enhanced_script_for_folder,
    list_enabled_dramaturgy_folders,
    revise_enhanced_script_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    load_script_draft,
    lock_script,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    SupplementFunnelError,
    list_open_funnel_gap_ids,
    run_supplement_funnel_for_gaps,
)

__all__ = [
    "AUTO_RUN_STEPS",
    "AUTO_RUN_STEP_SHORT_LABELS",
    "AUTO_RUN_STOCK_PROVIDERS",
    "AUTO_RUN_STOP_AFTER_FUNNEL",
    "AUTO_RUN_STOP_AFTER_LABELS",
    "AUTO_RUN_STOP_AFTER_YOUTUBE",
    "AutoRunProgress",
    "AutoRunStageSummary",
    "AutoRunStepStatus",
    "EnhancedAutoRunCancelled",
    "EnhancedAutoRunError",
    "EnhancedAutoRunReport",
    "auto_run_stop_step_id",
    "auto_run_steps_through",
    "format_auto_run_failure_message",
    "list_auto_run_step_statuses",
    "llm_cut_provider_model",
    "normalize_auto_run_stop_after",
    "pick_auto_intro_candidate",
    "pipeline_complete_through",
    "run_enhanced_auto_pipeline",
    "summarize_auto_run_stage",
    "youtube_publish_complete",
]

AUTO_RUN_STEPS: tuple[tuple[str, str], ...] = (
    ("brief", "① Project Brief"),
    ("style", "② Style"),
    ("dramaturgy", "③ Dramaturgie"),
    ("scripts", "④ Kapitel-Skripte"),
    ("script_revise", "④ Freitext-Nachbearbeitung"),
    ("script_lock", "④ Script Lock"),
    ("intro", "⑤ Intro"),
    ("tts", "⑥ Audio / TTS"),
    ("intro_cut", "⑦ Intro LLM Cut"),
    ("chapter_cuts", "⑦ Kapitel LLM Cuts"),
    ("stock", "⑧ Stocksuche"),
    ("funnel", "⑧ Supplement-Funnel"),
    ("maps", "Karten"),
    ("timing", "⑨ Python Timing"),
    ("music", "⑩ ElevenLabs Music"),
    ("otio", "⑪ OTIO-Export"),
    ("youtube", "⑫ YouTube Publish"),
)

AUTO_RUN_STEP_SHORT_LABELS: dict[str, str] = {
    "brief": "Brief",
    "style": "Style",
    "dramaturgy": "Dramaturgie",
    "scripts": "Skripte",
    "script_revise": "Freitext",
    "script_lock": "Lock",
    "intro": "Intro",
    "tts": "TTS",
    "intro_cut": "Intro-Cut",
    "chapter_cuts": "Kapitel-Cuts",
    "stock": "Stock",
    "funnel": "Funnel",
    "maps": "Karten",
    "timing": "Timing",
    "music": "Music",
    "otio": "OTIO",
    "youtube": "YouTube",
}

AUTO_RUN_STOP_AFTER_FUNNEL = "funnel"
AUTO_RUN_STOP_AFTER_YOUTUBE = "youtube"
AUTO_RUN_STOP_AFTER_LABELS: dict[str, str] = {
    AUTO_RUN_STOP_AFTER_FUNNEL: "Supplement-Funnel",
    AUTO_RUN_STOP_AFTER_YOUTUBE: "YouTube Publish",
}


def normalize_auto_run_stop_after(value: str | None) -> str:
    text = str(value or AUTO_RUN_STOP_AFTER_YOUTUBE).strip().lower()
    if text in {
        AUTO_RUN_STOP_AFTER_FUNNEL,
        "supplement",
        "supplement_funnel",
        "bis_funnel",
    }:
        return AUTO_RUN_STOP_AFTER_FUNNEL
    return AUTO_RUN_STOP_AFTER_YOUTUBE


def auto_run_stop_step_id(stop_after: str | None) -> str:
    if normalize_auto_run_stop_after(stop_after) == AUTO_RUN_STOP_AFTER_FUNNEL:
        return "funnel"
    return "youtube"


def auto_run_steps_through(stop_after: str | None) -> tuple[str, ...]:
    stop_id = auto_run_stop_step_id(stop_after)
    ids: list[str] = []
    for step_id, _label in AUTO_RUN_STEPS:
        ids.append(step_id)
        if step_id == stop_id:
            break
    return tuple(ids)


# Auto-Lauf sucht nur die freien Anbieter — analog zur UI-Auswahl.
AUTO_RUN_STOCK_PROVIDERS: dict[str, bool] = {
    "pexels": False,
    "pixabay": False,
    "wikimedia": True,
    "openverse": True,
    "archive_org": True,
}


class EnhancedAutoRunError(RuntimeError):
    """Harter Fehler — der Auto-Lauf stoppt."""


class EnhancedAutoRunCancelled(RuntimeError):
    """Nutzer hat Stop gedrückt."""


_AUTO_RUN_FAILURE_PREFIX = "Schritt "


def format_auto_run_failure_message(
    error: str,
    step_label: str = "",
    item_label: str = "",
) -> str:
    """Hängt den aktuellen Auto-Lauf-Schritt an die Fehlermeldung.

    Idempotent, falls die Meldung schon mit „Schritt …“ beginnt.
    """
    text = (error or "").strip() or "Unbekannter Fehler."
    if text.startswith(_AUTO_RUN_FAILURE_PREFIX):
        return text
    step = (step_label or "").strip()
    item = (item_label or "").strip()
    if not step:
        return text
    where = f"{_AUTO_RUN_FAILURE_PREFIX}{step}"
    if item:
        where = f"{where} · {item}"
    return f"{where}: {text}"


@dataclass
class AutoRunProgress:
    step_id: str
    step_label: str
    message: str
    step_index: int
    step_total: int
    item_label: str = ""
    item_index: int = 0
    item_total: int = 0
    skipped: bool = False


@dataclass(frozen=True)
class AutoRunStepStatus:
    step_id: str
    label: str
    short_label: str
    done: bool


@dataclass(frozen=True)
class AutoRunStageSummary:
    done_count: int
    step_total: int
    last_done_id: str | None
    last_done_label: str
    next_id: str | None
    next_label: str
    funnel_done: bool
    youtube_done: bool


_AUTO_RUN_STAGE_CACHE: dict[tuple[str, str], tuple[tuple[object, ...], AutoRunStageSummary]] = {}


def _path_stamp(path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return (str(path), 0, 0)


def _auto_run_stage_fingerprint(project: Project) -> tuple[object, ...]:
    """MTimes der Pipeline-Artefakte — ohne JSON zu parsen."""
    lang = project.language_work_dir_path
    paths = [
        get_project_brief_path(lang),
        get_voiceover_style_references_path(lang),
        get_dramaturgy_plan_confirmed_path(lang),
        get_intro_hook_confirmed_path(lang),
        get_youtube_metadata_path(lang),
        script_draft_path(project),
        script_locked_path(project),
        segment_timings_path(project),
        intro_unified_cut_plan_path(project),
        coverage_gaps_path(project),
        supplement_funnel_report_path(project),
        accepted_supplements_path(project),
        map_plan_path(project),
        exports_dir(project) / f"{project.name}_enhanced.otio",
    ]
    try:
        cuts = chapters_cut_dir(project)
        if cuts.is_dir():
            paths.extend(sorted(cuts.glob(f"*/{UNIFIED_CUT_PLAN_FILENAME}")))
    except (OSError, ValueError):
        pass
    return tuple(_path_stamp(path) for path in paths)


@dataclass
class EnhancedAutoRunReport:
    skipped: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    stopped: bool = False
    error: str | None = None


ProgressCallback = Callable[[AutoRunProgress], None]
CancelCallback = Callable[[], bool]


def pick_auto_intro_candidate(
    document: IntroHookCandidatesDocument,
) -> IntroHookCandidate:
    """Erste Variante ohne Risiken, sonst die erste vorhandene."""
    if not document.candidates:
        raise EnhancedAutoRunError("Keine Intro-Kandidaten vorhanden.")
    clean = [item for item in document.candidates if not item.risks]
    if clean:
        return clean[0]
    return document.candidates[0]


def _style_has_content(refs: VoiceoverStyleReferences) -> bool:
    chunks = [
        refs.raw_reference_text,
        refs.raw_intro_reference_text,
        *list(refs.intro_reference_texts or []),
        *list(refs.segment_reference_texts or []),
        *list(refs.uploaded_file_texts or []),
    ]
    return any(str(chunk or "").strip() for chunk in chunks)


def llm_cut_provider_model(
    project: Project,
    *,
    folder_name: str | None = None,
    is_intro: bool = False,
) -> tuple[str, str]:
    """Provider/Modell für Intro- und Kapitel-LLM-Cuts (Sprachstandard zuerst).

    Ohne Ziel: immer das Standard-Modell. Intro zählt als Index 0, danach
    Körper-Kapitel — die ersten N können ein Prefix-Modell nutzen.
    """
    return split_llm_model_id(
        resolve_llm_cut_model_id(
            project, folder_name=folder_name, is_intro=is_intro
        )
    )


def run_enhanced_auto_pipeline(
    project: Project,
    *,
    should_cancel: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    skip_done: bool = True,
    stop_after: str = AUTO_RUN_STOP_AFTER_YOUTUBE,
) -> EnhancedAutoRunReport:
    """Führt die Enhanced-Schritte strikt sequenziell aus bis Funnel oder YouTube."""
    report = EnhancedAutoRunReport()
    stop_after = normalize_auto_run_stop_after(stop_after)
    step_total = len(auto_run_steps_through(stop_after))
    last_step_label = ""
    last_item_label = ""

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def _remember(step_id: str, item_label: str | None = None) -> str:
        nonlocal last_step_label, last_item_label
        last_step_label = next(label for sid, label in AUTO_RUN_STEPS if sid == step_id)
        if item_label is not None:
            last_item_label = item_label
        return last_step_label

    def emit(
        step_id: str,
        message: str,
        *,
        skipped: bool = False,
        item_label: str = "",
        item_index: int = 0,
        item_total: int = 0,
    ) -> None:
        step_index = next(
            index
            for index, (sid, _) in enumerate(AUTO_RUN_STEPS, start=1)
            if sid == step_id
        )
        label = _remember(step_id, item_label)
        event = AutoRunProgress(
            step_id=step_id,
            step_label=label,
            message=message,
            step_index=step_index,
            step_total=step_total,
            item_label=item_label,
            item_index=item_index,
            item_total=item_total,
            skipped=skipped,
        )
        report.log_lines.append(message)
        if on_progress is not None:
            on_progress(event)

    def checkpoint(step_id: str) -> None:
        _remember(step_id, "")
        if cancelled():
            report.stopped = True
            emit(step_id, "Gestoppt.")
            raise EnhancedAutoRunCancelled("Auto-Lauf gestoppt.")

    def finish_step(step_id: str, *, skipped: bool) -> None:
        if skipped:
            report.skipped.append(step_id)
        else:
            report.completed.append(step_id)

    try:
        models = load_model_settings(project)

        checkpoint("brief")
        _run_brief(
            project,
            skip_done=skip_done,
            emit=emit,
            provider=models.project_brief.provider,
            model=models.project_brief.model,
            finish=finish_step,
        )

        checkpoint("style")
        _run_style(
            project,
            skip_done=skip_done,
            emit=emit,
            provider=models.style_profile.provider,
            model=models.style_profile.model,
            finish=finish_step,
        )

        checkpoint("dramaturgy")
        _run_dramaturgy(
            project,
            skip_done=skip_done,
            emit=emit,
            provider=models.dramaturgy.provider,
            model=models.dramaturgy.model,
            finish=finish_step,
        )

        checkpoint("scripts")
        _run_scripts(
            project,
            skip_done=skip_done,
            emit=emit,
            checkpoint=checkpoint,
            provider=models.voiceover_author.provider,
            model=models.voiceover_author.model,
            finish=finish_step,
        )

        checkpoint("script_revise")
        _run_script_revise(
            project,
            skip_done=skip_done,
            emit=emit,
            checkpoint=checkpoint,
            provider=models.voiceover_author.provider,
            model=models.voiceover_author.model,
            finish=finish_step,
        )

        checkpoint("script_lock")
        _run_script_lock(project, skip_done=skip_done, emit=emit, finish=finish_step)

        checkpoint("intro")
        _run_intro(
            project,
            skip_done=skip_done,
            emit=emit,
            provider=models.intro.provider,
            model=models.intro.model,
            finish=finish_step,
        )

        checkpoint("tts")
        _run_tts(project, skip_done=skip_done, emit=emit, finish=finish_step)

        checkpoint("intro_cut")
        _run_intro_cut(
            project,
            skip_done=skip_done,
            emit=emit,
            finish=finish_step,
        )

        checkpoint("chapter_cuts")
        _run_chapter_cuts(
            project,
            skip_done=skip_done,
            emit=emit,
            checkpoint=checkpoint,
            finish=finish_step,
        )

        checkpoint("stock")
        _run_stock_and_funnel(
            project,
            skip_done=skip_done,
            emit=emit,
            checkpoint=checkpoint,
            cancelled=cancelled,
            funnel_model=models.enhanced_supplement_funnel.model,
            finish=finish_step,
        )
        if stop_after == AUTO_RUN_STOP_AFTER_FUNNEL:
            emit("funnel", "Auto-Lauf bis Supplement-Funnel fertig.")
            return report

        checkpoint("maps")
        _run_maps(
            project,
            skip_done=skip_done,
            emit=emit,
            cancelled=cancelled,
            finish=finish_step,
        )

        checkpoint("timing")
        _run_timing(
            project,
            skip_done=skip_done,
            emit=emit,
            checkpoint=checkpoint,
            finish=finish_step,
        )

        checkpoint("music")
        _run_music(
            project,
            skip_done=skip_done,
            emit=emit,
            cancelled=cancelled,
            finish=finish_step,
        )

        checkpoint("otio")
        _run_otio(
            project,
            skip_done=skip_done,
            emit=emit,
            finish=finish_step,
        )

        checkpoint("youtube")
        _run_youtube(
            project,
            skip_done=skip_done,
            emit=emit,
            provider=models.youtube_publish.provider,
            model=models.youtube_publish.model,
            finish=finish_step,
        )

        emit("youtube", "Auto-Lauf fertig.")
        return report
    except EnhancedAutoRunCancelled:
        raise
    except EnhancedAutoRunError as exc:
        raise EnhancedAutoRunError(
            format_auto_run_failure_message(str(exc), last_step_label, last_item_label)
        ) from exc
    except Exception as exc:
        raise EnhancedAutoRunError(
            format_auto_run_failure_message(
                str(exc) or type(exc).__name__, last_step_label, last_item_label
            )
        ) from exc


def _run_brief(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    brief = load_project_brief(project)
    brief_path = get_project_brief_path(project.language_work_dir_path)
    if not brief_path.is_file():
        brief = save_project_brief(project, brief)
        emit("brief", "Sprachstandard ins Project Brief übernommen.")

    title = (brief.video_title or "").strip()
    if skip_done and title:
        emit("brief", f"Videotitel vorhanden — übersprungen ({title}).", skipped=True)
        finish("brief", skipped=True)
        return

    place = (project.video_place or "").strip()
    refs = list(brief.title_references or [])
    if not place:
        raise EnhancedAutoRunError(
            "Kein Land/Region am Projekt — unter Gespeicherte Projekte eintragen."
        )
    if not refs:
        raise EnhancedAutoRunError(
            "Keine Titel-Referenzen — zuerst den Sprachstandard für Brief speichern."
        )
    emit("brief", "Videotitel wird erzeugt…")
    result = generate_video_title(
        project,
        language=brief.language,
        video_place=place,
        title_references=refs,
        tone_tags=list(brief.tone_tags),
        provider=provider,
        model=model,
    )
    if result.status != STATUS_PASS or not (result.title or "").strip():
        raise EnhancedAutoRunError(result.error or "Titel-Erzeugung fehlgeschlagen.")
    save_project_brief(
        project, brief.model_copy(update={"video_title": result.title.strip()})
    )
    emit("brief", f"Videotitel: {result.title.strip()}")
    finish("brief", skipped=False)


def _run_style(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    refs_path = get_voiceover_style_references_path(project.language_work_dir_path)
    refs = load_style_references(project)
    if skip_done and refs_path.is_file() and _style_has_content(refs):
        profile = load_style_profile(project)
        if is_raw_style_mode(refs) or profile is not None:
            emit("style", "Style-Referenzen vorhanden — übersprungen.", skipped=True)
            finish("style", skipped=True)
            return

    if not refs_path.is_file() or not _style_has_content(refs):
        emit("style", "Sprachstandard für Style wird übernommen…")
        refs = apply_language_style_defaults_to_project(project)

    if not _style_has_content(refs):
        raise EnhancedAutoRunError(
            "Kein Style-Standard für diese Sprache — zuerst unter Style "
            "„Als Standard speichern“."
        )

    if is_raw_style_mode(refs):
        emit("style", "Raw-Style übernommen.")
        finish("style", skipped=False)
        return

    if load_style_profile(project) is not None:
        emit("style", "Style Profile vorhanden.")
        finish("style", skipped=False)
        return

    emit("style", "Style Profile wird erzeugt…")
    brief = load_project_brief(project)
    result = build_style_profile(
        project,
        project_brief=brief,
        style_references=refs,
        provider=provider,
        model=model,
    )
    if result.status != STATUS_PASS or result.profile is None:
        raise EnhancedAutoRunError(result.error or "Style Profile fehlgeschlagen.")
    emit("style", "Style Profile gespeichert.")
    finish("style", skipped=False)


def _run_dramaturgy(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    if skip_done and load_confirmed_dramaturgy(project) is not None:
        emit("dramaturgy", "Dramaturgie bereits bestätigt — übersprungen.", skipped=True)
        finish("dramaturgy", skipped=True)
        return

    settings_path = get_dramaturgy_settings_path(project.language_work_dir_path)
    if not settings_path.is_file():
        save_dramaturgy_settings(project, default_dramaturgy_settings(project))

    mode = auto_run_dramaturgy_planning_mode()
    emit("dramaturgy", f"Dramaturgie wird geplant ({mode})…")
    result = build_dramaturgy_plan(
        project,
        provider=provider,
        model=model,
        planning_mode=mode,
    )
    if result.status != STATUS_PASS or result.plan is None:
        raise EnhancedAutoRunError(result.error or "Dramaturgie-Planung fehlgeschlagen.")
    confirmed = confirm_dramaturgy_plan(project, result.plan)
    enabled = sum(1 for entry in confirmed.recommended_folder_order if entry.enabled)
    emit(
        "dramaturgy",
        f"Dramaturgie bestätigt ({enabled} aktive Kapitel, Auto-Bestätigung).",
    )
    finish("dramaturgy", skipped=False)


def _run_scripts(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    checkpoint: Callable[[str], None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    entries = list_enabled_dramaturgy_folders(project)
    if not entries:
        raise EnhancedAutoRunError(
            "Keine aktiven Dramaturgie-Kapitel — Dramaturgie zuerst bestätigen."
        )
    draft = load_script_draft(project)
    present = folders_present_in_script(draft)
    pending = [
        entry
        for entry in entries
        if not (
            skip_done
            and entry.folder_name in present
            and chapter_narration_text(draft, entry.folder_name).strip()
        )
    ]
    total = len(entries)
    if not pending:
        emit(
            "scripts",
            f"Alle {total} Kapitel-Skripte vorhanden — übersprungen.",
            skipped=True,
        )
        finish("scripts", skipped=True)
        return

    generated = 0
    for index, entry in enumerate(pending, start=1):
        checkpoint("scripts")
        emit(
            "scripts",
            f"Skript {index}/{len(pending)}: {entry.folder_name}",
            item_label=entry.folder_name,
            item_index=index,
            item_total=len(pending),
        )
        result = generate_enhanced_script_for_folder(
            project,
            entry.folder_name,
            provider=provider,
            model=model,
        )
        if result.status != "PASS":
            raise EnhancedAutoRunError(
                result.error
                or f"Skripterzeugung fehlgeschlagen für „{entry.folder_name}“."
            )
        generated += 1
    emit("scripts", f"{generated} Kapitel-Skript(e) erzeugt (sequenziell).")
    finish("scripts", skipped=False)


def _folders_with_scripts(project: Project) -> list:
    entries = list_enabled_dramaturgy_folders(project)
    draft = load_script_draft(project)
    present = folders_present_in_script(draft)
    return [
        entry
        for entry in entries
        if entry.folder_name in present
        and chapter_narration_text(draft, entry.folder_name).strip()
    ]


def _run_script_revise(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    checkpoint: Callable[[str], None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    if skip_done and load_locked_script(project) is not None:
        emit(
            "script_revise",
            "Script Lock vorhanden — Freitext-Nachbearbeitung übersprungen.",
            skipped=True,
        )
        finish("script_revise", skipped=True)
        return

    folders = _folders_with_scripts(project)
    if not folders:
        raise EnhancedAutoRunError(
            "Keine Kapitel-Skripte für die Freitext-Nachbearbeitung."
        )
    instructions = DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS.strip()
    revised = 0
    for index, entry in enumerate(folders, start=1):
        checkpoint("script_revise")
        emit(
            "script_revise",
            f"Freitext {index}/{len(folders)}: {entry.folder_name}",
            item_label=entry.folder_name,
            item_index=index,
            item_total=len(folders),
        )
        result = revise_enhanced_script_for_folder(
            project,
            entry.folder_name,
            editor_instructions=instructions,
            provider=provider,
            model=model,
        )
        if result.status != "PASS":
            raise EnhancedAutoRunError(
                result.error
                or f"Freitext-Nachbearbeitung fehlgeschlagen für „{entry.folder_name}“."
            )
        revised += 1
    emit(
        "script_revise",
        f"{revised} Kapitel mit Standard-Freitext nachbearbeitet (sequenziell).",
    )
    finish("script_revise", skipped=False)


def _run_script_lock(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    finish: Callable[..., None],
) -> None:
    if skip_done and load_locked_script(project) is not None:
        emit("script_lock", "Script Lock vorhanden — übersprungen.", skipped=True)
        finish("script_lock", skipped=True)
        return
    emit("script_lock", "Skript wird gesperrt (Auto-Bestätigung)…")
    locked = lock_script(project)
    emit("script_lock", f"Script Lock {locked.script_version}.")
    finish("script_lock", skipped=False)


def _run_intro(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    settings_path = get_intro_hook_settings_path(project.language_work_dir_path)
    if not settings_path.is_file():
        save_intro_hook_settings(project, default_intro_hook_settings(project))

    confirmed = load_confirmed_intro_hook(project)
    if skip_done and confirmed is not None:
        emit("intro", "Intro bereits bestätigt — übersprungen.", skipped=True)
        ensure_confirmed_intro_in_locked_script(project)
        finish("intro", skipped=True)
        return

    missing = missing_intro_source_folder_names(project)
    if missing:
        raise EnhancedAutoRunError(
            "Intro-Quellen fehlen: " + ", ".join(missing)
        )

    emit("intro", "Intro-Varianten werden erzeugt…")
    result = build_intro_hook_candidates(
        project,
        provider=provider,
        model=model,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    if result.status != STATUS_PASS or result.document is None:
        raise EnhancedAutoRunError(result.error or "Intro-Erzeugung fehlgeschlagen.")
    document = result.document
    existing = load_intro_hook_candidates(project)
    if existing is not None:
        document = existing
    picked = pick_auto_intro_candidate(document)
    risk_note = ""
    if picked.risks:
        risk_note = f" (mit {len(picked.risks)} Risiko-Hinweis(en))"
    confirm_intro_hook(project, picked.hook_id)
    ensure_confirmed_intro_in_locked_script(project)
    emit(
        "intro",
        f"Intro bestätigt: {picked.hook_id}{risk_note} (erste gültige Variante).",
    )
    finish("intro", skipped=False)


def _run_tts(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    finish: Callable[..., None],
) -> None:
    statuses = list_chapter_audio_statuses(project)
    open_rows = [row for row in statuses if row.is_open]
    if skip_done and statuses and not open_rows:
        emit("tts", "Alle Kapitel bereits vertont — übersprungen.", skipped=True)
        finish("tts", skipped=True)
        return
    if not open_rows and not statuses:
        emit("tts", "TTS startet…")
    else:
        emit("tts", f"TTS für {len(open_rows)} offene Kapitel (sequenziell)…")

    def _tts_progress(
        folder_name: str,
        chapter_index: int,
        chapter_total: int,
        _segment_index: int,
        _segment_total: int,
    ) -> None:
        emit(
            "tts",
            f"TTS {chapter_index}/{chapter_total}: {folder_name}",
            item_label=folder_name,
            item_index=chapter_index,
            item_total=chapter_total,
        )

    synthesize_open_chapters_audio(project, progress_callback=_tts_progress)
    emit("tts", "TTS abgeschlossen.")
    finish("tts", skipped=False)


def _run_intro_cut(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    finish: Callable[..., None],
) -> None:
    existing = load_model(
        intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
    )
    if skip_done and existing is not None and existing.slots:
        emit("intro_cut", "Intro LLM Cut vorhanden — übersprungen.", skipped=True)
        finish("intro_cut", skipped=True)
        return
    provider, model = llm_cut_provider_model(project, is_intro=True)
    emit("intro_cut", f"Intro LLM Cut ({provider}:{model})…")
    result = generate_intro_unified_cut(
        project, provider=provider, model=model
    )
    emit(
        "intro_cut",
        f"Intro Cut: {result.slot_count} Slots, {result.gap_count} Gaps.",
    )
    finish("intro_cut", skipped=False)


def _run_chapter_cuts(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    checkpoint: Callable[[str], None],
    finish: Callable[..., None],
) -> None:
    names = list_chapters_needing_unified_cut(project) if skip_done else [
        entry.folder_name for entry in list_enabled_dramaturgy_folders(project)
    ]
    if not names:
        emit(
            "chapter_cuts",
            "Alle Kapitel-LLM-Cuts vorhanden — übersprungen.",
            skipped=True,
        )
        finish("chapter_cuts", skipped=True)
        return

    generated = 0
    errors: list[str] = []
    total = len(names)
    for index, name in enumerate(names, start=1):
        checkpoint("chapter_cuts")
        emit(
            "chapter_cuts",
            f"LLM Cut {index}/{total}: {name}",
            item_label=name,
            item_index=index,
            item_total=total,
        )
        try:
            chapter_provider, chapter_model = llm_cut_provider_model(
                project, folder_name=name
            )
            generate_chapter_unified_cut(
                project,
                name,
                provider=chapter_provider,
                model=chapter_model,
                refresh_merged=False,
            )
            generated += 1
        except ChapterCutError as exc:
            errors.append(f"{name}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — Batch stoppt, Teilergebnis bleibt
            errors.append(f"{name}: {exc}")
            break
    if generated:
        refresh_merged_unified_cut_plan(project)
    if errors:
        raise EnhancedAutoRunError(
            f"{generated}/{total} Kapitel-Cuts ok, dann Fehler: {errors[0]}"
        )
    emit("chapter_cuts", f"{generated} Kapitel-LLM-Cut(s) sequenziell erzeugt.")
    finish("chapter_cuts", skipped=False)


def intro_timing_complete(project: Project) -> bool:
    return intro_resolved_timeline_path(project).is_file()


def otio_export_complete(project: Project) -> bool:
    path = exports_dir(project) / f"{project.name}_enhanced.otio"
    if not path.is_file():
        return False
    if not intro_timing_complete(project):
        return False
    if list_chapters_needing_python_timing(project):
        return False
    try:
        otio_mtime = path.stat().st_mtime
    except OSError:
        return False
    candidates = [
        intro_resolved_timeline_path(project),
        resolved_timeline_path(project),
    ]
    for name in list_body_chapter_names(project):
        candidates.append(chapter_resolved_timeline_path(project, name))
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_mtime > otio_mtime + 1e-6:
                return False
        except OSError:
            continue
    return True


def youtube_publish_complete(project: Project) -> bool:
    """Titel + Beschreibung reichen; Quiz bleibt manuell auf Final Output."""
    document = load_youtube_metadata(project)
    if document is None:
        return False
    if not (document.title or "").strip():
        return False
    return bool(
        (document.description or "").strip()
        or (document.description_body or "").strip()
    )


def _music_targets_complete(project: Project) -> bool:
    targets = list_music_generation_targets(project)
    if not targets:
        return False
    for kind, folder in targets:
        if kind == "intro":
            status = music_ui_status_intro(project)
        else:
            status = music_ui_status_chapter(project, folder)
        if str(status.get("status") or "") != "completed":
            return False
    return True


def list_auto_run_step_statuses(
    project: Project,
    *,
    stop_after_first_open: bool = False,
) -> list[AutoRunStepStatus]:
    """Skip-done-Stand je Auto-Lauf-Schritt für die Statusübersicht.

    ``stop_after_first_open``: nach dem ersten offenen Schritt keine weiteren
    Disk-Checks (für die Projektliste). Spätere Zeilen gelten als offen.
    """

    def _safe(checker: Callable[[], bool]) -> bool:
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 — unfertiges Projekt zählt als offen
            return False

    def brief_done() -> bool:
        brief = load_project_brief(project)
        return bool((brief.video_title or "").strip())

    def style_done() -> bool:
        refs_path = get_voiceover_style_references_path(project.language_work_dir_path)
        refs = load_style_references(project)
        if not (refs_path.is_file() and _style_has_content(refs)):
            return False
        return is_raw_style_mode(refs) or load_style_profile(project) is not None

    def dramaturgy_done() -> bool:
        return load_confirmed_dramaturgy(project) is not None

    def scripts_done() -> bool:
        entries = list_enabled_dramaturgy_folders(project)
        if not entries:
            return False
        draft = load_script_draft(project)
        present = folders_present_in_script(draft)
        return all(
            entry.folder_name in present
            and chapter_narration_text(draft, entry.folder_name).strip()
            for entry in entries
        )

    def lock_done() -> bool:
        return load_locked_script(project) is not None

    def intro_done() -> bool:
        return load_confirmed_intro_hook(project) is not None

    def tts_done() -> bool:
        statuses = list_chapter_audio_statuses(project)
        return bool(statuses) and not any(row.is_open for row in statuses)

    def intro_cut_done() -> bool:
        existing = load_model(
            intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
        )
        return existing is not None and bool(existing.slots)

    def chapter_cuts_done() -> bool:
        return not list_chapters_needing_unified_cut(project)

    def gaps_done() -> bool:
        return not list_open_funnel_gap_ids(project)

    def timing_done() -> bool:
        return intro_timing_complete(project) and not list_chapters_needing_python_timing(
            project
        )

    funnel_gaps_done: bool | None = None

    def gaps_done_cached() -> bool:
        nonlocal funnel_gaps_done
        if funnel_gaps_done is None:
            funnel_gaps_done = gaps_done()
        return funnel_gaps_done

    checkers: dict[str, Callable[[], bool]] = {
        "brief": brief_done,
        "style": style_done,
        "dramaturgy": dramaturgy_done,
        "scripts": scripts_done,
        "script_revise": lock_done,
        "script_lock": lock_done,
        "intro": intro_done,
        "tts": tts_done,
        "intro_cut": intro_cut_done,
        "chapter_cuts": chapter_cuts_done,
        "stock": gaps_done_cached,
        "funnel": gaps_done_cached,
        "maps": lambda: maps_complete(project),
        "timing": timing_done,
        "music": lambda: _music_targets_complete(project),
        "otio": lambda: otio_export_complete(project),
        "youtube": lambda: youtube_publish_complete(project),
    }
    rows: list[AutoRunStepStatus] = []
    found_open = False
    for step_id, label in AUTO_RUN_STEPS:
        checker = checkers.get(step_id, lambda: False)
        if stop_after_first_open and found_open:
            done = False
        else:
            done = _safe(checker)
            if not done:
                found_open = True
        rows.append(
            AutoRunStepStatus(
                step_id=step_id,
                label=label,
                short_label=AUTO_RUN_STEP_SHORT_LABELS.get(step_id, step_id),
                done=done,
            )
        )
    return rows


def pipeline_complete_through(
    project: Project,
    stop_after: str = AUTO_RUN_STOP_AFTER_YOUTUBE,
) -> bool:
    """True wenn jeder Schritt bis einschließlich Funnel bzw. YouTube erledigt ist."""
    return _complete_through_rows(
        list_auto_run_step_statuses(project, stop_after_first_open=True),
        stop_after,
    )


def _complete_through_rows(
    rows: list[AutoRunStepStatus],
    stop_after: str,
) -> bool:
    wanted = set(auto_run_steps_through(stop_after))
    by_id = {row.step_id: row for row in rows}
    for step_id in wanted:
        row = by_id.get(step_id)
        if row is None or not row.done:
            return False
    return True


def summarize_auto_run_stage(project: Project) -> AutoRunStageSummary:
    """Konsekutiver Pipeline-Stand für die Statusübersicht je Sprache."""
    cache_key = (str(project.id), str(project.work_dir))
    try:
        fingerprint = _auto_run_stage_fingerprint(project)
    except Exception:  # noqa: BLE001 — Cache ist optional
        fingerprint = None
    if fingerprint is not None:
        hit = _AUTO_RUN_STAGE_CACHE.get(cache_key)
        if hit is not None and hit[0] == fingerprint:
            return hit[1]
    rows = list_auto_run_step_statuses(project, stop_after_first_open=True)
    last_done: AutoRunStepStatus | None = None
    next_open: AutoRunStepStatus | None = None
    consecutive = 0
    for row in rows:
        if next_open is not None:
            break
        if row.done:
            last_done = row
            consecutive += 1
        else:
            next_open = row
    if next_open is None and last_done is not None:
        next_label = "fertig"
    elif next_open is not None:
        next_label = next_open.short_label
    else:
        next_label = AUTO_RUN_STEP_SHORT_LABELS.get("brief", "Brief")
    summary = AutoRunStageSummary(
        done_count=consecutive,
        step_total=len(rows),
        last_done_id=last_done.step_id if last_done else None,
        last_done_label=last_done.short_label if last_done else "—",
        next_id=next_open.step_id if next_open else None,
        next_label=next_label,
        funnel_done=_complete_through_rows(rows, AUTO_RUN_STOP_AFTER_FUNNEL),
        youtube_done=_complete_through_rows(rows, AUTO_RUN_STOP_AFTER_YOUTUBE),
    )
    if fingerprint is not None:
        _AUTO_RUN_STAGE_CACHE[cache_key] = (fingerprint, summary)
    return summary


def _run_stock_and_funnel(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    checkpoint: Callable[[str], None],
    cancelled: Callable[[], bool],
    funnel_model: str,
    finish: Callable[..., None],
) -> None:
    open_ids = list_open_funnel_gap_ids(project)
    if skip_done and not open_ids:
        emit("stock", "Keine offenen Coverage-Gaps — Stocksuche übersprungen.", skipped=True)
        finish("stock", skipped=True)
        emit("funnel", "Keine offenen Coverage-Gaps — Funnel übersprungen.", skipped=True)
        finish("funnel", skipped=True)
        return

    save_stock_providers_config(project, AUTO_RUN_STOCK_PROVIDERS)
    emit(
        "stock",
        "Stockanbieter: Wikimedia, Openverse, Archive.org.",
    )
    if not open_ids:
        emit("stock", "Keine Coverage-Gaps — Stocksuche übersprungen.", skipped=True)
        finish("stock", skipped=True)
        emit("funnel", "Kein Funnel nötig.", skipped=True)
        finish("funnel", skipped=True)
        return

    emit("stock", f"Stocksuche für {len(open_ids)} offene Gap(s)…")
    try:
        results = search_supplements_for_gaps(project)
    except CutPlanError as exc:
        text = str(exc)
        if "bereits erfüllt" in text:
            emit("stock", "Alle Gaps bereits erfüllt — übersprungen.", skipped=True)
            finish("stock", skipped=True)
            emit("funnel", "Kein Funnel nötig.", skipped=True)
            finish("funnel", skipped=True)
            return
        raise EnhancedAutoRunError(text) from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancedAutoRunError(f"Stocksuche fehlgeschlagen: {exc}") from exc

    n_candidates = len(getattr(results, "candidates", None) or [])
    emit("stock", f"Stocksuche fertig — {n_candidates} Kandidat(en).")
    finish("stock", skipped=False)

    checkpoint("funnel")
    emit("funnel", f"Alle offenen Gaps auflösen ({len(open_ids)})…")
    try:
        run_supplement_funnel_for_gaps(
            project,
            gap_ids=open_ids,
            skip_filled=True,
            should_stop=cancelled,
            model=(funnel_model or "").strip() or None,
        )
    except EnhancedAutoRunCancelled:
        raise
    except SupplementFunnelError as exc:
        raise EnhancedAutoRunError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancedAutoRunError(f"Funnel fehlgeschlagen: {exc}") from exc

    still_open = list_open_funnel_gap_ids(project)
    if still_open:
        preview = ", ".join(still_open[:8])
        more = f" (+{len(still_open) - 8})" if len(still_open) > 8 else ""
        raise EnhancedAutoRunError(
            f"{len(still_open)} Coverage Gap(s) nach dem Funnel noch offen: "
            f"{preview}{more}"
        )
    emit("funnel", "Alle offenen Gaps erfüllt.")
    finish("funnel", skipped=False)


def _run_maps(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    cancelled: Callable[[], bool],
    finish: Callable[..., None],
) -> None:
    if skip_done and maps_complete(project):
        emit("maps", "Karten vorhanden — übersprungen.", skipped=True)
        finish("maps", skipped=True)
        return

    def on_message(message: str, **kwargs) -> None:
        emit("maps", message, **kwargs)

    emit("maps", "Karten: Plan, Koordinaten, Rendern…")
    try:
        result = run_maps_for_auto_run(
            project,
            should_cancel=cancelled,
            on_message=on_message,
        )
    except EnhancedAutoRunCancelled:
        raise
    except MapRenderCancelled as exc:
        raise EnhancedAutoRunCancelled(str(exc) or "Auto-Lauf gestoppt.") from exc
    except MapPlanError as exc:
        raise EnhancedAutoRunError(str(exc)) from exc
    except MapRenderError as exc:
        raise EnhancedAutoRunError(f"Kartenrender fehlgeschlagen: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancedAutoRunError(f"Karten fehlgeschlagen: {exc}") from exc

    failed = list(result.get("failed") or [])
    if failed:
        first_id, first_reason = failed[0]
        more = f" (+{len(failed) - 1})" if len(failed) > 1 else ""
        raise EnhancedAutoRunError(
            f"{len(failed)} Karte(n) fehlgeschlagen ({first_id}{more}): {first_reason}"
        )
    rendered = list(result.get("rendered") or [])
    blocked = list(result.get("blocked") or [])
    already_done = list(result.get("already_done") or [])
    errors = list(result.get("geocode_errors") or [])
    parts = [f"{len(rendered)} gerendert"]
    if already_done:
        parts.append(f"{len(already_done)} schon da")
    if blocked:
        parts.append(f"{len(blocked)} ohne Koordinaten")
    if errors:
        parts.append(f"{len(errors)} Geocode-Hinweis(e)")
    emit("maps", "Karten fertig — " + ", ".join(parts) + ".")
    finish("maps", skipped=False)


def _run_timing(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    checkpoint: Callable[[str], None],
    finish: Callable[..., None],
) -> None:
    intro_done = intro_timing_complete(project)
    names = (
        list_chapters_needing_python_timing(project)
        if skip_done
        else list_chapters_ready_for_python_timing(project)
    )
    if skip_done and intro_done and not names:
        emit("timing", "Python Timing vorhanden — übersprungen.", skipped=True)
        finish("timing", skipped=True)
        return

    if not intro_done:
        emit("timing", "Intro Python Timing…", item_label="Intro")
        try:
            resolve_intro_timeline(project)
        except IntroCutError as exc:
            raise EnhancedAutoRunError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise EnhancedAutoRunError(f"Intro Python Timing fehlgeschlagen: {exc}") from exc

    timed = 0
    if names:
        total = len(names)
        workers = min(max(1, ENHANCED_CHAPTER_TIMING_MAX_WORKERS), total)

        def _timing_progress(folder_name: str, index: int, total_chapters: int) -> None:
            checkpoint("timing")
            emit(
                "timing",
                f"Python Timing {index}/{total_chapters}: {folder_name} (parallel, max. {workers})",
                item_label=folder_name,
                item_index=index,
                item_total=total_chapters,
            )

        emit(
            "timing",
            f"Python Timing parallel ({total} Kapitel, max. {workers})…",
            item_total=total,
        )
        try:
            timed_results = resolve_all_chapter_timelines(
                project,
                chapter_names=names,
                progress_callback=_timing_progress,
                max_workers=workers,
            )
            timed = len(timed_results)
        except ChapterCutError as exc:
            raise EnhancedAutoRunError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise EnhancedAutoRunError(f"Python Timing fehlgeschlagen: {exc}") from exc

    leftover = list_chapters_needing_python_timing(project)
    if leftover:
        preview = ", ".join(leftover[:8])
        more = f" (+{len(leftover) - 8})" if len(leftover) > 8 else ""
        raise EnhancedAutoRunError(
            f"Python Timing unvollständig, noch offen: {preview}{more}"
        )
    emit("timing", f"Python Timing fertig (Intro + {timed} Kapitel).")
    finish("timing", skipped=False)


def _run_music(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    cancelled: Callable[[], bool],
    finish: Callable[..., None],
) -> None:
    emit("music", "ElevenLabs Music (Intro + erste Kapitel laut Settings)…")

    def _progress(label: str, index: int, total: int) -> None:
        emit(
            "music",
            f"ElevenLabs Music {index}/{total}: {label}",
            item_label=label,
            item_index=index,
            item_total=total,
        )

    try:
        result = generate_music_for_allowed_targets(
            project,
            skip_completed=True,
            on_progress=_progress,
            should_stop=cancelled,
        )
    except EnhancedAutoRunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EnhancedAutoRunError(f"ElevenLabs Music fehlgeschlagen: {exc}") from exc

    if result.get("stopped"):
        raise EnhancedAutoRunCancelled("Auto-Lauf gestoppt.")
    failed = list(result.get("failed") or [])
    if failed:
        first = failed[0]
        raise EnhancedAutoRunError(
            f"ElevenLabs Music fehlgeschlagen ({first.get('label') or 'Ziel'}): "
            f"{first.get('reason') or 'Unbekannt'}"
        )
    generated = list(result.get("generated") or [])
    skipped = list(result.get("skipped") or [])
    leftover_skip = [
        item
        for item in skipped
        if str(item.get("reason") or "").strip() != "bereits vorhanden"
    ]
    if leftover_skip:
        first = leftover_skip[0]
        raise EnhancedAutoRunError(
            "ElevenLabs Music unvollständig "
            f"({first.get('label') or 'Ziel'}): "
            f"{first.get('reason') or 'übersprungen'}"
        )
    if skip_done and not generated and skipped:
        emit("music", "ElevenLabs Music vorhanden — übersprungen.", skipped=True)
        finish("music", skipped=True)
        return
    emit(
        "music",
        f"ElevenLabs Music fertig ({len(generated)} neu, {len(skipped)} übersprungen).",
    )
    finish("music", skipped=False)


def _run_otio(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    finish: Callable[..., None],
) -> None:
    if skip_done and otio_export_complete(project):
        emit("otio", "OTIO-Export vorhanden — übersprungen.", skipped=True)
        finish("otio", skipped=True)
        return
    emit("otio", "OTIO-Export (Intro + alle Kapitel)…")
    try:
        path = export_all_chapters_otio(
            project,
            basename=f"{project.name}_enhanced",
            allow_errors=True,
            include_intro=True,
        )
    except (ChapterCutError, EnhancedOtioExportError) as exc:
        raise EnhancedAutoRunError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise EnhancedAutoRunError(f"OTIO-Export fehlgeschlagen: {exc}") from exc
    emit("otio", f"OTIO geschrieben: {path}")
    finish("otio", skipped=False)


def load_resolved_timeline_for_auto_run(project: Project) -> ResolvedTimelineDocument | None:
    return load_model(resolved_timeline_path(project), ResolvedTimelineDocument)


def _run_youtube(
    project: Project,
    *,
    skip_done: bool,
    emit: Callable[..., None],
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    if skip_done and youtube_publish_complete(project):
        emit("youtube", "YouTube Publish vorhanden — übersprungen.", skipped=True)
        finish("youtube", skipped=True)
        return

    emit("youtube", "YouTube Publish (Metadaten)…")
    resolved = load_resolved_timeline_for_auto_run(project)
    if resolved is None:
        raise EnhancedAutoRunError(
            "Keine aufgelöste Timeline für YouTube — OTIO-Export zuerst."
        )
    context = build_youtube_publish_context_from_resolved(project, resolved)
    if not context.chapters:
        raise EnhancedAutoRunError(
            "Keine Kapitel in der Timeline — YouTube Publish nicht möglich."
        )

    existing = load_youtube_metadata(project)
    need_meta = not (
        existing is not None
        and (existing.title or "").strip()
        and (
            (existing.description or "").strip()
            or (existing.description_body or "").strip()
        )
    )
    if not skip_done:
        need_meta = True

    if need_meta:
        emit("youtube", "YouTube-Metadaten…", item_label="Metadaten")
        result = generate_youtube_publish_metadata_from_context(
            project,
            context,
            provider=provider,
            model=model,
        )
        if result.status != STATUS_PASS or result.document is None:
            raise EnhancedAutoRunError(
                result.error or "YouTube-Metadaten fehlgeschlagen."
            )

    if not youtube_publish_complete(project):
        raise EnhancedAutoRunError(
            "YouTube Publish unvollständig — Titel oder Beschreibung fehlen."
        )
    emit("youtube", "YouTube Publish fertig (Metadaten). Quiz bleibt manuell.")
    finish("youtube", skipped=False)
