"""Enhanced pipeline step: map cards (plan and coordinates; no render in Phase 1)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    lookup_missing_coordinates,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_MANUAL,
    COORDINATE_STATUS_MISSING,
    MAP_HEADING_BY_LANGUAGE,
    MAP_RESOLUTION_4K,
    MAP_RESOLUTION_HD,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    MapPlanError,
    build_map_plan,
    clamp_max_parallel,
    dramaturgy_fingerprint,
    load_map_coordinates,
    load_map_plan,
    load_map_settings,
    map_heading,
    save_map_coordinates,
    save_map_plan,
    save_map_settings,
    unique_chapter_places,
)
from otio_app.services.without_voiceover_enhanced.paths import map_output_dir
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
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
        "Rendern startet erst nach einem ausdrücklichen Klick (Phase 2)."
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
        help="HD höchstens 4, 4K höchstens 2. Der Renderer kommt in Phase 2.",
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
    for item in plan.maps:
        rows.append(
            {
                "Nr.": item.chapter_ordinal,
                "Kapitel-ID": item.chapter_id,
                "Originalname": item.original_chapter_label,
                "Sichtbarer Name": item.localized_display_label,
                "Modus": item.animation_mode,
                "Von": item.from_original_chapter_label,
                "Koordinaten": item.coordinate_status,
                "Status": item.render_status,
                "Dateiname": item.output_filename,
                "Hinweis": item.blocked_reason,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Koordinaten")
    st.caption(
        "Vorhandene Werte aus dem Projekt werden bevorzugt. "
        "Unsichere Treffer rendern nicht automatisch."
    )
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
        with st.expander(f"{original} ({chapter_id})", expanded=not rec.has_coordinates):
            st.text_input("Sichtbarer Name (lokalisiert)", key=display_key)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.text_input("Breite", key=lat_key)
            with c2:
                st.text_input("Länge", key=lon_key)
            with c3:
                st.write(f"Status: {rec.status}")
                st.write(f"Quelle: {rec.source or '—'}")
                st.write(f"Konfidenz: {rec.confidence:.2f}")

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
                same_point = (
                    rec is not None
                    and rec.latitude == lat
                    and rec.longitude == lon
                    and rec.has_coordinates
                )
                if lat is None or lon is None:
                    status = COORDINATE_STATUS_MISSING
                    confidence = 0.0
                    source = rec.source if rec is not None else ""
                elif same_point:
                    status = rec.status
                    confidence = rec.confidence
                    source = rec.source
                else:
                    status = COORDINATE_STATUS_MANUAL
                    confidence = 1.0
                    source = "manual"
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
            rebuilt = build_map_plan(
                project,
                settings=settings,
                coordinates=next_coords,
                previous=plan,
            )
            save_map_plan(project, rebuilt)
            st.success("Koordinaten gespeichert. Kartenplan aktualisiert.")
    with save_c2:
        if st.button("Fehlende Koordinaten prüfen", key=f"enh_map_geocode_{project.id}"):
            try:
                _coords, rebuilt, errors = lookup_missing_coordinates(
                    project,
                    settings=settings,
                    plan=plan,
                    coordinates=coordinates,
                )
                save_map_plan(project, rebuilt)
                if errors:
                    st.warning(
                        "Einige Orte konnten nicht aufgelöst werden:\n" + "\n".join(errors)
                    )
                else:
                    st.success("Koordinatenprüfung abgeschlossen.")
            except Exception as exc:
                st.error(f"Koordinatenprüfung fehlgeschlagen: {exc}")

    st.subheader("Rendern")
    st.info(
        "Der Renderer (gemeinsames Remotion-Bundle, Live-Fortschritt, ffprobe) folgt in Phase 2. "
        "Bestehende Kartendateien werden nicht überschrieben."
    )
    st.button("Alle Karten rendern", disabled=True, key=f"enh_map_render_all_{project.id}")
    st.button(
        "Nur fehlende/fehlerhafte Karten rendern",
        disabled=True,
        key=f"enh_map_render_missing_{project.id}",
    )
    st.button("Render abbrechen", disabled=True, key=f"enh_map_cancel_{project.id}")
    st.caption(
        "Unterstützte Kartenüberschriften: "
        + ", ".join(f"{key}={value}" for key, value in MAP_HEADING_BY_LANGUAGE.items())
    )
