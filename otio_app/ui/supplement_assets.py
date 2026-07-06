"""Streamlit-UI: Supplement Assets zwischen Zuordnung und Schnittplan."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanSettings, SupplementCandidate
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SECTION_OUTRO_SEC,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_GOOGLE,
    SUPPLEMENT_SOURCE_LABELS,
    SUPPLEMENT_SOURCE_MANUAL,
    SUPPLEMENT_SOURCE_NANO_BANANA,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.project_layout import safe_folder_slug
from otio_app.services.edit_plan_builder import build_edit_plan, load_edit_plan
from otio_app.services.edit_plan_rules import save_edit_plan_rules
from otio_app.services.gemini_client import is_gemini_configured
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED
from otio_app.services.supplement_pipeline import (
    acquire_supplement_candidate,
    analyze_supplement_asset,
    approve_adobe_candidate,
    extend_folder_inventory,
    mark_edit_plans_stale_for_folder,
    run_coverage_for_folder,
    search_supplement_candidates,
)
from otio_app.services.supplement_requests import (
    load_supplement_requests,
    pending_supplement_count,
    requests_for_folder,
    update_request,
)
from otio_app.services.edit_plan_builder import load_voice_analysis
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.edit_plan_rules_ui import get_edit_plan_rules_for_project
from otio_app.ui.navigation import PAGE_SUPPLEMENT
from otio_app.ui.project_context import render_project_selector, render_workflow_progress


def render_supplement_assets_page() -> None:
    st.header("②½ Supplement Assets")
    project = render_project_selector(PAGE_SUPPLEMENT)
    if project is None:
        st.info("Bitte zuerst ein Projekt auswählen.")
        return

    render_workflow_progress(project, current_step=PAGE_SUPPLEMENT)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        st.warning("Bitte zuerst unter **② Zuordnung** die Voice-over-Zuordnung bestätigen.")
        return

    folders = sorted({entry.folder for entry in mapping.entries if entry.folder and entry.confirmed})
    if not folders:
        st.warning("Keine bestätigten Ordner in der Zuordnung.")
        return

    selected_folder = st.selectbox("Asset-Ordner", folders, key=f"supplement_folder_{project.id}")
    document = load_supplement_requests(project)
    folder_requests = requests_for_folder(document, selected_folder)
    required = [req for req in folder_requests if req.status != "ACQUIRED"]

    st.markdown(
        f"**{len(required)}** offene Supplement-Anforderungen für **{selected_folder}** "
        f"(gesamt pending: {pending_supplement_count(document)})"
    )

    if st.button("Coverage prüfen & Supplement Requests erzeugen", key=f"coverage_{project.id}"):
        try:
            voice_doc = load_voice_analysis(project)
            inventory = load_folder_inventory(project, selected_folder)
            mapping_entry = next(
                entry for entry in mapping.entries if entry.folder == selected_folder and entry.confirmed
            )
            voice_entry = next(
                (item for item in voice_doc.files if item.path == mapping_entry.voice_file),
                None,
            )
            if voice_entry is None:
                st.error("Voice-over-Datei für diesen Ordner nicht gefunden.")
            else:
                coverages, requests = run_coverage_for_folder(
                    project,
                    folder_name=selected_folder,
                    voice_file=mapping_entry.voice_file,
                    segments=voice_entry.segments,
                    assets=inventory.assets,
                )
                st.success(
                    f"{len(coverages)} Segmente geprüft — "
                    f"{len(requests)} Supplement Request(s) erzeugt."
                )
                st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    if not folder_requests:
        st.info("Keine Supplement Requests — Coverage-Prüfung ausführen oder Schnittplan vorschlagen.")
        return

    for request in folder_requests:
        with st.expander(
            f"{request.supplement_request_id} · {request.beat_id} · {request.status}",
            expanded=request.status != "ACQUIRED",
        ):
            st.write(request.passage_text)
            st.caption(f"**Motiv:** {request.visual_requirement}")
            st.caption(
                f"Bester lokaler Kandidat: `{request.local_best_asset_id or '—'}` "
                f"(Score {request.local_best_match_score:.2f}) · "
                f"Dauer {request.duration_needed_sec:.1f}s"
            )
            st.caption(f"**Warum unzureichend:** {request.reason}")

            source = st.selectbox(
                "Quelle",
                options=list(SUPPLEMENT_SOURCE_LABELS.keys()),
                format_func=lambda key: SUPPLEMENT_SOURCE_LABELS[key],
                key=f"source_{request.supplement_request_id}",
                index=0,
            )
            if st.button("Quelle speichern", key=f"save_source_{request.supplement_request_id}"):
                update_request(
                    project,
                    request.supplement_request_id,
                    selected_source=source,
                    status="SOURCE_SELECTED",
                )
                st.rerun()

            candidates = [
                candidate
                for candidate in document.candidates
                if candidate.supplement_request_id == request.supplement_request_id
            ]
            if st.button("Supplement-Kandidaten suchen", key=f"search_{request.supplement_request_id}"):
                try:
                    updated = update_request(
                        project,
                        request.supplement_request_id,
                        selected_source=source,
                    )
                    if updated is None:
                        st.error("Request nicht gefunden.")
                    else:
                        found = search_supplement_candidates(project, updated)
                        st.success(f"{len(found)} Kandidat(en) gefunden.")
                        st.rerun()
                except (OSError, ValueError, PermissionError) as exc:
                    st.error(str(exc))

            if candidates:
                labels = [
                    f"{candidate.provider} · {candidate.title[:50]} · score={candidate.match_score:.2f}"
                    for candidate in candidates
                ]
                selected_idx = st.selectbox(
                    "Kandidat",
                    range(len(candidates)),
                    format_func=lambda index: labels[index],
                    key=f"candidate_{request.supplement_request_id}",
                )
                candidate: SupplementCandidate = candidates[selected_idx]

                if candidate.provider == SUPPLEMENT_SOURCE_ADOBE:
                    st.warning(
                        "Adobe Stock: Lizenzierung ist kostenpflichtig — nur nach expliziter Freigabe."
                    )
                    if st.button(
                        "Adobe Asset lizenzieren und herunterladen",
                        key=f"adobe_license_{request.supplement_request_id}",
                    ):
                        try:
                            approved = approve_adobe_candidate(candidate)
                            asset = acquire_supplement_candidate(project, approved, request)
                            st.success(f"Adobe-Asset gespeichert: `{asset.local_path}`")
                            st.rerun()
                        except (OSError, ValueError, PermissionError) as exc:
                            st.error(str(exc))
                elif st.button(
                    "Ausgewähltes Asset herunterladen/generieren",
                    key=f"acquire_{request.supplement_request_id}",
                ):
                    try:
                        asset = acquire_supplement_candidate(project, candidate, request)
                        st.success(f"Asset gespeichert: `{asset.local_path}`")
                        st.rerun()
                    except (OSError, ValueError, PermissionError) as exc:
                        st.error(str(exc))

    action_col1, action_col2, action_col3 = st.columns(3)
    with action_col1:
        analyze_clicked = st.button("Neue Assets analysieren", key=f"analyze_{project.id}")
    with action_col2:
        inventory_clicked = st.button("Inventory aktualisieren", key=f"inventory_{project.id}")
    with action_col3:
        replan_clicked = st.button(
            "Schnittplan mit neuen Assets neu vorschlagen",
            key=f"replan_{project.id}",
        )

    if analyze_clicked or inventory_clicked:
        acquired = [req for req in folder_requests if req.status == "ACQUIRED"]
        if not acquired:
            st.warning("Noch keine heruntergeladenen Supplement-Assets.")
        else:
            for request in acquired:
                provider_dir = Path(project.project_root_path) / selected_folder / "_supplemental" / f"_{request.selected_source or 'pexels'}"
                if not provider_dir.is_dir():
                    continue
                for media_path in sorted(provider_dir.glob("*")):
                    if media_path.suffix.lower() in {".json"}:
                        continue
                    sidecar_path = media_path.with_suffix(media_path.suffix + ".asset.json")
                    if not sidecar_path.is_file():
                        continue
                    from otio_app.services.supplement_pipeline import load_sidecar

                    sidecar = load_sidecar(media_path)
                    if sidecar is None:
                        continue
                    if analyze_clicked:
                        analyze_supplement_asset(
                            project,
                            folder_name=selected_folder,
                            local_path=media_path,
                            sidecar=sidecar,
                        )
                    if inventory_clicked:
                        asset = analyze_supplement_asset(
                            project,
                            folder_name=selected_folder,
                            local_path=media_path,
                            sidecar=sidecar,
                        )
                        extend_folder_inventory(project, folder_name=selected_folder, asset=asset)
                        mark_edit_plans_stale_for_folder(project, selected_folder)
            if analyze_clicked:
                st.success("Analyse abgeschlossen.")
            if inventory_clicked:
                st.success("Inventory erweitert — alter Schnittplan als stale markiert.")
            st.rerun()

    if replan_clicked:
        rules_doc = get_edit_plan_rules_for_project(project)
        save_edit_plan_rules(project, rules_doc)
        settings = EditPlanSettings(
            shot_min_sec=DEFAULT_SHOT_MIN_SEC,
            shot_max_sec=DEFAULT_SHOT_MAX_SEC,
            audio_offset_sec=DEFAULT_AUDIO_OFFSET_SEC,
            section_outro_sec=DEFAULT_SECTION_OUTRO_SEC,
            fallback_order=list(DEFAULT_FALLBACK_ORDER),
        )
        try:
            plan = build_edit_plan(
                project,
                settings,
                use_api=is_gemini_configured(),
                folder_names=[selected_folder],
                rules_doc=rules_doc,
            )
            from otio_app.services.edit_plan_builder import save_edit_plan

            save_edit_plan(project, plan, selected_folder)
            st.success(f"Schnittplan neu vorgeschlagen: {len(plan.shots)} Shots.")
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    existing_plan = load_edit_plan(project, selected_folder)
    if existing_plan and existing_plan.inventory_hash_at_plan_time:
        from otio_app.services.inventory_hash import inventory_hash_is_stale, current_folder_inventory_hash

        if inventory_hash_is_stale(
            project,
            selected_folder,
            existing_plan.inventory_hash_at_plan_time,
        ):
            st.warning("Inventory changed — please regenerate cut plan.")
