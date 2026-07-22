"""Schritt 8 Enhanced: Final Output — OTIO nur aus aufgelöster Timeline."""

from __future__ import annotations

import streamlit as st

from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import ResolvedTimelineDocument
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    collect_export_blockers,
    export_otio_from_resolved_timeline,
    export_portable_otio_package,
)
from otio_app.services.without_voiceover_enhanced.paths import resolved_timeline_path
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_final_output_page() -> None:
    st.header("⑧ Final Output (Enhanced)")
    st.caption(
        "Keine neue redaktionelle Planung. Lädt freigegebenen finalen Cut Plan / "
        "technisch aufgelöste Timeline und erzeugt OTIO. "
        "Produktions-Export prüft reale Medien und Source-Ranges fail-closed "
        "und erzeugt ein portables Medienpaket mit eindeutigen Dateinamen."
    )
    project = get_enhanced_project()
    if project is None:
        return

    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        st.error(
            "Keine aufgelöste Timeline vorhanden — zuerst Schritt 7 "
            "(Finalen Cut Plan erzeugen und technisch auflösen)."
        )
        return

    export_blockers = collect_export_blockers(project, resolved)
    has_errors = bool(export_blockers)
    st.success(
        f"Skript `{resolved.script_version}` · "
        f"{resolved.total_duration_seconds:.2f}s · "
        f"{len(resolved.shots)} Shots · {len(resolved.audio_segments)} Audio-Segmente · "
        f"Vorlauf {resolved.voiceover_preroll_sec:.2f}s · "
        f"Nachlauf {resolved.voiceover_postroll_sec:.2f}s"
    )
    risk_ack = False
    if has_errors:
        st.error(
            f"Export-Gate: {len(export_blockers)} Fehler — "
            "Produktions-Export ist gesperrt, bis du die Risiken bestätigst. "
            "Ungültige Clips können danach als Gaps exportiert werden."
        )
        with st.expander(
            f"Alle Export-Fehler ({len(export_blockers)})",
            expanded=True,
        ):
            for idx, err in enumerate(export_blockers, start=1):
                st.write(f"{idx}. {err}")
        risk_ack = st.checkbox(
            "Ich habe die Fehler gelesen und bin mir der Risiken bewusst "
            "(Export trotzdem — ungültige Clips können Gaps sein).",
            key=f"enh_otio_risk_ack_{project.id}",
        )

    show_detail_key = f"enh_otio_show_detail_{project.id}"
    st.checkbox("Shot-/Audio-Details laden", key=show_detail_key)
    if st.session_state.get(show_detail_key):
        for segment in resolved.audio_segments:
            st.caption(
                f"Audio {segment.segment_id}: "
                f"{segment.timeline_start_seconds:.2f}–"
                f"{segment.timeline_end_seconds:.2f} "
                f"(pause {segment.pause_after_seconds:.2f}s)"
            )
        for shot in resolved.shots:
            st.caption(
                f"Video {shot.shot_id}: "
                f"{shot.timeline_start_seconds:.2f}–"
                f"{shot.timeline_end_seconds:.2f} · "
                f"{shot.asset_id} · `{shot.resolved_media_path}` · "
                f"src {shot.source_start_seconds:.3f}–{shot.source_end_seconds:.3f}"
            )

    basename = st.text_input(
        "Export-Basename (Paketordner)",
        value=f"{project.name}_enhanced",
        key=f"enh_otio_name_{project.id}",
    )
    export_disabled = has_errors and not risk_ack
    force_export = has_errors and risk_ack
    if st.button(
        "Portablen Export trotz Fehler erzeugen"
        if force_export
        else "Portablen Produktions-Export erzeugen",
        type="primary",
        key=f"enh_otio_export_portable_{project.id}",
        disabled=export_disabled,
        help=(
            "Fail-closed: Fehlerliste lesen und Risiko-Checkbox setzen."
            if export_disabled
            else (
                "Risiko-Override: portables Paket trotz Fehler "
                "(ungültige Clips → Gaps)."
                if force_export
                else "Portables Paket (timeline.otio + media/ + Manifest)."
            )
        ),
    ):
        try:
            package_dir = export_portable_otio_package(
                project,
                basename=basename.strip() or "enhanced",
                allow_errors=force_export,
            )
            media_dir = package_dir / "media"
            media_files = sorted(p.name for p in media_dir.glob("*") if p.is_file())
            if force_export:
                st.warning(
                    f"Risiko-Override-Paket: `{package_dir}` "
                    f"({len(media_files)} Medien, {len(export_blockers)} bekannte "
                    "Blocker) — in Resolve prüfen."
                )
            else:
                st.success(
                    f"Portables Produktionspaket geschrieben: `{package_dir}` "
                    f"({len(media_files)} Medien)."
                )
            st.caption("`timeline.otio` · `media_manifest.json` · `media/`")
            with st.expander("Paketmedien anzeigen", expanded=True):
                for name in media_files:
                    st.write(f"- `{name}`")
        except EnhancedOtioExportError as exc:
            st.error(str(exc))

    st.markdown("**Lokale Einzel-OTIO (nicht portabel)**")
    st.caption(
        "Schreibt nur eine `.otio` mit absoluten Quellpfaden — nicht für "
        "Transfer auf einen anderen Rechner. Für Resolve-Import das portable Paket nutzen."
    )
    if st.button(
        "Lokale OTIO trotz Fehler erzeugen"
        if force_export
        else "Lokale Produktions-OTIO erzeugen",
        key=f"enh_otio_export_{project.id}",
        disabled=export_disabled,
    ):
        try:
            path = export_otio_from_resolved_timeline(
                project,
                basename=basename.strip() or "enhanced",
                allow_errors=force_export,
            )
            if force_export:
                st.warning(
                    f"Lokale Risiko-Override-OTIO: `{path}` · "
                    f"{len(export_blockers)} bekannte Blocker"
                )
            else:
                st.success(f"Lokale Produktions-OTIO geschrieben: `{path}`")
        except EnhancedOtioExportError as exc:
            st.error(str(exc))

    st.markdown("**Test-/Diagnose-Export (mit Lücken)**")
    st.caption(
        "Nicht für DaVinci-Resolve-Produktion. Schreibt eine OTIO mit Gaps für "
        "fehlende/ungültige Shots (`allow_errors=True`). Nur zum Anschauen."
    )
    test_name = (basename.strip() or "enhanced") + "_preview_gaps"
    if st.button(
        "Test-OTIO mit Lücken erzeugen",
        key=f"enh_otio_export_gaps_{project.id}",
    ):
        try:
            path = export_otio_from_resolved_timeline(
                project,
                basename=test_name,
                allow_errors=True,
            )
            st.warning(
                f"Diagnose-OTIO geschrieben: `{path}` · "
                f"{len(resolved.shots)} Shots · "
                f"nicht für Produktion verwenden"
            )
        except EnhancedOtioExportError as exc:
            st.error(str(exc))
