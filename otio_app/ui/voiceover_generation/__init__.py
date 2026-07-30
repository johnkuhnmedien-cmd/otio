"""UI für "Projekt ohne Voice-Over" — Dramaturgie-/Voice-over-Generierungs-Pipeline.

Diese Seiten sind bewusst von der bestehenden Produktionspipeline
(Zuordnung, Supplement Assets, Schnittplan, OTIO-Export) getrennt. Sie dürfen
niemals `save_edit_plan()`, `build_edit_plan()`, `_set_draft()` oder
`export_otio_timeline()` aufrufen — siehe `tests/voiceover_generation/
test_no_production_edit_plan_calls.py`.
"""

from __future__ import annotations
