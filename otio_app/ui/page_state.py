"""Session-State bereinigen beim Verlassen einer Streamlit-Seite."""

from __future__ import annotations

import streamlit as st

from otio_app.ui.navigation import (
    PAGE_ANALYSIS,
    PAGE_CLEAN_MEDIA,
    PAGE_EDIT_PLAN,
    PAGE_MAPPING,
)

_PAGE_WIDGET_MARKERS: dict[str, tuple[str, ...]] = {
    PAGE_MAPPING: (
        "mapping_folder_",
        "confirm_mapping_",
        "rematch_",
        "save_mapping_",
        "voice_folder_mapping_",
    ),
    PAGE_EDIT_PLAN: (
        "plan_min_",
        "plan_max_",
        "plan_offset_",
        "plan_outro_",
        "plan_split_",
        "plan_gemini_",
        "plan_folder_select_",
        "confirm_plan_",
        "save_plan_",
        "build_plan_",
        "export_audio_offset_",
        "export_section_outro_",
        "otio_export_folders_",
        "export_otio_",
        "edit_plan_active_tab_",
    ),
    PAGE_ANALYSIS: (
        "workbench_",
        "stop_voice_",
        "stop_assets_",
        "dismiss_voice_",
        "dismiss_asset_",
    ),
    PAGE_CLEAN_MEDIA: (
        "clean_folders_",
        "clean_validate_",
        "clean_process_",
        "clean_repair_",
        "clean_all_",
        "clean_cancel_",
        "clean_refresh_",
        "clean_reset_",
        "clean_dismiss",
        "clean_diag_",
        "show_diag_",
    ),
}


def clear_page_widget_state(page: str) -> None:
    """Entfernt Widget-Keys der verlassenen Seite — verhindert Ghost-UI."""
    markers = _PAGE_WIDGET_MARKERS.get(page)
    if not markers:
        return
    for key in list(st.session_state.keys()):
        if any(marker in key for marker in markers):
            del st.session_state[key]
