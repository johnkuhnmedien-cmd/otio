"""Sequenzielle Enhanced-Pipeline: Brief → … → LLM Cuts, Stop vor Funnel.

Ein Schritt nach dem anderen, innerhalb jedes Schritts ein Kapitel nach dem
anderen. Keine parallelen LLM-/TTS-Calls. Bereits erledigte Schritte werden
übersprungen (skip-done). Kapitel-Skripte laufen zuerst komplett durch, danach
die Freitext-Nachbearbeitung aller Kapitel, erst dann Script Lock. Der Aufruf
über den Auto-Lauf-Button gilt als explizite Bestätigung für Dramaturgie,
Script Lock und Intro (erste gültige Variante). Clean Media, Analysen, Funnel,
Timing, Musik, SFX, OTIO und YouTube bleiben manuell.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from otio_app.models import Project
from otio_app.project_layout import (
    get_dramaturgy_settings_path,
    get_intro_hook_settings_path,
    get_project_brief_path,
    get_voiceover_style_references_path,
)
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
    generate_chapter_unified_cut,
    list_chapters_needing_unified_cut,
    refresh_merged_unified_cut_plan,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    generate_intro_unified_cut,
    intro_unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ensure_confirmed_intro_in_locked_script,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import UnifiedCutPlanDocument
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

__all__ = [
    "AUTO_RUN_STEPS",
    "AutoRunProgress",
    "EnhancedAutoRunCancelled",
    "EnhancedAutoRunError",
    "EnhancedAutoRunReport",
    "pick_auto_intro_candidate",
    "run_enhanced_auto_pipeline",
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
)


class EnhancedAutoRunError(RuntimeError):
    """Harter Fehler — der Auto-Lauf stoppt."""


class EnhancedAutoRunCancelled(RuntimeError):
    """Nutzer hat Stop gedrückt."""


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


def run_enhanced_auto_pipeline(
    project: Project,
    *,
    should_cancel: CancelCallback | None = None,
    on_progress: ProgressCallback | None = None,
    skip_done: bool = True,
) -> EnhancedAutoRunReport:
    """Führt die Enhanced-Schritte strikt sequenziell aus. Stoppt vor Funnel."""
    report = EnhancedAutoRunReport()
    step_total = len(AUTO_RUN_STEPS)

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

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
        label = AUTO_RUN_STEPS[step_index - 1][1]
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
        if cancelled():
            report.stopped = True
            emit(step_id, "Gestoppt.")
            raise EnhancedAutoRunCancelled("Auto-Lauf gestoppt.")

    def finish_step(step_id: str, *, skipped: bool) -> None:
        if skipped:
            report.skipped.append(step_id)
        else:
            report.completed.append(step_id)

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
        provider=models.enhanced_final_cut.provider,
        model=models.enhanced_final_cut.model,
        finish=finish_step,
    )

    checkpoint("chapter_cuts")
    _run_chapter_cuts(
        project,
        skip_done=skip_done,
        emit=emit,
        checkpoint=checkpoint,
        provider=models.enhanced_final_cut.provider,
        model=models.enhanced_final_cut.model,
        finish=finish_step,
    )

    emit("chapter_cuts", "Auto-Lauf fertig — als Nächstes manuell: Funnel / Timing.")
    return report


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
        project, provider=provider, model=model
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
    provider: str,
    model: str,
    finish: Callable[..., None],
) -> None:
    existing = load_model(
        intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
    )
    if skip_done and existing is not None and existing.slots:
        emit("intro_cut", "Intro LLM Cut vorhanden — übersprungen.", skipped=True)
        finish("intro_cut", skipped=True)
        return
    emit("intro_cut", "Intro LLM Cut…")
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
    provider: str,
    model: str,
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
            generate_chapter_unified_cut(
                project,
                name,
                provider=provider,
                model=model,
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
