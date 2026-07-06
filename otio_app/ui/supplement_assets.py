"""Streamlit-UI: Supplement Assets zwischen Zuordnung und Schnittplan."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import (
    EditPlanDocument,
    SupplementCandidate,
    SupplementRequest,
    SupplementRequestsDocument,
)
from otio_app.defaults import (
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_GOOGLE,
    SUPPLEMENT_SOURCE_LABELS,
    SUPPLEMENT_SOURCE_MANUAL,
    SUPPLEMENT_SOURCE_NANO_BANANA,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.services.supplement_sources.base import ProviderReadiness
from otio_app.project_layout import safe_folder_slug
from otio_app.services.edit_plan_builder import load_edit_plan
from otio_app.services.edit_plan_rules import save_edit_plan_rules
from otio_app.services.generic_outro_selector import section_id_for_folder
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED, coverage_to_supplement_request
from otio_app.services.supplement_pipeline import (
    acquire_supplement_candidate,
    acquire_top_candidates,
    acquire_top_candidates_for_folder,
    analyze_and_update_inventory_for_folder,
    analyze_supplement_asset,
    approve_adobe_candidate,
    import_manual_supplement_asset,
    mark_edit_plans_stale_for_folder,
    replan_folder_after_supplement,
    run_coverage_for_folder,
    run_full_supplement_pipeline_for_folder,
    search_supplement_candidates,
)
from otio_app.services.supplement_search import (
    build_pexels_primary_query,
    build_pexels_query_variants,
    preferred_search_query,
    request_with_keyword_query,
)
from otio_app.services.supplement_sources import get_provider_readiness, list_provider_readiness
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


def _candidates_for_source(
    candidates: list[SupplementCandidate],
    *,
    request_id: str,
    selected_source: str,
    demo_mode: bool = False,
) -> list[SupplementCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.supplement_request_id == request_id
        and candidate.provider == selected_source
        and (demo_mode or (not candidate.is_mock and candidate.status != "CANDIDATE_MOCK_ONLY"))
    ]


def _provider_tab_label(provider: str, readiness: ProviderReadiness) -> str:
    label = SUPPLEMENT_SOURCE_LABELS.get(provider, provider)
    if readiness.status == "READY":
        marker = "✅ READY"
    elif provider == SUPPLEMENT_SOURCE_GOOGLE:
        marker = "🔎 Discovery"
    elif readiness.status == "CONFIG_MISSING":
        marker = "⚠️ CONFIG"
    elif readiness.status == "ERROR":
        marker = "🔴 ERROR"
    else:
        marker = "🟡 Mock"
    return f"{label} {marker}"


def _status_chain(request: SupplementRequest) -> str:
    steps = [
        ("Request erstellt", True),
        ("Quelle gewählt", request.status not in {"PENDING_SOURCE_SELECTION"}),
        ("Kandidaten gefunden", request.status in {
            "CANDIDATES_FOUND",
            "ASSET_ACQUIRED",
            "ANALYSIS_PENDING",
            "ANALYSIS_COMPLETE",
            "INVENTORY_UPDATED",
            "READY_FOR_REPLAN",
        }),
        ("Asset übernommen", request.status in {
            "ASSET_ACQUIRED",
            "ANALYSIS_PENDING",
            "ANALYSIS_COMPLETE",
            "INVENTORY_UPDATED",
            "READY_FOR_REPLAN",
        }),
        ("Analyse fertig", request.status in {
            "ANALYSIS_COMPLETE",
            "INVENTORY_UPDATED",
            "READY_FOR_REPLAN",
        }),
        ("Inventory aktualisiert", request.status in {"INVENTORY_UPDATED", "READY_FOR_REPLAN"}),
        ("Schnittplan neu vorschlagen", request.status == "READY_FOR_REPLAN"),
    ]
    return " → ".join(("✅ " if done else "⬜ ") + label for label, done in steps)


def _query_contains_location(query: str, request: SupplementRequest) -> bool:
    location = (request.location_name or request.folder_name).strip()
    return bool(location and location.casefold() in query.casefold())


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
                    location_name=folder_name,
                    search_context=shot.motif or passage,
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

    # Bereinigung läuft immer, auch wenn active_request_ids leer ist — z. B.
    # wenn nach einem Neu-Vorschlag alle Beats des Ordners jetzt ein gutes
    # lokales Asset haben. Sonst blieben veraltete Supplement Requests für
    # bereits gelöste Beats für immer sichtbar/aktiv.
    # Nur komplett unberührte Requests (noch keine Quelle gewählt) werden
    # entfernt — alles, woran der Nutzer schon gearbeitet hat, bleibt erhalten.
    untouched_status = "PENDING_SOURCE_SELECTION"
    kept_requests = [
        request
        for request in existing.requests
        if request.folder_name != folder_name
        or request.supplement_request_id in active_request_ids
        or request.status != untouched_status
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


def _save_source_and_query(project, request: SupplementRequest, provider: str, query: str) -> SupplementRequest | None:
    request_for_search = request_with_keyword_query(request, query)
    return update_request(
        project,
        request.supplement_request_id,
        selected_source=provider,
        search_queries=request_for_search.search_queries,
        query_used=request_for_search.search_queries.get("en", [query])[0],
        status="SOURCE_SELECTED",
    )


def _render_query_controls(request: SupplementRequest, provider: str) -> str:
    location = request.location_name or request.folder_name
    st.caption(f"Ort / location_name: **{location}**")
    generated = _default_query_for_provider(request, provider)
    query = st.text_input(
        "Suchquery",
        value=generated,
        key=f"query_{request.supplement_request_id}_{provider}",
    )
    if location and location.casefold() not in query.casefold():
        st.warning("Die Query enthält den Ortsnamen nicht. Produktive Suche sollte den Ort enthalten.")
    return query


def _default_query_for_provider(request: SupplementRequest, provider: str) -> str:
    if provider == SUPPLEMENT_SOURCE_PEXELS:
        return build_pexels_primary_query(request)
    return preferred_search_query(request)


def _render_pexels_candidate_card(project, request: SupplementRequest, candidate: SupplementCandidate, index: int) -> None:
    media_label = "Video" if candidate.media_type == "video" else "Foto"
    with st.expander(
        f"{index + 1}. {media_label} · {candidate.title[:80]} · "
        f"{candidate.width}×{candidate.height} · {candidate.location_match}",
        expanded=index == 0,
    ):
        if candidate.preview_url:
            st.image(candidate.preview_url, caption="Preview")
        st.caption(f"Query: `{candidate.query_used}`")
        if candidate.media_type == "video":
            st.caption(
                f"Creator: {candidate.creator or '—'} · Dauer: {candidate.duration_sec:.1f}s · "
                f"Auflösung: {candidate.width}×{candidate.height} · "
                f"Aspect Ratio: {candidate.aspect_ratio:.3f} · "
                f"16:9: {'PASS' if candidate.is_16_9 else 'FAIL'}"
            )
            st.caption(
                f"Download-Datei: {candidate.selected_video_file_width}×{candidate.selected_video_file_height} "
                f"{candidate.pexels_quality}"
            )
        else:
            st.caption(
                f"Creator: {candidate.creator or '—'} · Foto-Auflösung: {candidate.width}×{candidate.height} · "
                f"Aspect Ratio: {candidate.aspect_ratio:.3f} · Regel: {candidate.aspect_ratio_policy}"
            )
            st.info("Foto wird später als Vintage-Hintergrund mit Bild-Zoom 0.8 geplant.")
        st.caption(f"Location Match: **{candidate.location_match or '—'}**")
        if candidate.source_page_url:
            st.link_button("Pexels-Link öffnen", candidate.source_page_url)
        disabled = not candidate.download_enabled or candidate.location_match == "missing"
        if candidate.location_match == "missing":
            st.warning("Ort fehlt im Kandidaten — keine automatische Nutzung ohne manuelle Freigabe.")
        if st.button(
            "Dieses Asset herunterladen",
            key=f"download_pexels_{candidate.candidate_id}",
            disabled=disabled,
        ):
            try:
                asset = acquire_supplement_candidate(project, candidate, request)
                st.success(f"Asset gespeichert: `{asset.local_path}`")
                st.rerun()
            except (OSError, ValueError, PermissionError, NotImplementedError, RuntimeError) as exc:
                st.error(str(exc))


def _load_pexels_debug_report(project) -> dict | None:
    from otio_app.project_layout import get_pexels_debug_report_path

    path = get_pexels_debug_report_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def _render_pexels_tab(project, request: SupplementRequest, readiness: ProviderReadiness, document: SupplementRequestsDocument) -> None:
    st.markdown("#### Pexels")
    st.caption(f"Status: **{readiness.status}** — {readiness.message}")
    st.caption(
        f"Suchmodus: **{request.required_asset_type}** · "
        "Videos: nur 16:9 · Fotos: prefer_16_9 / Hintergrund möglich"
    )
    debug_report = _load_pexels_debug_report(project)
    with st.expander("Technische Suchdetails", expanded=bool(debug_report and debug_report.get("errors"))):
        st.caption("Video Endpoint: `https://api.pexels.com/v1/videos/search`")
        st.caption("Photo Endpoint: `https://api.pexels.com/v1/search`")
        st.caption("Video orientation: `landscape`, per_page: `15`")
        if debug_report and debug_report.get("request_id") == request.supplement_request_id:
            st.caption(f"Queries versucht: {', '.join(debug_report.get('queries_attempted', [])) or '—'}")
            st.caption(
                f"Raw Videos: {debug_report.get('raw_video_result_count', 0)} · "
                f"Raw Fotos: {debug_report.get('raw_photo_result_count', 0)} · "
                f"Verworfen (Videos): {debug_report.get('rejected_video_count', 0)}"
            )
            statuses = debug_report.get("http_status_by_query", {})
            if statuses:
                st.caption("HTTP-Status pro Query: " + ", ".join(f"`{q}`→{s}" for q, s in statuses.items()))
            errors = debug_report.get("errors", [])
            if errors:
                st.error("Pexels-API-Fehler bei der letzten Suche:")
                for entry in errors:
                    st.caption(
                        f"HTTP {entry.get('status')} · Query `{entry.get('query')}` · {entry.get('message')}"
                    )
    if request.last_error and request.status == "ACQUIRE_FAILED":
        st.error(f"Letzter Fehler: {request.last_error}")
    query = _render_query_controls(request, SUPPLEMENT_SOURCE_PEXELS)
    if readiness.status != "READY":
        st.warning("PEXELS_API_KEY fehlt. Bitte unter Systemstatus/API-Schlüssel oder .env setzen.")
        st.info("Alternative: Manual Import oder Google-Discovery verwenden.")
        return
    if st.button("Pexels-Kandidaten suchen", key=f"search_pexels_{request.supplement_request_id}"):
        updated = _save_source_and_query(project, request, SUPPLEMENT_SOURCE_PEXELS, query)
        if updated is not None:
            found = search_supplement_candidates(project, updated)
            if found:
                st.success(f"{len(found)} echte Pexels-Kandidaten gefunden.")
            else:
                refreshed = requests_for_folder(
                    load_supplement_requests(project), request.folder_name
                )
                current = next(
                    (r for r in refreshed if r.supplement_request_id == request.supplement_request_id),
                    None,
                )
                if current is not None and current.last_error:
                    st.error(f"Pexels-Suche fehlgeschlagen: {current.last_error}")
                else:
                    st.warning("0 echte Pexels-Kandidaten gefunden — siehe Technische Suchdetails.")
            st.rerun()
    candidates = _candidates_for_source(
        document.candidates,
        request_id=request.supplement_request_id,
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    if not candidates:
        st.info("0 echte Pexels-Kandidaten gefunden oder noch nicht gesucht.")
        st.button("Query vereinfachen und erneut suchen", key=f"simplify_pexels_{request.supplement_request_id}", disabled=True)
        st.caption("Du kannst oben eine kürzere Query mit Ortsnamen eintragen und erneut suchen.")
        return

    downloadable = [
        candidate
        for candidate in candidates
        if candidate.download_enabled and not candidate.is_mock and candidate.location_match != "missing"
    ]
    auto_count = min(3, len(downloadable))
    if auto_count > 0:
        if st.button(
            f"Top {auto_count} automatisch herunterladen",
            key=f"auto_download_pexels_{request.supplement_request_id}",
            type="primary",
        ):
            results = acquire_top_candidates(project, candidates, request, max_count=3)
            successes = [r for r in results if r[1] is not None]
            failures = [r for r in results if r[1] is None]
            if successes:
                st.success(
                    f"{len(successes)} Asset(s) automatisch heruntergeladen: "
                    + ", ".join(Path(asset.local_path).name for _c, asset, _e in successes)
                )
            for candidate, _asset, error in failures:
                st.error(f"Download fehlgeschlagen für `{candidate.title[:60]}`: {error}")
            st.rerun()
    for index, candidate in enumerate(candidates):
        _render_pexels_candidate_card(project, request, candidate, index)


def _render_google_tab(project, request: SupplementRequest) -> None:
    st.markdown("#### Google Suche")
    query = _render_query_controls(request, SUPPLEMENT_SOURCE_GOOGLE)
    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    st.link_button("Google-Suche im Browser öffnen", search_url)
    st.info("Google liefert keine automatisch freigegebenen Produktionsassets. Bitte Datei manuell herunterladen.")
    manual_path = st.text_input(
        "Lokalen Pfad nach manuellem Download einfügen",
        key=f"google_manual_path_{request.supplement_request_id}",
        placeholder="/Users/.../Downloads/asset.mp4",
    )
    source_url = st.text_input(
        "Quell-URL / Website",
        value=search_url,
        key=f"google_source_url_{request.supplement_request_id}",
    )
    if st.button("Manuell heruntergeladenes Google-Asset übernehmen", key=f"import_google_{request.supplement_request_id}"):
        try:
            imported = import_manual_supplement_asset(
                project,
                request=request,
                source_path=Path(manual_path).expanduser(),
                source_url=source_url,
                rights_status="NEEDS_LICENSE_REVIEW",
                source_provider=SUPPLEMENT_SOURCE_GOOGLE,
                acquisition_method="manual_download",
            )
            st.success(f"Asset übernommen: `{imported.local_path}`")
            st.rerun()
        except (OSError, ValueError, PermissionError) as exc:
            st.error(str(exc))


def _render_mock_provider_tab(provider: str, readiness: ProviderReadiness, request: SupplementRequest) -> None:
    st.markdown(f"#### {SUPPLEMENT_SOURCE_LABELS.get(provider, provider)}")
    st.caption(f"Status: **{readiness.status}** — {readiness.message}")
    _render_query_controls(request, provider)
    if provider == SUPPLEMENT_SOURCE_NANO_BANANA:
        st.caption(f"Prompt-Vorschlag: {request.generation_prompt or request.visual_requirement or request.passage_text}")
    st.warning("Noch nicht produktiv angebunden. Keine Mock-Dateien werden ins Inventory geschrieben.")


def _render_manual_tab(project, request: SupplementRequest) -> None:
    st.markdown("#### Manual Import")
    destination = Path(project.project_root_path) / request.folder_name / "_supplemental" / "_manual"
    st.caption("Status: **READY** — Manueller Import ist verfügbar.")
    st.caption(f"Zielordner: `{destination}`")
    manual_path = st.text_input(
        "Lokaler Dateipfad",
        key=f"manual_path_{request.supplement_request_id}",
        placeholder="/Users/.../Downloads/asset.mp4",
    )
    source_url = st.text_input("Optionale Source-URL", key=f"manual_source_{request.supplement_request_id}")
    rights_status = st.selectbox(
        "Rechte-/Lizenzstatus",
        options=["APPROVED", "NEEDS_LICENSE_REVIEW", "PRIVATE_ONLY"],
        index=1,
        key=f"manual_rights_status_{request.supplement_request_id}",
    )
    if st.button("Datei übernehmen", key=f"manual_import_{request.supplement_request_id}"):
        try:
            imported = import_manual_supplement_asset(
                project,
                request=request,
                source_path=Path(manual_path).expanduser(),
                source_url=source_url,
                rights_status=rights_status,
                source_provider=SUPPLEMENT_SOURCE_MANUAL,
                acquisition_method="manual_import",
            )
            st.success(f"Asset übernommen: `{imported.local_path}`")
            st.rerun()
        except (OSError, ValueError, PermissionError) as exc:
            st.error(str(exc))


def _render_source_tab(
    *,
    project,
    request: SupplementRequest,
    provider: str,
    readiness: ProviderReadiness,
    document: SupplementRequestsDocument,
) -> None:
    # Wichtig: st.tabs() rendert ALLE Tab-Inhalte im selben Skriptlauf (nicht nur
    # den sichtbaren Tab). `selected_source` darf deshalb NICHT als Nebeneffekt des
    # bloßen Renderns gesetzt werden — sonst gewinnt immer der zuletzt gerenderte
    # Tab (z. B. "manual") und überschreibt eine zuvor bewusst gewählte Quelle wie
    # "pexels". Das Setzen von selected_source passiert ausschließlich durch
    # explizite Nutzeraktionen (Suchen/Download-Button).
    if provider == SUPPLEMENT_SOURCE_PEXELS:
        _render_pexels_tab(project, request, readiness, document)
    elif provider == SUPPLEMENT_SOURCE_GOOGLE:
        _render_google_tab(project, request)
    elif provider == SUPPLEMENT_SOURCE_MANUAL:
        _render_manual_tab(project, request)
    else:
        _render_mock_provider_tab(provider, readiness, request)


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
    with st.expander("Provider-Status", expanded=False):
        for provider, readiness in list_provider_readiness().items():
            st.caption(
                f"**{SUPPLEMENT_SOURCE_LABELS.get(provider, provider)}**: "
                f"{readiness.status} — {readiness.message}"
            )
    created_from_plan = _materialize_requests_from_plan(project, selected_folder)
    document = load_supplement_requests(project)
    folder_requests = requests_for_folder(document, selected_folder)
    required = [req for req in folder_requests if req.status not in {"READY_FOR_REPLAN", "INVENTORY_UPDATED"}]

    if created_from_plan:
        st.info(
            f"{created_from_plan} Supplement Request(s) aus dem aktuellen Schnittplan übernommen."
        )

    st.markdown(
        f"**{len(required)}** offene Supplement-Anforderungen für **{selected_folder}** "
        f"(gesamt pending: {pending_supplement_count(document)})"
    )
    st.caption(
        "Zählt Beats/Anfragen, nicht Shots — ein Beat kann mehrere Shots erzeugen. "
        "Die Shot-Anzahl im Schnittplan-Tab (Vorschlag/Prüfen & Speichern) kann daher "
        "höher sein, u. a. wenn zusätzlich Shots durch Wiederverwendungsregeln "
        "(Max. Asset-Nutzung / Min. Abstand) ohne Asset geblieben sind."
    )

    pexels_readiness_for_folder = get_provider_readiness(SUPPLEMENT_SOURCE_PEXELS)
    auto_col, coverage_col = st.columns(2)
    with auto_col:
        if st.button(
            "Alles automatisch: Top 3 suchen, herunterladen, analysieren, "
            "Inventar aktualisieren & Schnittplan neu vorschlagen",
            key=f"auto_full_{project.id}_{safe_folder_slug(selected_folder)}",
            type="primary",
            disabled=pexels_readiness_for_folder.status != "READY",
        ):
            with st.spinner("Suche, Download, Analyse und Inventory laufen für alle offenen Anfragen …"):
                result = run_full_supplement_pipeline_for_folder(
                    project,
                    selected_folder,
                    max_per_request=3,
                )
            summary = result["downloads"]
            processed = [entry for entry in summary if not entry["skipped"]]
            skipped = [entry for entry in summary if entry["skipped"]]
            st.success(
                f"{result['total_downloaded']} Asset(s) über {len(processed)} Anfrage(n) heruntergeladen "
                f"({len(skipped)} übersprungen) · {result['analyzed']} analysiert · "
                f"{result['inventory_added']} ins Inventory übernommen."
            )
            if result["replanned"]:
                st.success(
                    f"Schnittplan automatisch neu vorgeschlagen: {result['replan_shot_count']} Shots "
                    "(noch nicht bestätigt — bitte unter **③ Schnittplan → Prüfen & Speichern** prüfen)."
                )
            elif result["inventory_added"] and result["replan_error"]:
                st.warning(f"Automatischer Replan fehlgeschlagen: {result['replan_error']}")
            if result["inventory_skipped"]:
                st.warning(
                    f"{len(result['inventory_skipped'])} Asset(s) nicht ins Inventory übernommen "
                    "(Validierung nicht bestanden):"
                )
                for skip_reason in result["inventory_skipped"]:
                    st.caption(f"⚠️ {skip_reason}")
            with st.expander("Details pro Anfrage", expanded=False):
                for entry in summary:
                    if entry["skipped"]:
                        st.caption(f"⏭️ `{entry['supplement_request_id']}`: {entry['reason']}")
                    else:
                        st.caption(
                            f"✅ `{entry['supplement_request_id']}`: "
                            f"{entry['downloaded']}/{entry['candidates_found']} heruntergeladen"
                        )
                        for error in entry["errors"]:
                            st.caption(f"   ⚠️ {error}")
            st.rerun()
    if pexels_readiness_for_folder.status != "READY":
        st.caption("Pexels ist nicht READY — bitte PEXELS_API_KEY setzen.")

    with coverage_col:
        run_coverage_clicked = st.button("Coverage prüfen & Supplement Requests erzeugen", key=f"coverage_{project.id}")
    if run_coverage_clicked:
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

    request_labels = [
        f"{req.beat_id} · {req.status} · {req.visual_requirement[:60] or req.passage_text[:60]}"
        for req in folder_requests
    ]
    selected_request_idx = st.selectbox(
        "Supplement Request",
        options=range(len(folder_requests)),
        format_func=lambda index: request_labels[index],
        key=f"supplement_request_select_{project.id}_{safe_folder_slug(selected_folder)}",
    )
    request = folder_requests[selected_request_idx]

    st.markdown("### Fehlendes Motiv")
    st.write(request.passage_text)
    st.caption(
        f"**Motiv:** {request.visual_requirement or '—'} · "
        f"**Dauer:** {request.duration_needed_sec:.1f}s · "
        f"**Ort:** {request.location_name or request.folder_name}"
    )
    st.caption(f"**Warum Supplement nötig:** {request.reason or '—'}")
    st.caption(_status_chain(request))

    source_order = [
        SUPPLEMENT_SOURCE_PEXELS,
        SUPPLEMENT_SOURCE_GOOGLE,
        SUPPLEMENT_SOURCE_NANO_BANANA,
        SUPPLEMENT_SOURCE_ADOBE,
        SUPPLEMENT_SOURCE_MANUAL,
    ]
    readiness = {provider: get_provider_readiness(provider) for provider in source_order}
    tabs = st.tabs([_provider_tab_label(provider, readiness[provider]) for provider in source_order])
    for provider, tab in zip(source_order, tabs):
        with tab:
            _render_source_tab(
                project=project,
                request=request,
                provider=provider,
                readiness=readiness[provider],
                document=document,
            )

    analyzable_statuses = {"ACQUIRED", "ASSET_ACQUIRED", "ANALYSIS_PENDING"}
    can_analyze = any(req.status in analyzable_statuses for req in folder_requests)
    can_update_inventory = any(req.status == "ANALYSIS_COMPLETE" for req in folder_requests)
    can_replan = any(req.status == "READY_FOR_REPLAN" for req in folder_requests)
    action_col1, action_col2, action_col3 = st.columns(3)
    with action_col1:
        analyze_clicked = st.button(
            "Neue Assets analysieren",
            key=f"analyze_{project.id}",
            disabled=not can_analyze,
        )
    with action_col2:
        inventory_clicked = st.button(
            "Inventory aktualisieren & Schnittplan neu vorschlagen",
            key=f"inventory_{project.id}",
            disabled=not can_update_inventory,
            help=(
                "Übernimmt neue Assets ins Inventory UND schlägt automatisch "
                "einen neuen (noch nicht bestätigten) Schnittplan für diesen "
                "Ort vor."
            ),
        )
    with action_col3:
        replan_clicked = st.button(
            "Schnittplan mit neuen Assets neu vorschlagen",
            key=f"replan_{project.id}",
            disabled=not can_replan,
        )

    if analyze_clicked:
        relevant_statuses = analyzable_statuses | {"ANALYSIS_COMPLETE"}
        acquired = [req for req in folder_requests if req.status in relevant_statuses]
        if not acquired:
            st.warning("Noch keine heruntergeladenen Supplement-Assets.")
        else:
            processed_dirs: set[Path] = set()
            analyzed_count = 0
            for acquired_request in acquired:
                provider_dir = (
                    Path(project.project_root_path)
                    / selected_folder
                    / "_supplemental"
                    / f"_{acquired_request.selected_source or 'pexels'}"
                )
                if provider_dir in processed_dirs or not provider_dir.is_dir():
                    continue
                processed_dirs.add(provider_dir)
                for media_path in sorted(provider_dir.glob("*")):
                    if media_path.suffix.lower() in {".json"}:
                        continue
                    from otio_app.services.supplement_pipeline import load_sidecar

                    sidecar = load_sidecar(media_path)
                    if sidecar is None:
                        continue
                    analyze_supplement_asset(
                        project,
                        folder_name=selected_folder,
                        local_path=media_path,
                        sidecar=sidecar,
                    )
                    analyzed_count += 1
            if analyzed_count == 0:
                st.warning("Keine analysierbaren Supplement-Assets (Datei + Sidecar) gefunden.")
            else:
                st.success(f"Analyse abgeschlossen für {analyzed_count} Asset(s).")
            st.rerun()

    if inventory_clicked:
        inventory_result = analyze_and_update_inventory_for_folder(
            project, selected_folder, auto_replan=True
        )
        if not inventory_result["touched"]:
            st.warning("Keine analysierbaren Supplement-Assets (Datei + Sidecar) gefunden.")
        elif inventory_result["inventory_added"]:
            st.success(
                f"{inventory_result['inventory_added']} Asset(s) ins Inventory übernommen."
            )
            if inventory_result["replanned"]:
                st.success(
                    f"Schnittplan automatisch neu vorgeschlagen: {inventory_result['replan_shot_count']} "
                    "Shots (noch nicht bestätigt — bitte unter **③ Schnittplan → Prüfen & Speichern** prüfen)."
                )
            elif inventory_result["replan_error"]:
                st.warning(f"Automatischer Replan fehlgeschlagen: {inventory_result['replan_error']}")
        if inventory_result["inventory_skipped"]:
            st.warning(
                f"{len(inventory_result['inventory_skipped'])} Asset(s) nicht ins Inventory übernommen "
                "(Validierung nicht bestanden):"
            )
            for skip_reason in inventory_result["inventory_skipped"]:
                st.caption(f"⚠️ {skip_reason}")
        st.rerun()

    if replan_clicked:
        rules_doc = get_edit_plan_rules_for_project(project)
        save_edit_plan_rules(project, rules_doc)
        # Nutzt dieselbe Logik wie der automatische Replan nach Inventory-
        # Update (persistierte Timing-/Gemini-Einstellungen statt Defaults).
        replan_result = replan_folder_after_supplement(project, selected_folder)
        if replan_result["replanned"]:
            st.success(f"Schnittplan neu vorgeschlagen: {replan_result['shot_count']} Shots.")
        else:
            st.error(replan_result["error"] or "Schnittplan konnte nicht neu vorgeschlagen werden.")

    existing_plan = load_edit_plan(project, selected_folder)
    if existing_plan and existing_plan.inventory_hash_at_plan_time:
        from otio_app.services.inventory_hash import inventory_hash_is_stale, current_folder_inventory_hash

        if inventory_hash_is_stale(
            project,
            selected_folder,
            existing_plan.inventory_hash_at_plan_time,
        ):
            st.warning("Inventory changed — please regenerate cut plan.")
