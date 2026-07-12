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
    generate_youtube_publish_metadata,
    load_youtube_metadata,
    youtube_metadata_path,
)
from otio_app.ui.activity import log_heavy_operation
from otio_app.ui.voiceover_generation._shared import render_llm_model_selectbox


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
    *,
    project_id: str,
    key_prefix: str,
) -> None:
    run = document.llm_run_id or "saved"
    st.caption("Oben rechts am Code-Block: **Kopieren**-Icon (ein Klick).")

    _copyable_text(
        "YouTube-Titel",
        document.title,
        key=f"{key_prefix}_yt_title_{project_id}_{run}",
    )
    _copyable_text(
        f"Beschreibung ({len(document.description)}/{YOUTUBE_DESCRIPTION_MAX_CHARS}, "
        f"Textkörper ≤{YOUTUBE_DESCRIPTION_BODY_MAX_CHARS})",
        document.description,
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
        chapter_text = "\n".join(
            f"{chapter.display_title} - {chapter.timestamp}" for chapter in document.chapters
        )
        _copyable_text(
            "Kapitel",
            chapter_text,
            key=f"{key_prefix}_yt_chapters_{project_id}_{run}",
            height=min(220, 40 + 22 * len(document.chapters)),
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
        "Titel, Beschreibung (max. ~3500 Zeichen Text + Kapitel-Timestamps), Hashtags und "
        "Quiz (1× pro 10 Min., 3 Antworten). Kapitelzeiten kommen aus dem Timeline-Merge; "
        "Text/Hashtags/Quiz über LLM in der Projektsprache."
    )

    settings = load_model_settings(project)
    with st.expander("⚙️ Modell für YouTube Publish", expanded=False):
        role_settings = render_llm_model_selectbox(
            label="Modell",
            role_settings=settings.youtube_publish,
            key=f"{key_prefix}_yt_publish_model_{project.id}",
        )
        if st.button("Modell speichern", key=f"{key_prefix}_yt_publish_model_save_{project.id}"):
            updated = settings.model_copy(update={"youtube_publish": role_settings})
            save_model_settings(project, updated)
            st.success("Modell für YouTube Publish gespeichert.")

    existing = load_youtube_metadata(project)
    if existing is not None and existing.title:
        st.write(f"**Titel:** {existing.title}")
    else:
        st.caption("Titel erscheint nach der Generierung (aus dem bestätigten Voice-over-Plan).")

    generate_clicked = st.button(
        "YouTube-Beschreibung & Quiz generieren",
        key=f"{key_prefix}_yt_publish_generate_{project.id}",
        type="primary",
        use_container_width=True,
        help="Merged die gewählten Orte, baut Kapitelzeiten und ruft das LLM auf.",
    )

    if generate_clicked:
        try:
            with st.spinner("Timeline mergen und YouTube-Metadaten generieren …"):
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
                    if not any(
                        (entry.get("voiceover_text") or "").strip()
                        for entry in context.folder_scripts
                    ) and not (context.intro_text or "").strip():
                        st.warning(
                            "Kein bestätigtes Voice-over-Skript gefunden. "
                            "Bitte zuerst **⑦ Final Output** / den Voice-over-Plan bestätigen."
                        )
                    else:
                        log_heavy_operation(
                            f"YouTube Publish ({context.quiz_count} Quiz, "
                            f"{len(context.chapters)} Kapitel)",
                            page=page,
                        )
                        result = generate_youtube_publish_metadata(
                            project,
                            merged,
                            provider=role_settings.provider,
                            model=role_settings.model,
                        )
                        if result.status != STATUS_PASS or result.document is None:
                            st.error(result.error or "YouTube-Generierung fehlgeschlagen.")
                        else:
                            st.success(
                                f"YouTube-Metadaten gespeichert "
                                f"({len(result.document.quizzes)} Quiz, "
                                f"{len(result.document.chapters)} Kapitel)."
                            )
                            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    document = load_youtube_metadata(project)
    if document is None:
        return

    _render_copyable_results(
        document,
        project_id=project.id,
        key_prefix=key_prefix,
    )
    st.caption(f"Gespeichert: `{youtube_metadata_path(project)}`")
