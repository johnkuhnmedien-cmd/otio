"""Enhanced pipeline step: map cards after confirmed dramaturgy."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from otio_app.services.job_registry import any_job_running
from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    GeocodeProgress,
    lookup_missing_coordinates,
)
from otio_app.services.without_voiceover_enhanced.maps.map_render_job import (
    JobStatus as MapJobStatus,
    get_map_render_job_manager,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_LABELS,
    COORDINATE_STATUS_MISSING,
    COORDINATE_STATUS_NEEDS_REVIEW,
    MAP_HEADING_BY_LANGUAGE,
    MAP_RESOLUTION_4K,
    MAP_RESOLUTION_HD,
    RENDER_STATUS_BLOCKED,
    RENDER_STATUS_LABELS,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    MapPlanError,
    build_map_plan,
    clamp_max_parallel,
    confirm_map_place_coordinates,
    dramaturgy_fingerprint,
    load_map_coordinates,
    load_map_plan,
    load_map_settings,
    map_heading,
    rebuild_saved_map_plan,
    save_map_coordinates,
    save_map_plan,
    save_map_settings,
    status_after_saving_coordinates,
    unique_chapter_places,
)
from otio_app.services.without_voiceover_enhanced.maps.render_service import MapRenderer
from otio_app.services.without_voiceover_enhanced.paths import map_output_dir
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.ui.polling import poll_while_running
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project

_RES_OPTIONS = (MAP_RESOLUTION_HD, MAP_RESOLUTION_4K)


def _parse_optional_float(value: object) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _init_settings_keys(project_id: str, stored: MapRenderSettings) -> None:
    res_key = f"enh_map_resolution_{project_id}"
    par_key = f"enh_map_parallel_{project_id}"
    veh_key = f"enh_map_vehicle_{project_id}"
    if res_key not in st.session_state:
        st.session_state[res_key] = (
            stored.resolution if stored.resolution in _RES_OPTIONS else MAP_RESOLUTION_HD
        )
    if veh_key not in st.session_state:
        st.session_state[veh_key] = bool(stored.show_vehicle)
    if par_key not in st.session_state:
        resolution = str(st.session_state.get(res_key) or MAP_RESOLUTION_HD)
        st.session_state[par_key] = clamp_max_parallel(resolution, stored.max_parallel)


def _settings_from_session(project_id: str, stored: MapRenderSettings) -> MapRenderSettings:
    resolution = str(st.session_state.get(f"enh_map_resolution_{project_id}") or stored.resolution)
    max_parallel = clamp_max_parallel(
        resolution,
        int(st.session_state.get(f"enh_map_parallel_{project_id}", stored.max_parallel) or stored.max_parallel),
    )
    return MapRenderSettings(
        resolution=resolution if resolution in _RES_OPTIONS else MAP_RESOLUTION_HD,
        max_parallel=max_parallel,
        show_vehicle=bool(st.session_state.get(f"enh_map_vehicle_{project_id}", stored.show_vehicle)),
    )


def render_enhanced_maps_page() -> None:
    st.header("③½ Karten")
    st.caption(
        "Plant Eröffnungs- und Übergangskarten aus der bestätigten Dramaturgie. "
        "Rendern startet hier per Klick oder im Auto-Lauf nach dem Funnel "
        "(Plan, Koordinaten prüfen/bestätigen, alle Karten rendern)."
    )
    project = get_enhanced_project()
    if project is None:
        return

    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is None:
        st.warning("Keine bestätigte Dramaturgie. Bitte zuerst ③ Dramaturgie bestätigen.")
        return

    stored_settings = load_map_settings(project)
    _init_settings_keys(project.id, stored_settings)
    coordinates = load_map_coordinates(project)
    saved_plan = load_map_plan(project)
    live_fp = dramaturgy_fingerprint(confirmed)
    enabled = sum(1 for folder in confirmed.recommended_folder_order if folder.enabled)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Sprache", confirmed.language)
    with col_b:
        st.metric("Kapitel (aktiviert)", enabled)
    with col_c:
        st.metric("Kartenüberschrift", map_heading(confirmed.language))
    st.caption(f"Ausgabeordner: `{map_output_dir(project)}`")
    st.caption(
        "Dateinamen nutzen den Originalkapitelnamen aus der Dramaturgie, "
        "sichtbarer Text folgt der Projektsprache."
    )

    if saved_plan is not None and saved_plan.dramaturgy_fingerprint != live_fp:
        st.warning(
            "Die bestätigte Dramaturgie hat sich geändert. Der gespeicherte Kartenplan "
            "wird nicht automatisch überschrieben. Klicken Sie auf "
            "„Kartenplan aus Dramaturgie erzeugen“, um neu zu planen."
        )

    st.subheader("Render-Einstellungen")
    st.selectbox(
        "Auflösung",
        options=list(_RES_OPTIONS),
        format_func=lambda value: (
            "HD (1920×1080)" if value == MAP_RESOLUTION_HD else "4K (3840×2160)"
        ),
        key=f"enh_map_resolution_{project.id}",
    )
    settings = _settings_from_session(project.id, stored_settings)
    max_cap = 2 if settings.resolution == MAP_RESOLUTION_4K else 4
    current_parallel = clamp_max_parallel(settings.resolution, settings.max_parallel)
    par_key = f"enh_map_parallel_{project.id}"
    existing_parallel = int(st.session_state.get(par_key) or current_parallel)
    if existing_parallel != current_parallel:
        st.session_state[par_key] = current_parallel
    st.number_input(
        "Maximale Parallelität",
        min_value=1,
        max_value=max_cap,
        step=1,
        key=par_key,
        help="HD höchstens 4, 4K höchstens 2.",
    )
    st.checkbox("Fahrzeuge auf der Route anzeigen", key=f"enh_map_vehicle_{project.id}")
    settings = _settings_from_session(project.id, stored_settings)
    if st.button("Einstellungen speichern", key=f"enh_map_save_settings_{project.id}"):
        save_map_settings(project, settings)
        st.success("Einstellungen gespeichert.")

    st.subheader("Kartenplan")
    if st.button("Kartenplan aus Dramaturgie erzeugen", key=f"enh_map_build_plan_{project.id}"):
        try:
            plan = build_map_plan(
                project,
                settings=settings,
                coordinates=coordinates,
                previous=saved_plan,
            )
            save_map_plan(project, plan)
            saved_plan = plan
            st.success(f"{len(plan.maps)} Karten geplant. Nichts gerendert.")
        except MapPlanError as exc:
            st.error(str(exc))
            return

    plan = saved_plan
    if plan is None:
        st.info("Noch kein Kartenplan gespeichert. Erzeugen Sie ihn mit dem Button oben.")
        return

    rows = []
    job_state = get_map_render_job_manager().get_state(project.id)
    runtime_by_id = job_state.items if job_state is not None else {}
    for item in plan.maps:
        runtime = runtime_by_id.get(item.chapter_id)
        status = runtime.status if runtime is not None else item.render_status
        progress = runtime.progress if runtime is not None else item.progress
        error = (runtime.error if runtime is not None else item.error_detail) or item.blocked_reason
        rows.append(
            {
                "Nr.": item.chapter_ordinal,
                "Kapitel-ID": item.chapter_id,
                "Originalname": item.original_chapter_label,
                "Sichtbarer Name": item.localized_display_label,
                "Modus": item.animation_mode,
                "Von": item.from_original_chapter_label,
                "Koordinaten": COORDINATE_STATUS_LABELS.get(
                    item.coordinate_status, item.coordinate_status
                ),
                "Status": RENDER_STATUS_LABELS.get(status, status),
                "Fortschritt": f"{int(round(progress * 100))}%",
                "Dateiname": item.output_filename,
                "Datei": item.output_path or (runtime.output_path if runtime else ""),
                "Hinweis": error,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Koordinaten")
    st.caption(
        "Vorhandene Werte aus dem Projekt werden bevorzugt. "
        "Schon gefundene Orte werden nicht erneut bei Nominatim abgefragt. "
        "Unsichere Treffer rendern nicht automatisch — bitte mit "
        "„Koordinaten bestätigen“ oder „Koordinaten speichern“ freigeben."
    )
    geocode_note_key = f"enh_map_geocode_note_{project.id}"
    geocode_note = st.session_state.get(geocode_note_key)
    if geocode_note:
        kind, text = geocode_note
        if kind == "success":
            st.success(text)
        else:
            st.warning(text)
    places = unique_chapter_places(plan)
    for chapter_id, original, display in places:
        rec = coordinates.places.get(chapter_id) or MapCoordinateRecord(
            chapter_id=chapter_id,
            original_label=original,
            display_label=display,
            status=COORDINATE_STATUS_MISSING,
            country_context=plan.country,
        )
        display_key = f"enh_map_disp_{project.id}_{chapter_id}"
        lat_key = f"enh_map_lat_{project.id}_{chapter_id}"
        lon_key = f"enh_map_lon_{project.id}_{chapter_id}"
        if display_key not in st.session_state:
            st.session_state[display_key] = rec.display_label or display
        if lat_key not in st.session_state:
            st.session_state[lat_key] = (
                "" if rec.latitude is None else f"{rec.latitude:.6f}"
            )
        if lon_key not in st.session_state:
            st.session_state[lon_key] = (
                "" if rec.longitude is None else f"{rec.longitude:.6f}"
            )
        with st.expander(
            f"{original} ({chapter_id})",
            expanded=rec.status
            in {COORDINATE_STATUS_MISSING, COORDINATE_STATUS_NEEDS_REVIEW},
        ):
            st.text_input("Sichtbarer Name (lokalisiert)", key=display_key)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.text_input("Breite", key=lat_key)
            with c2:
                st.text_input("Länge", key=lon_key)
            with c3:
                st.write(
                    "Status: "
                    + COORDINATE_STATUS_LABELS.get(rec.status, rec.status)
                )
                st.write(f"Quelle: {rec.source or '—'}")
                st.write(f"Konfidenz: {rec.confidence:.2f}")
            form_lat = _parse_optional_float(st.session_state.get(lat_key))
            form_lon = _parse_optional_float(st.session_state.get(lon_key))
            if rec.status == COORDINATE_STATUS_NEEDS_REVIEW and rec.has_coordinates:
                st.caption(
                    "Koordinaten sind gefunden, müssen aber bestätigt werden, "
                    "bevor die Karte gerendert werden darf."
                )
            if st.button(
                "Koordinaten bestätigen",
                key=f"enh_map_confirm_{project.id}_{chapter_id}",
                disabled=form_lat is None or form_lon is None,
            ):
                display_new = (
                    str(st.session_state.get(display_key) or display).strip()
                    or original
                )
                try:
                    confirm_map_place_coordinates(
                        project,
                        chapter_id=chapter_id,
                        original_label=original,
                        display_label=display_new,
                        latitude=form_lat,
                        longitude=form_lon,
                        note=rec.note,
                        settings=settings,
                        previous=plan,
                    )
                except MapPlanError as exc:
                    st.warning(str(exc))
                else:
                    st.session_state.pop(lat_key, None)
                    st.session_state.pop(lon_key, None)
                    st.session_state.pop(display_key, None)
                    st.session_state[geocode_note_key] = (
                        "success",
                        f"„{original}“ bestätigt. Kartenplan aktualisiert.",
                    )
                    st.rerun()

    save_c1, save_c2 = st.columns(2)
    with save_c1:
        if st.button("Koordinaten speichern", key=f"enh_map_save_coords_{project.id}"):
            next_places = dict(coordinates.places)
            for chapter_id, original, display in places:
                rec = coordinates.places.get(chapter_id)
                display_new = str(
                    st.session_state.get(f"enh_map_disp_{project.id}_{chapter_id}") or display
                ).strip() or original
                lat = _parse_optional_float(
                    st.session_state.get(f"enh_map_lat_{project.id}_{chapter_id}")
                )
                lon = _parse_optional_float(
                    st.session_state.get(f"enh_map_lon_{project.id}_{chapter_id}")
                )
                status, confidence, source = status_after_saving_coordinates(
                    lat, lon, rec
                )
                next_places[chapter_id] = MapCoordinateRecord(
                    chapter_id=chapter_id,
                    original_label=original,
                    display_label=display_new,
                    latitude=lat,
                    longitude=lon,
                    confidence=confidence,
                    status=status,
                    source=source,
                    country_context=plan.country,
                    note=rec.note if rec is not None else "",
                )
            next_coords = MapCoordinatesDocument(
                project_id=project.id,
                country=plan.country,
                places=next_places,
            )
            save_map_coordinates(project, next_coords)
            rebuild_saved_map_plan(
                project,
                settings=settings,
                coordinates=next_coords,
                previous=plan,
            )
            st.session_state[geocode_note_key] = (
                "success",
                "Koordinaten gespeichert und bestätigt. Kartenplan aktualisiert.",
            )
            st.rerun()
    with save_c2:
        if st.button("Fehlende Koordinaten prüfen", key=f"enh_map_geocode_{project.id}"):
            progress_bar = st.progress(0.0)
            status_box = st.empty()
            seen: list[str] = []

            def on_progress(event: GeocodeProgress) -> None:
                seen.append(event.message)
                progress_bar.progress(event.fraction)
                status_box.info(event.message)

            _coords, rebuilt, errors = lookup_missing_coordinates(
                project,
                settings=settings,
                plan=plan,
                coordinates=coordinates,
                on_progress=on_progress,
            )
            save_map_plan(project, rebuilt)
            found = sum(1 for item in seen if item.endswith(": gefunden"))
            skipped = sum(1 for item in seen if item.endswith(": bereits vorhanden"))
            if errors:
                note = (
                    f"Koordinatenprüfung: {found} gefunden, "
                    f"{skipped} übersprungen, {len(errors)} ohne Ergebnis.\n"
                    + "\n".join(errors)
                )
                st.session_state[geocode_note_key] = ("warning", note)
            else:
                if not seen or (len(seen) == 1 and "schon Koordinaten" in seen[0]):
                    note = "Alle Orte haben schon Koordinaten."
                else:
                    note = (
                        f"Koordinatenprüfung fertig: {found} gefunden"
                        + (f", {skipped} aus dem Cache." if skipped else ".")
                    )
                st.session_state[geocode_note_key] = ("success", note)
            for chapter_id, _original, _display in places:
                rec = _coords.places.get(chapter_id)
                if rec is None or not rec.has_coordinates:
                    continue
                st.session_state.pop(f"enh_map_lat_{project.id}_{chapter_id}", None)
                st.session_state.pop(f"enh_map_lon_{project.id}_{chapter_id}", None)
                st.session_state.pop(f"enh_map_disp_{project.id}_{chapter_id}", None)
            st.rerun()

    st.subheader("Rendern")
    readiness = MapRenderer().readiness()
    if not readiness["ready"]:
        missing = [
            name for name, ok in readiness["checks"].items() if not ok
        ]
        st.warning(
            "Kartenrenderer ist noch nicht bereit ("
            + ", ".join(missing)
            + "). Im Ordner des Vendored Remotion-Renderers einmal `npm ci` ausführen."
        )
    manager = get_map_render_job_manager()
    running = manager.is_running(project.id)
    other_running = (not running) and any_job_running(project.id, reconcile=False)
    start_disabled = running or other_running or not readiness["ready"]

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button(
            "Alle Karten rendern",
            disabled=start_disabled,
            key=f"enh_map_render_all_{project.id}",
        ):
            if not manager.start(project, mode="all"):
                st.warning("Keine renderbaren Karten (Koordinaten prüfen).")
    with col_b:
        if st.button(
            "Nur fehlende/fehlerhafte Karten rendern",
            disabled=start_disabled,
            key=f"enh_map_render_missing_{project.id}",
        ):
            if not manager.start(project, mode="missing"):
                st.warning("Keine fehlenden oder fehlerhaften Karten.")

    st.markdown("**Einzelrender**")
    for item in plan.maps:
        disabled = start_disabled or item.render_status == RENDER_STATUS_BLOCKED
        if st.button(
            f"„{item.original_chapter_label}“ erneut rendern",
            disabled=disabled,
            key=f"enh_map_render_one_{project.id}_{item.chapter_ordinal}",
        ):
            if not manager.start(project, mode="one", chapter_id=item.chapter_id):
                st.warning("Diese Karte kann nicht gerendert werden.")
        if item.output_path:
            st.caption(f"Datei: `{item.output_path}`")
        if item.error_detail:
            st.caption(f"Fehler: {item.error_detail}")

    running = manager.is_running(project.id)
    with col_c:
        if st.button(
            "Render abbrechen",
            disabled=not running,
            key=f"enh_map_cancel_{project.id}",
        ):
            manager.request_cancel(project.id)

    def _render_live_status() -> None:
        state = manager.get_state(project.id)
        if state is None or state.status != MapJobStatus.RUNNING:
            return
        extra = " **(Stop angefordert …)**" if state.cancel_requested else ""
        st.info((state.message or "Karten werden gerendert.") + extra)
        st.progress(min(max(state.overall_progress, 0.0), 1.0))
        if st.button(
            "Render abbrechen",
            disabled=state.cancel_requested,
            key=f"enh_map_cancel_live_{project.id}",
        ):
            manager.request_cancel(project.id)
        for chapter_id, runtime in state.items.items():
            label = RENDER_STATUS_LABELS.get(runtime.status, runtime.status)
            st.caption(
                f"{chapter_id}: {label} · {int(round(runtime.progress * 100))}%"
                + (f" — {runtime.error}" if runtime.error else "")
            )

    if manager.is_running(project.id):
        poll_while_running(
            _render_live_status,
            lambda: manager.is_running(project.id),
            refresh_key=f"enh_map_render_refresh_{project.id}",
        )
    else:
        state = manager.get_state(project.id)
        if state is not None and state.status == MapJobStatus.FAILED:
            st.error(state.error or state.message or "Kartenrender fehlgeschlagen.")
        elif state is not None and state.status == MapJobStatus.CANCELLED:
            st.warning(
                state.message
                or "Kartenrender abgebrochen. Fertige Dateien bleiben. "
                "Mit „Nur fehlende/fehlerhafte Karten rendern“ fortsetzen."
            )
        elif state is not None and state.status == MapJobStatus.COMPLETED:
            st.success(state.message or "Kartenrender fertig.")

    st.caption(
        "Unterstützte Kartenüberschriften: "
        + ", ".join(f"{key}={value}" for key, value in MAP_HEADING_BY_LANGUAGE.items())
    )
