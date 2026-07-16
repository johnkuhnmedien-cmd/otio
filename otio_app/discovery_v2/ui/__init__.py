"""Discovery-V2-Streamlit-Seiten."""

from __future__ import annotations

from otio_app.discovery_v2.ui.inventory_page import render_discovery_inventory_page
from otio_app.discovery_v2.ui.overview import (
    render_discovery_overview_page,
    render_discovery_settings_page,
)

__all__ = [
    "render_discovery_inventory_page",
    "render_discovery_overview_page",
    "render_discovery_settings_page",
]
