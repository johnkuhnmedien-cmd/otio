"""Streamlit-Seite: Discovery V2 Assetanalyse (Prepare, Fake Vision, Review)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.analysis_prepare_service import (
    get_analysis_prepare_artifact_review,
    get_analysis_prepare_view,
    start_analysis_prepare,
)
from otio_app.discovery_v2.application.model_analysis_service import (
    get_model_analysis_view,
    preview_model_analysis_selection,
    start_model_analysis,
)
from otio_app.discovery_v2.application.observation_review_service import (
    get_observation_review_view,
    get_phase8_project_summary,
    submit_observation_review,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_PREPARE_PROFILE_VERSION,
    FRAME_SAMPLE_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
)
from otio_app.discovery_v2.ui.overview import active_discovery_project


def _short_hash(value: str | None) -> str:
    if not value:
        return "—"
    text = value.strip()
    if len(text) <= 12:
        return text
    return f"{text[:8]}…{text[-4:]}"


def render_discovery_asset_analysis_page() -> None:
    """Assetanalyse — lokale Prepare-UI, nur persistierte Daten beim Rendering."""
    st.title("Assetanalyse")
    project = active_discovery_project()
    if project is None:
        return

    view = get_analysis_prepare_view(project)

    st.subheader("Lokale Vorbereitung")
    st.info(
        "Die lokale Vorbereitung erkennt technische Videoszenen und erzeugt "
        "ausgewählte Analyseframes. Es werden keine Medien an externe Dienste "
        "gesendet."
    )

    if not view.ok:
        st.warning(view.message or "Eligibility nicht verfügbar.")
        if view.chain_error_code:
            st.caption(f"Grund: `{view.chain_error_code}`")
        return

    st.caption(
        f"Prepare-Profil: `{ANALYSIS_PREPARE_PROFILE_VERSION}` · "
        f"Shot: `{SHOT_DETECT_PROFILE_VERSION}` · "
        f"Frame: `{FRAME_SAMPLE_PROFILE_VERSION}` · "
        f"Plan: `{view.plan_id or '—'}`"
    )
    if view.active_run is not None:
        st.caption(
            f"Aktiver Analysis-Run: `{view.active_run.run_id}` "
            f"({view.active_run.scope}/{view.active_run.status.value})"
        )
    elif view.latest_run is not None:
        st.caption(
            f"Letzter Prepare-Run: `{view.latest_run.run_id}` "
            f"({view.latest_run.status.value})"
        )

    if not view.items:
        st.write("Keine Plan-Assets vorhanden.")
    else:
        rows = []
        for item in view.items:
            el = item.eligibility
            rows.append(
                {
                    "Anzeige": el.display_name or el.asset_id,
                    "Medienart": el.media_kind,
                    "Working-Media-Profil": el.actual_processing_profile_version
                    or "—",
                    "Analysis Identity": item.analysis_identity_id or "—",
                    "eligible": "ja" if el.eligible else "nein",
                    "Prepare-Status": (
                        item.prepare_status.value if item.prepare_status else "—"
                    ),
                    "Shots": item.shot_count,
                    "Frames": item.frame_count,
                    "Fehlergrund": item.error_code or el.reason_code or "—",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    eligible_count = sum(1 for i in view.items if i.eligibility.eligible)
    st.caption(
        f"{eligible_count} von {len(view.items)} Assets eligible für visuelle Analyse."
    )

    start_clicked = st.button(
        "Lokale Analyse vorbereiten",
        disabled=not view.can_start,
        key="discovery_v2_analysis_prepare_start",
    )
    if start_clicked and view.can_start:
        result = start_analysis_prepare(project, sync=False)
        if result.started:
            st.success(result.message)
        else:
            st.warning(result.message)

    _render_prepare_review(project)
    _render_model_analysis_section(project)


def _render_prepare_review(project) -> None:
    st.subheader("Review: Technical Shots und Frames")
    review = get_analysis_prepare_artifact_review(project)
    if not review.ok:
        st.caption(f"Registry nicht lesbar: {review.message or 'unbekannter Fehler'}")
        return
    shots = review.shots
    frames = review.frames

    if not shots and not frames:
        st.write("Noch keine persistierten Shots oder Frames vorhanden.")
        return

    if shots:
        st.markdown("**Technical Shots**")
        st.dataframe(
            [
                {
                    "Asset": shot.asset_id,
                    "Ordinal": shot.ordinal,
                    "Start": round(shot.start_seconds, 3),
                    "Ende": round(shot.end_seconds, 3),
                    "Dauer": round(shot.duration_seconds, 3),
                    "Profil": shot.detection_profile_version,
                }
                for shot in shots
            ],
            use_container_width=True,
            hide_index=True,
        )

    if frames:
        st.markdown("**Representative Frames**")
        st.dataframe(
            [
                {
                    "Asset": frame.asset_id,
                    "Ordinal": frame.ordinal,
                    "Timestamp": (
                        "—"
                        if frame.timestamp_seconds is None
                        else round(frame.timestamp_seconds, 3)
                    ),
                    "Breite": frame.width,
                    "Höhe": frame.height,
                    "Helligkeit": round(frame.brightness_mean, 4),
                    "Schwarzanteil": round(frame.black_fraction, 4),
                    "Schärfehinweis": round(frame.sharpness_score, 2),
                    "Profil": frame.sampling_profile_version,
                    "Pfad": frame.relative_path,
                    "Hash": _short_hash(frame.frame_sha256),
                }
                for frame in frames
            ],
            use_container_width=True,
            hide_index=True,
        )


def _render_model_analysis_section(project) -> None:
    st.subheader("Modellanalyse")
    try:
        model_view = get_model_analysis_view(project)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Modellanalyse nicht verfügbar: {exc}")
        return
    if not model_view.ok:
        st.warning(model_view.message or "Modellanalyse nicht verfügbar.")
        return

    st.caption(
        f"Vision-Konfiguration: `{model_view.config_provider or '—'}` · "
        f"Modell: `{model_view.config_model_label or '—'}`"
    )
    st.info(
        "Für diesen Stand wird der Fake-Adapter lokal verwendet; es werden "
        "keine Medien an externe Dienste gesendet."
    )
    if model_view.active_run is not None:
        st.caption(
            f"Aktiver Analysis-Run: `{model_view.active_run.run_id}` "
            f"({model_view.active_run.scope}/{model_view.active_run.status.value})"
        )
    elif model_view.latest_run is not None:
        st.caption(
            f"Letzter Modellanalyse-Run: `{model_view.latest_run.run_id}` "
            f"({model_view.latest_run.status.value})"
        )

    prepared = model_view.prepared_assets
    if not prepared:
        st.write("Noch keine vorbereiteten Assets mit Analyseframes vorhanden.")
        _render_observation_review(project)
        return

    st.markdown("**Vorbereitete Assets**")
    st.dataframe(
        [
            {
                "Asset": item.asset_id,
                "Analysis Identity": item.analysis_identity_id,
                "Medienart": item.media_kind,
                "Frames": item.frame_count,
                "Bytes": item.total_bytes,
            }
            for item in prepared
        ],
        use_container_width=True,
        hide_index=True,
    )

    asset_options = [item.asset_id for item in prepared]
    selected_asset_ids = _model_asset_selection(asset_options)
    preview = preview_model_analysis_selection(project, selected_asset_ids)
    st.caption(
        f"Auswahl: {preview.asset_count} Asset(s), "
        f"{preview.frame_count} Frame(s), {preview.total_bytes} Bytes."
    )
    if preview.error_code:
        st.warning(f"{preview.error_code}: {preview.message or 'Auswahl ungültig.'}")

    consent = _model_consent_checkbox()
    can_start_model = (
        consent
        and preview.frame_count > 0
        and preview.error_code is None
        and model_view.can_start
        and model_view.chain_ok
    )
    start_clicked = st.button(
        "Modellanalyse starten",
        disabled=not can_start_model,
        key="discovery_v2_model_analysis_start",
    )
    if start_clicked and can_start_model:
        result = start_model_analysis(
            project,
            asset_ids=selected_asset_ids,
            consent_acknowledged=consent,
            sync=False,
        )
        if result.started:
            st.success(result.message)
        else:
            st.warning(result.message)

    _render_observation_review(project)


def _model_asset_selection(asset_options: list[str]) -> list[str]:
    if hasattr(st, "multiselect"):
        selected = st.multiselect(
            "Assets für Modellanalyse",
            asset_options,
            default=asset_options,
            key="discovery_v2_model_analysis_assets",
        )
        return list(selected)
    return list(asset_options)


def _model_consent_checkbox() -> bool:
    label = (
        "Ich bestätige diese Modellanalyse für die ausgewählten "
        "persistierten Representative Frames."
    )
    if hasattr(st, "checkbox"):
        return bool(
            st.checkbox(
                label,
                value=False,
                key="discovery_v2_model_analysis_consent",
            )
        )
    return False


def _render_observation_review(project) -> None:
    st.subheader("Review: Visual Observations")
    summary = get_phase8_project_summary(project)
    if summary.ok:
        if summary.status_counts:
            st.dataframe(
                [
                    {"Status": status, "Assets": count}
                    for status, count in sorted(summary.status_counts.items())
                ],
                use_container_width=True,
                hide_index=True,
            )
        st.caption(
            f"Phase-8 Assets: {summary.total_assets} · "
            f"not_applicable: {summary.not_applicable_count}"
        )
    elif summary.message:
        st.caption(f"Phase-8 Summary nicht verfügbar: {summary.message}")

    st.info(
        "Freigabe bestätigt nur die strukturierte Beobachtung als redaktionelle "
        "Eingabe; sie bestätigt weder Geografie noch Echtheit und trifft keine "
        "automatische Asset-Auswahl."
    )

    view = get_observation_review_view(project)
    if not view.ok:
        st.warning(view.message or "Observation Review nicht verfügbar.")
        return
    observations = view.observations
    if not observations:
        st.write("Noch keine Visual Observations persistiert.")
        return
    st.dataframe(
        [
            {
                "Asset": item.asset_id,
                "Status": item.status,
                "Review": item.current_review_decision,
                "Current": "ja" if item.is_current_identity else "nein",
                "Editorial Ready": "ja" if item.is_editorial_ready else "nein",
                "Summary": item.summary,
                "Geo": (
                    "—"
                    if item.geographic_confidence is None
                    else item.geographic_confidence
                ),
                "Synthetic": (
                    "—"
                    if item.synthetic_confidence is None
                    else item.synthetic_confidence
                ),
                "Evidence Frames": ", ".join(item.evidence_frame_ids) or "—",
                "Uncertainty": "; ".join(item.uncertainty_notes) or "—",
                "Observation": item.observation_id,
                "Analysis Identity": item.analysis_identity_id,
                "Frames": _short_hash(item.frame_set_fingerprint),
                "JSON": _short_hash(item.observation_sha256),
                "Fehler": item.error_code or "—",
            }
            for item in observations
        ],
        use_container_width=True,
        hide_index=True,
    )

    for item in observations:
        st.markdown(f"**{item.asset_id}** · `{item.observation_id}`")
        st.caption(
            f"Identity: `{item.analysis_identity_id}` · "
            f"Working Media: `{item.working_media_id or '—'}` · "
            f"Prompt: `{item.prompt_version}` · Schema: `{item.response_schema_version}`"
        )
        if item.review_history:
            st.dataframe(
                [
                    {
                        "Revision": review.review_revision,
                        "Entscheidung": review.decision,
                        "Grund": review.reason_code or "—",
                        "Notiz": review.review_note or "—",
                        "Erstellt": review.created_at.isoformat(),
                        "Review ID": review.review_id,
                    }
                    for review in item.review_history
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Noch keine Review-Revision.")

        reason = _review_reason_input(item.observation_id)
        if st.button(
            "Observation akzeptieren",
            key=f"discovery_v2_observation_accept_{item.observation_id}",
            disabled=not item.is_valid,
        ):
            _submit_review_action(
                project,
                observation_id=item.observation_id,
                decision="accepted",
            )
        if st.button(
            "Erneute Analyse anfordern",
            key=f"discovery_v2_observation_reanalyze_{item.observation_id}",
            disabled=not item.is_valid,
        ):
            if not reason:
                st.warning("Für Reanalyse bitte einen Grund eingeben.")
            else:
                _submit_review_action(
                    project,
                    observation_id=item.observation_id,
                    decision="reanalyze_requested",
                    reason_code=reason,
                )
        if st.button(
            "Observation ablehnen",
            key=f"discovery_v2_observation_reject_{item.observation_id}",
            disabled=not item.is_valid,
        ):
            if not reason:
                st.warning("Für Ablehnung bitte einen Grund eingeben.")
            else:
                _submit_review_action(
                    project,
                    observation_id=item.observation_id,
                    decision="rejected",
                    reason_code=reason,
                )


def _review_reason_input(observation_id: str) -> str:
    label = "Grund für Reject/Reanalyse"
    key = f"discovery_v2_observation_reason_{observation_id}"
    if hasattr(st, "text_area"):
        return str(st.text_area(label, key=key) or "").strip()
    if hasattr(st, "text_input"):
        return str(st.text_input(label, key=key) or "").strip()
    return ""


def _submit_review_action(
    project,
    *,
    observation_id: str,
    decision: str,
    reason_code: str | None = None,
) -> None:
    result = submit_observation_review(
        project,
        observation_id=observation_id,
        decision=decision,
        reason_code=reason_code,
    )
    if result.ok:
        st.success(result.message)
    else:
        st.warning(
            f"{result.error_code or 'observation_review_failed'}: {result.message}"
        )
