"""Streamlit-UI: Supplement Assets zwischen Zuordnung und Schnittplan."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    SupplementCandidate,
    SupplementRequest,
    SupplementRequestsDocument,
)
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
from otio_app.services.generic_outro_selector import section_id_for_folder
from otio_app.services.gemini_client import is_gemini_configured
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED, coverage_to_supplement_request
from otio_app.services.supplement_pipeline import (
    acquire_supplement_candidate,
    acquire_google_candidate_for_private_use,
    analyze_supplement_asset,
    approve_adobe_candidate,
    extend_folder_inventory,
    import_manual_supplement_asset,
    mark_edit_plans_stale_for_folder,
    run_coverage_for_folder,
    search_supplement_candidates,
)
from otio_app.services.supplement_search import preferred_search_query, request_with_keyword_query
from otio_app.services.supplement_requests import (
    load_supplement_requests,
    pending_supplement_count,
    requests_for_folder,
    save_supplement_requests,
    update_request,
    upsert_requests,
)
from otio_app.services.edit_plan_builder import load_voice_analysis
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping
from otio_app.ui.edit_plan_rules_ui import get_edit_plan_rules_for_project
from otio_app.ui.navigation import PAGE_SUPPLEMENT
from otio_app.ui.project_context import render_project_selector, render_workflow_progress


def _plan_state_key(project_id: str, folder_name: str) -> str:
    return f"edit_plan_draft_{project_id}_{safe_folder_slug(folder_name)}"


def _load_current_plan(project, folder_name: str) -> EditPlanDocument | None:
    raw = st.session_state.get(_plan_state_key(project.id, folder_name))
    if raw:
        try:
            return EditPlanDocument.model_validate(raw)
        except ValueError:
            pass
    return load_edit_plan(project, folder_name)


def _materialize_requests_from_plan(project, folder_name: str) -> int:
    """Erzeugt Requests aus aktuellem Schnittplan/Draft, wenn noch keine Datei existiert."""
    plan = _load_current_plan(project, folder_name)
    if plan is None:
        return 0

    existing = load_supplement_requests(project)
    existing_ids = {request.supplement_request_id for request in existing.requests}
    new_requests: list[SupplementRequest] = []
    active_request_ids: set[str] = set()

    missing_shots = [shot for shot in plan.shots if not shot.asset_path]

    # If the concrete cut plan already tells us which shots are missing, use that
    # actionable list instead of broad coverage hints. Otherwise one missing shot
    # can be hidden among many weak-but-still-used local matches.
    if missing_shots:
        for index, shot in enumerate(plan.shots, start=1):
            if shot.asset_path:
                continue
            request_id = (
                shot.supplement_request_id
                or f"supp_req_{safe_folder_slug(folder_name)}_{index:03d}"
            )
            active_request_ids.add(request_id)
            if request_id in existing_ids:
                continue
            passage = shot.passage_text or shot.motif or f"Shot {index}"
            new_requests.append(
                SupplementRequest(
                    supplement_request_id=request_id,
                    section_id=section_id_for_folder(folder_name),
                    folder_name=folder_name,
                    beat_id=shot.beat_id or f"shot_{index:03d}",
                    passage_text=passage,
                    visual_requirement=shot.motif or passage,
                    duration_needed_sec=max(0.1, shot.duration_sec),
                    reason=(
                        "Schnittplan enthält für dieses Voice-over-Segment kein Asset. "
                        "Bitte supplementieren oder lokalen Kandidaten manuell akzeptieren."
                    ),
                    local_best_asset_id=shot.asset_id,
                    local_best_match_score=0.0,
                search_queries={
                    "en": [
                        preferred_search_query(
                            SupplementRequest(
                                supplement_request_id=request_id,
                                section_id=section_id_for_folder(folder_name),
                                folder_name=folder_name,
                                beat_id=shot.beat_id or f"shot_{index:03d}",
                                passage_text=passage,
                                visual_requirement=shot.motif or passage,
                            )
                        )
                    ],
                    "de": [shot.motif or passage],
                },
                    status="PENDING_SOURCE_SELECTION",
                )
            )
            existing_ids.add(request_id)
    else:
        for coverage in plan.segment_coverage:
            if coverage.coverage_status != COVERAGE_SUPPLEMENT_REQUIRED:
                continue
            request_id = (
                coverage.supplement_request_id
                or f"supp_req_{safe_folder_slug(folder_name)}_{coverage.beat_id}"
            )
            active_request_ids.add(request_id)
            request = coverage_to_supplement_request(coverage, request_id=request_id)
            if request is not None and request.supplement_request_id not in existing_ids:
                new_requests.append(request)
                existing_ids.add(request.supplement_request_id)

    if active_request_ids:
        keep_statuses = {"ACQUIRED", "DOWNLOADED", "GENERATED"}
        kept_requests = [
            request
            for request in existing.requests
            if request.folder_name != folder_name
            or request.supplement_request_id in active_request_ids
            or request.status in keep_statuses
        ]
        kept_ids = {request.supplement_request_id for request in kept_requests}
        kept_candidates = [
            candidate
            for candidate in existing.candidates
            if candidate.supplement_request_id in kept_ids or candidate.supplement_request_id in active_request_ids
        ]
        if len(kept_requests) != len(existing.requests):
            save_supplement_requests(
                project,
                SupplementRequestsDocument(
                    project_id=project.id,
                    requests=kept_requests,
                    candidates=kept_candidates,
                ),
            )

    if new_requests:
        upsert_requests(project, new_requests)
    return len(new_requests)


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
    created_from_plan = _materialize_requests_from_plan(project, selected_folder)
    document = load_supplement_requests(project)
    folder_requests = requests_for_folder(document, selected_folder)
    required = [req for req in folder_requests if req.status != "ACQUIRED"]

    if created_from_plan:
        st.info(
            f"{created_from_plan} Supplement Request(s) aus dem aktuellen Schnittplan übernommen."
        )

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
            query_key = f"search_query_{request.supplement_request_id}"
            query = st.text_input(
                "Suchbegriffe",
                value=preferred_search_query(request),
                key=query_key,
                help="Kurze Keywords funktionieren besser als ganze Sätze, z. B. Antelope Canyon narrow light.",
            )
            st.caption(f"Verwendete Query: `{query}`")

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
            if request.status == "CANDIDATES_FOUND":
                st.success(f"{len(candidates)} Kandidat(en) gefunden.")
            elif candidates:
                st.info(f"{len(candidates)} gespeicherte Kandidat(en) vorhanden.")
            if st.button("Supplement-Kandidaten suchen", key=f"search_{request.supplement_request_id}"):
                try:
                    request_for_search = request_with_keyword_query(request, query)
                    updated = update_request(
                        project,
                        request.supplement_request_id,
                        selected_source=source,
                        search_queries=request_for_search.search_queries,
                        status="SOURCE_SELECTED",
                    )
                    if updated is None:
                        st.error("Request nicht gefunden.")
                    else:
                        found = search_supplement_candidates(project, updated)
                        st.session_state[f"supplement_search_status_{request.supplement_request_id}"] = (
                            f"{len(found)} Kandidat(en) gefunden mit Query: {query}"
                        )
                        st.rerun()
                except (OSError, ValueError, PermissionError, NotImplementedError) as exc:
                    st.error(str(exc))
            status_message = st.session_state.get(
                f"supplement_search_status_{request.supplement_request_id}"
            )
            if status_message:
                st.success(status_message)

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
                        except (OSError, ValueError, PermissionError, NotImplementedError) as exc:
                            st.error(str(exc))
                elif candidate.provider == SUPPLEMENT_SOURCE_GOOGLE:
                    st.warning(
                        "Google Suche liefert externe Treffer. Für private Nutzung kannst du "
                        "die gefundene Medien-URL automatisch herunterladen."
                    )
                    if candidate.source_page_url:
                        st.link_button("Google-Treffer im Browser öffnen", candidate.source_page_url)
                    if candidate.download_url:
                        st.caption(f"Gefundene Medien-URL: `{candidate.download_url}`")
                    private_ok = st.checkbox(
                        "Ich nutze dieses Google-Asset nur privat und möchte es automatisch herunterladen",
                        key=f"google_private_ok_{request.supplement_request_id}",
                    )
                    if st.button(
                        "Google-Asset automatisch herunterladen",
                        key=f"google_download_{request.supplement_request_id}",
                        disabled=not private_ok or not bool(candidate.download_url),
                    ):
                        try:
                            downloaded = acquire_google_candidate_for_private_use(
                                project,
                                candidate,
                                request,
                            )
                            st.success(f"Google-Asset gespeichert: `{downloaded.local_path}`")
                            st.rerun()
                        except (OSError, ValueError, PermissionError, RuntimeError) as exc:
                            st.error(str(exc))
                    st.caption("Alternativ kannst du weiterhin eine manuell heruntergeladene Datei übernehmen.")
                    manual_path = st.text_input(
                        "Lokaler Pfad nach manuellem Download",
                        key=f"manual_path_{request.supplement_request_id}",
                        placeholder="/Users/.../Downloads/asset.mp4",
                    )
                    rights_ok = st.checkbox(
                        "Rechte geprüft / Nutzung freigegeben",
                        key=f"manual_rights_{request.supplement_request_id}",
                    )
                    if st.button(
                        "Manuell heruntergeladene Datei übernehmen",
                        key=f"manual_import_{request.supplement_request_id}",
                    ):
                        try:
                            imported = import_manual_supplement_asset(
                                project,
                                request=request,
                                source_path=Path(manual_path).expanduser(),
                                source_url=candidate.source_page_url,
                                rights_status="APPROVED" if rights_ok else "NEEDS_LICENSE_REVIEW",
                            )
                            st.success(f"Manuelles Asset übernommen: `{imported.local_path}`")
                            st.rerun()
                        except (OSError, ValueError, PermissionError, NotImplementedError) as exc:
                            st.error(str(exc))
                elif st.button(
                    "Ausgewähltes Asset herunterladen/generieren",
                    key=f"acquire_{request.supplement_request_id}",
                ):
                    try:
                        asset = acquire_supplement_candidate(project, candidate, request)
                        st.success(f"Asset gespeichert: `{asset.local_path}`")
                        st.rerun()
                    except (OSError, ValueError, PermissionError, NotImplementedError) as exc:
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
