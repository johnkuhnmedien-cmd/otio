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
    filter_observation_review_items,
    get_observation_review_view,
    get_phase8_project_summary,
    submit_observation_review,
    submit_observation_review_batch,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_PREPARE_PROFILE_VERSION,
    FRAME_SAMPLE_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
)
from otio_app.discovery_v2.ui.flash import discovery_ui_flash_and_rerun
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
            discovery_ui_flash_and_rerun(result.message, level="info")
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

    # Optional advanced filter — default path analyzes all prepared assets.
    asset_options = [item.asset_id for item in prepared]
    selected_asset_ids = list(asset_options)
    if hasattr(st, "expander"):
        with st.expander("Optionaler Asset-Filter", expanded=False):
            selected_asset_ids = _model_asset_selection(asset_options)
    else:
        selected_asset_ids = _model_asset_selection(asset_options)
    preview = preview_model_analysis_selection(project, selected_asset_ids)
    st.caption(
        f"Queue: {preview.asset_count} Asset(s), "
        f"{preview.frame_count} Frame(s), {preview.total_bytes} Bytes "
        "(assetweise, Fake-Parallelität=1)."
    )
    if preview.message:
        st.info(preview.message)
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
        "Vorbereitete Assets analysieren",
        disabled=not can_start_model,
        key="discovery_v2_model_analysis_start",
    )
    if start_clicked and can_start_model:
        # None = all prepared eligible assets (no manual multi-ID picking required).
        use_filter = set(selected_asset_ids) != set(asset_options)
        result = start_model_analysis(
            project,
            asset_ids=selected_asset_ids if use_filter else None,
            consent_acknowledged=consent,
            sync=False,
        )
        if result.started:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)

    _render_observation_review(project)


def _model_asset_selection(asset_options: list[str]) -> list[str]:
    if hasattr(st, "multiselect"):
        selected = st.multiselect(
            "Optional: Assets eingrenzen (Standard = alle vorbereiteten)",
            asset_options,
            default=asset_options,
            key="discovery_v2_model_analysis_assets",
        )
        return list(selected)
    return list(asset_options)


def _model_consent_checkbox() -> bool:
    label = (
        "Ich bestätige die Modellanalyse für die persistierten "
        "Representative Frames der Queue (Fake Vision, assetweise)."
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
        "automatische Asset-Auswahl. Unreviewed Observations bleiben unreviewed."
    )

    view = get_observation_review_view(project)
    if not view.ok:
        st.warning(view.message or "Observation Review nicht verfügbar.")
        return
    observations = view.observations
    if not observations:
        st.write("Noch keine Visual Observations persistiert.")
        return

    filter_options = {
        "Alle": "all",
        "Unreviewed": "unreviewed",
        "Accepted": "accepted",
        "Rejected": "rejected",
        "Geringe Confidence": "low_confidence",
        "Geografisches Risiko": "geographic_risk",
        "Möglicherweise synthetisch": "possibly_synthetic",
        "Technische Warnung": "technical_warning",
    }
    filter_label = "Unreviewed"
    if hasattr(st, "radio"):
        filter_label = st.radio(
            "Filter",
            list(filter_options.keys()),
            index=list(filter_options.keys()).index("Unreviewed"),
            key="discovery_v2_observation_review_filter",
            horizontal=True,
        )
    visible = filter_observation_review_items(
        observations,
        status_filter=filter_options.get(str(filter_label), "all"),
    )
    st.caption(f"Sichtbar: {len(visible)} von {len(observations)} Observation(s).")

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
            for item in visible
        ],
        use_container_width=True,
        hide_index=True,
    )

    visible_ids = [item.observation_id for item in visible if item.is_valid]
    select_all = False
    if hasattr(st, "checkbox"):
        select_all = bool(
            st.checkbox(
                "Alle sichtbaren auswählen",
                value=False,
                key="discovery_v2_observation_select_all_visible",
            )
        )
    selected_ids: list[str] = list(visible_ids) if select_all else []
    if hasattr(st, "multiselect") and not select_all:
        selected_ids = list(
            st.multiselect(
                "Observations für Batch",
                visible_ids,
                default=[],
                key="discovery_v2_observation_batch_ids",
            )
        )
    st.caption(f"Ausgewählt: {len(selected_ids)} Observation(s).")

    batch_reason = ""
    if hasattr(st, "text_input"):
        batch_reason = str(
            st.text_input(
                "Batch-Grund (Reject/Reanalyse)",
                key="discovery_v2_observation_batch_reason",
            )
            or ""
        ).strip()
    confirm_count = False
    if hasattr(st, "checkbox"):
        confirm_count = bool(
            st.checkbox(
                (
                    f"{len(selected_ids)} Observations werden entschieden. "
                    "Diese Entscheidung wird für jede Observation protokolliert."
                ),
                value=False,
                key="discovery_v2_observation_batch_confirm",
                disabled=not selected_ids,
            )
        )

    if st.button(
        "Ausgewählte akzeptieren",
        key="discovery_v2_observation_batch_accept",
        disabled=not selected_ids or not confirm_count,
    ):
        _submit_batch_review(
            project,
            observation_ids=selected_ids,
            decision="accepted",
            user_confirmed=confirm_count,
        )
    if st.button(
        "Ausgewählte ablehnen",
        key="discovery_v2_observation_batch_reject",
        disabled=not selected_ids or not confirm_count or not batch_reason,
    ):
        _submit_batch_review(
            project,
            observation_ids=selected_ids,
            decision="rejected",
            reason_code=batch_reason or "batch_reject",
            user_confirmed=confirm_count,
        )
    if st.button(
        "Ausgewählte erneut analysieren",
        key="discovery_v2_observation_batch_reanalyze",
        disabled=not selected_ids or not confirm_count or not batch_reason,
    ):
        _submit_batch_review(
            project,
            observation_ids=selected_ids,
            decision="reanalyze_requested",
            reason_code=batch_reason or "batch_reanalyze",
            user_confirmed=confirm_count,
        )

    st.markdown("**Einzelansicht**")
    for item in visible:
        st.markdown(f"**{item.asset_id}** · `{item.observation_id}`")
        st.caption(
            f"Identity: `{item.analysis_identity_id}` · "
            f"Working Media: `{item.working_media_id or '—'}` · "
            f"Frames: {', '.join(item.evidence_frame_ids) or '—'} · "
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
        discovery_ui_flash_and_rerun(result.message)
    else:
        st.warning(
            f"{result.error_code or 'observation_review_failed'}: {result.message}"
        )


def _submit_batch_review(
    project,
    *,
    observation_ids: list[str],
    decision: str,
    reason_code: str | None = None,
    user_confirmed: bool = False,
) -> None:
    result = submit_observation_review_batch(
        project,
        observation_ids=observation_ids,
        decision=decision,
        reason_code=reason_code,
        user_confirmed=user_confirmed,
    )
    if result.ok:
        discovery_ui_flash_and_rerun(result.message)
    else:
        st.warning(
            f"{result.error_code or 'observation_batch_failed'}: {result.message}"
        )
