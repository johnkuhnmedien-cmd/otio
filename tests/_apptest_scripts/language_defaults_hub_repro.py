"""AppTest-Skript: rendert die Sprachstandards-Seite gegen ein tmp data-Verzeichnis."""

from __future__ import annotations

import os
from pathlib import Path

from otio_app.services.voiceover_generation import (
    dramaturgy_defaults_service,
    elevenlabs_voice_defaults_service,
    intro_hook_defaults_service,
    language_defaults_catalog,
    project_brief_defaults_service,
    style_reference_defaults_service,
)
from otio_app.services.without_voiceover_enhanced import (
    cut_plan_options_defaults_service,
)
from otio_app.ui.voiceover_generation.language_defaults_hub import (
    render_language_defaults_hub_page,
)

_data = Path(os.environ["REPRO_DATA_DIR"])
_data.mkdir(parents=True, exist_ok=True)
for module in (
    project_brief_defaults_service,
    style_reference_defaults_service,
    dramaturgy_defaults_service,
    intro_hook_defaults_service,
    elevenlabs_voice_defaults_service,
    cut_plan_options_defaults_service,
    language_defaults_catalog,
):
    module.ensure_data_dir = lambda: _data

render_language_defaults_hub_page()
