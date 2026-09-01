"""Gemeinsames YouTube-Publish-UI unter OTIO Export (Schnittplan + Cut Plan)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    YOUTUBE_DESCRIPTION_BODY_MAX_CHARS,
    YOUTUBE_DESCRIPTION_MAX_CHARS,
    YOUTUBE_HASHTAGS_MAX_CHARS,
)
from otio_app.models import Project
from otio_app.services.edit_plan_validator import ValidationStatus
from otio_app.services.otio_exporter import MergedEditPlanResult, merge_confirmed_edit_plans
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.youtube_publish_models import YouTubeMetadataDocument
from otio_app.services.youtube_publish_service import (
    _normalize_hashtags,
    build_youtube_publish_context,
    build_youtube_publish_context_from_resolved,
    format_youtube_chapter_lines,
    generate_youtube_publish_metadata,
    generate_youtube_publish_metadata_from_context,
    generate_youtube_quizzes,
    generate_youtube_quizzes_from_context,
    load_youtube_metadata,
    youtube_country_folder_text_path,
    youtube_description_for_copy,
    youtube_metadata_path,
    youtube_project_metadata_path,
)
from otio_app.ui.activity import log_heavy_operation
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_model_selectbox,
)


def _merge_blockers_message(
    merged: MergedEditPlanResult,
    folder_selection: tuple[str, ...],
) -> str:
    if not folder_selection:
        return (
            "Kein Ort zum Export ausgewählt — wähle mindestens einen **bestätigten** Ort."
        )
    if merged.skipped_folders and not merged.included_folders:
        return (
            "Keine bestätigten Schnittpläne für die gewählten Orte — "
            "bitte zuerst promoten / bestätigen."
        )
    if not merged.timeline_items:
        return "Keine `timeline_items` im Schnittplan — bitte Schnittplan neu erzeugen und bestätigen."
    if merged.validation_status != ValidationStatus.OK.value:
        return (
            f"Schnittplan-Validierung: **{merged.validation_status}** — "
            "Details prüfen, ggf. neu vorschlagen und bestätigen."
        )
    return "Merge nicht bereit — bitte Validierungsmeldungen prüfen."


def _copyable_text(
    label: str,
    value: str,
    *,
    key: str,
    height: int | None = None,
) -> None:
    """Kopierbares Feld mit Streamlit-Copy-Button (st.code)."""
    del key, height  # Anzeige über st.code — kein Widget-State nötig.
    st.markdown(f"**{label}**")
    st.code(value or "", language=None)


def _render_copyable_results(
    document: YouTubeMetadataDocument,
    project: Project,
    *,
    key_prefix: str,
) -> None:
    run = document.llm_run_id or "saved"
    project_id = project.id
    st.caption("Oben rechts am Code-Block: **Kopieren**-Icon (ein Klick).")

    _copyable_text(
        "YouTube-Titel",
        document.title,
        key=f"{key_prefix}_yt_title_{project_id}_{run}",
    )
    wonders = document.formatted_wonders_title()
    if wonders:
        _copyable_text(
            "Videotitel (Die Wunder von …)",
            wonders,
            key=f"{key_prefix}_yt_wonders_{project_id}_{run}",
        )
        st.caption(
            "Zweizeilige Titelkarte in der Videosprache: "
            "Zeile 1 = Formel, Zeile 2 = Land/Region."
        )
    description = youtube_description_for_copy(document, project)
    _copyable_text(
        f"Beschreibung ({len(description)}/{YOUTUBE_DESCRIPTION_MAX_CHARS}, "
        f"Textkörper ≤{YOUTUBE_DESCRIPTION_BODY_MAX_CHARS})",
        description,
        key=f"{key_prefix}_yt_desc_{project_id}_{run}",
        height=280,
    )
    _copyable_text(
        f"Hashtags ({len(_normalize_hashtags(document.hashtags))}/{YOUTUBE_HASHTAGS_MAX_CHARS}) — Format: USA, Natur, …",
        _normalize_hashtags(document.hashtags),
        key=f"{key_prefix}_yt_hash_{project_id}_{run}",
        height=80,
    )

    if document.chapters:
        chapter_text = format_youtube_chapter_lines(
            document.chapters, document.language, project
        )
        _copyable_text(
            "Kapitel",
            chapter_text,
            key=f"{key_prefix}_yt_chapters_{project_id}_{run}",
            height=min(220, 40 + 22 * len(document.chapters)),
        )
        st.caption("Kapitelnamen wie auf der Karte, in der Videosprache.")

    prompts = [str(item).strip() for item in document.thumbnail_prompts if str(item).strip()]
    if prompts:
        _copyable_text(
            "Thumbnail-Prompts (ohne Text, realistisch)",
            "\n".join(f"{index}. {prompt}" for index, prompt in enumerate(prompts, start=1)),
            key=f"{key_prefix}_yt_thumbs_{project_id}_{run}",
            height=min(160, 40 + 28 * len(prompts)),
        )

    if document.quizzes:
        st.markdown("**YouTube Quiz**")
        for quiz in document.quizzes:
            st.caption(
                f"Quiz {quiz.order_index} · einfügen bei {quiz.insert_timestamp} "
                f"({quiz.insert_at_sec:.0f}s)"
                + (f" · {quiz.reason}" if quiz.reason else "")
            )
            _copyable_text(
                "Frage",
                quiz.question,
                key=f"{key_prefix}_yt_q_{project_id}_{run}_{quiz.order_index}",
            )
            for opt_index, opt in enumerate(quiz.options):
                suffix = " (richtig)" if opt.is_correct else ""
                _copyable_text(
                    f"Antwort{suffix}",
                    opt.text,
                    key=f"{key_prefix}_yt_a_{project_id}_{run}_{quiz.order_index}_{opt_index}",
                )


def _render_saved_paths(project: Project) -> None:
    st.caption(
        f"TXT im Länderordner: `{youtube_country_folder_text_path(project)}`  \n"
        f"Intern: `{youtube_metadata_path(project)}`  \n"
        f"JSON (Sprache): `{youtube_project_metadata_path(project)}`"
    )


def render_youtube_publish_block(
    project: Project,
    folder_selection: tuple[str, ...] | list[str],
    *,
    page: str,
    key_prefix: str,
) -> None:
    """YouTube-Titel, Beschreibung, Kapitel, Hashtags und Quiz unter OTIO Export."""
    folders = tuple(folder_selection)
    st.markdown("---")
    st.markdown("**📺 YouTube Publish**")
    st.caption(
        "Zwei getrennte Schritte: **Metadaten** (YouTube-Titel, Videotitel "
        "„Die Wunder von …“, Beschreibung/Hashtags) und **Quiz**. "
        "Das LLM erhält nur Kapitelüberschriften + Timestamps — keine Folder-Skripte. "
        "Kapitelzeiten kommen aus dem Timeline-Merge."
    )

    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für YouTube Publish", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell",
            role_settings=settings.youtube_publish,
            key=f"{key_prefix}_yt_publish_model_{project.id}",
            input_info=LLM_INPUT_INFO["youtube_publish"],
        )
        if st.button("Modell speichern", key=f"{key_prefix}_yt_publish_model_save_{project.id}"):
            updated = settings.model_copy(update={"youtube_publish": role_settings})
            save_model_settings(project, updated)
            st.success("Modell für YouTube Publish gespeichert.")

    existing = load_youtube_metadata(project)
    if existing is not None and existing.title:
        st.write(f"**Titel:** {existing.title}")
        wonders = existing.formatted_wonders_title()
        if wonders:
            st.markdown("**Videotitel:**")
            st.code(wonders, language=None)
    else:
        st.caption("Titel erscheint nach der Metadaten-Generierung.")

    col_meta, col_quiz = st.columns(2)
    with col_meta:
        meta_clicked = st.button(
            "YouTube-Metadaten generieren",
            key=f"{key_prefix}_yt_publish_generate_{project.id}",
            type="primary",
            use_container_width=True,
            help="YouTube-Titel, Videotitel (Wunder von …), Beschreibung und Hashtags.",
        )
    with col_quiz:
        quiz_clicked = st.button(
            "YouTube-Quiz generieren",
            key=f"{key_prefix}_yt_quiz_generate_{project.id}",
            use_container_width=True,
            help="Nur Quizzes (1× pro 10 Min.) — Metadaten bleiben erhalten.",
        )

    if meta_clicked or quiz_clicked:
        try:
            kind = "Metadaten" if meta_clicked else "Quiz"
            with st.spinner(f"Timeline mergen und YouTube-{kind} generieren …"):
                merged = merge_confirmed_edit_plans(
                    project,
                    folder_names=list(folders) if folders else None,
                )
                if not merged.ready:
                    st.warning(_merge_blockers_message(merged, folders))
                    for warning in merged.warnings:
                        st.caption(f"• {warning}")
                else:
                    context = build_youtube_publish_context(project, merged)
                    if not context.chapters:
                        st.warning(
                            "Keine Kapitel im Timeline-Merge — "
                            "bitte Schnittplan bestätigen und Orte wählen."
                        )
                    elif meta_clicked:
                        log_heavy_operation(
                            f"YouTube Metadaten ({len(context.chapters)} Kapitel)",
                            page=page,
                        )
                        result = generate_youtube_publish_metadata(
                            project,
                            merged,
                            provider=role_settings.provider,
                            model=role_settings.model,
                        )
                        if result.status != STATUS_PASS or result.document is None:
                            st.error(result.error or "YouTube-Metadaten fehlgeschlagen.")
                        else:
                            st.success(
                                f"YouTube-Metadaten gespeichert "
                                f"({len(result.document.chapters)} Kapitel; "
                                f"{len(result.document.quizzes)} Quiz unverändert)."
                            )
                            st.rerun()
                    else:
                        log_heavy_operation(
                            f"YouTube Quiz ({context.quiz_count} geplant, "
                            f"{len(context.chapters)} Kapitel)",
                            page=page,
                        )
                        result = generate_youtube_quizzes(
                            project,
                            merged,
                            provider=role_settings.provider,
                            model=role_settings.model,
                        )
                        if result.status != STATUS_PASS or result.document is None:
                            st.error(result.error or "YouTube-Quiz fehlgeschlagen.")
                        else:
                            st.success(
                                f"YouTube-Quiz gespeichert "
                                f"({len(result.document.quizzes)} Quiz)."
                            )
                            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    document = load_youtube_metadata(project)
    if document is None:
        return

    _render_copyable_results(
        document,
        project,
        key_prefix=key_prefix,
    )
    _render_saved_paths(project)


def render_enhanced_youtube_publish_block(
    project: Project,
    resolved: object,
    *,
    page: str,
    key_prefix: str = "enhanced_final",
) -> None:
    """YouTube Publish für Enhanced: Kontext aus Resolved Timeline (Kapitelüberschriften)."""
    st.markdown("**📺 YouTube Publish**")
    st.caption(
        "Zwei getrennte Schritte: **Metadaten** (YouTube-Titel, Videotitel "
        "„Die Wunder von …“, Beschreibung/Hashtags) und **Quiz**. "
        "Das LLM erhält nur Kapitelüberschriften + Timestamps aus der aufgelösten Timeline — "
        "keine Folder-Skripte."
    )

    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für YouTube Publish", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell",
            role_settings=settings.youtube_publish,
            key=f"{key_prefix}_yt_publish_model_{project.id}",
            input_info=LLM_INPUT_INFO["youtube_publish"],
        )
        if st.button("Modell speichern", key=f"{key_prefix}_yt_publish_model_save_{project.id}"):
            updated = settings.model_copy(update={"youtube_publish": role_settings})
            save_model_settings(project, updated)
            st.success("Modell für YouTube Publish gespeichert.")

    existing = load_youtube_metadata(project)
    if existing is not None and existing.title:
        st.write(f"**Titel:** {existing.title}")
        wonders = existing.formatted_wonders_title()
        if wonders:
            st.markdown("**Videotitel:**")
            st.code(wonders, language=None)
    else:
        st.caption(
            "Titel erscheint nach der Metadaten-Generierung "
            "(aus Project Brief / Dramaturgie + Kapitelüberschriften)."
        )

    col_meta, col_quiz = st.columns(2)
    with col_meta:
        meta_clicked = st.button(
            "YouTube-Metadaten generieren",
            key=f"{key_prefix}_yt_publish_generate_{project.id}",
            type="primary",
            use_container_width=True,
            help="YouTube-Titel, Videotitel (Wunder von …), Beschreibung und Hashtags.",
        )
    with col_quiz:
        quiz_clicked = st.button(
            "YouTube-Quiz generieren",
            key=f"{key_prefix}_yt_quiz_generate_{project.id}",
            use_container_width=True,
            help="Nur Quizzes (1× pro 10 Min.) — Metadaten bleiben erhalten.",
        )

    if meta_clicked or quiz_clicked:
        try:
            kind = "Metadaten" if meta_clicked else "Quiz"
            with st.spinner(f"YouTube-{kind} aus Enhanced-Timeline generieren …"):
                context = build_youtube_publish_context_from_resolved(project, resolved)
                if not context.chapters:
                    st.warning(
                        "Keine Kapitel in der aufgelösten Timeline — "
                        "bitte zuerst Final Cut auflösen."
                    )
                elif meta_clicked:
                    log_heavy_operation(
                        f"YouTube Metadaten Enhanced ({len(context.chapters)} Kapitel)",
                        page=page,
                    )
                    result = generate_youtube_publish_metadata_from_context(
                        project,
                        context,
                        provider=role_settings.provider,
                        model=role_settings.model,
                    )
                    if result.status != STATUS_PASS or result.document is None:
                        st.error(result.error or "YouTube-Metadaten fehlgeschlagen.")
                    else:
                        st.success(
                            f"YouTube-Metadaten gespeichert "
                            f"({len(result.document.chapters)} Kapitel; "
                            f"{len(result.document.quizzes)} Quiz unverändert)."
                        )
                        st.rerun()
                else:
                    log_heavy_operation(
                        f"YouTube Quiz Enhanced ({context.quiz_count} geplant, "
                        f"{len(context.chapters)} Kapitel)",
                        page=page,
                    )
                    result = generate_youtube_quizzes_from_context(
                        project,
                        context,
                        provider=role_settings.provider,
                        model=role_settings.model,
                    )
                    if result.status != STATUS_PASS or result.document is None:
                        st.error(result.error or "YouTube-Quiz fehlgeschlagen.")
                    else:
                        st.success(
                            f"YouTube-Quiz gespeichert "
                            f"({len(result.document.quizzes)} Quiz)."
                        )
                        st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    document = load_youtube_metadata(project)
    if document is None:
        return

    _render_copyable_results(
        document,
        project,
        key_prefix=key_prefix,
    )
    _render_saved_paths(project)
