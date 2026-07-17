"""Discovery-V2-UI-Seiten."""

from otio_app.discovery_v2.ui.asset_analysis_page import (
    render_discovery_asset_analysis_page,
)
from otio_app.discovery_v2.ui.inventory_page import render_discovery_inventory_page
from otio_app.discovery_v2.ui.media_intake_page import render_discovery_media_intake_page
from otio_app.discovery_v2.ui.overview import (
    render_discovery_overview_page,
    render_discovery_settings_page,
)
from otio_app.discovery_v2.ui.technical_validation_page import (
    render_discovery_technical_validation_page,
)

__all__ = [
    "render_discovery_asset_analysis_page",
    "render_discovery_inventory_page",
    "render_discovery_media_intake_page",
    "render_discovery_overview_page",
    "render_discovery_settings_page",
    "render_discovery_technical_validation_page",
]
