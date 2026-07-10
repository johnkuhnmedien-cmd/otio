"""Dramaturgie-/Voice-over-Generierungs-Pipeline für "Projekt ohne Voice-Over".

Diese Pipeline ist strikt von der Produktionspipeline (edit_plan_builder.py,
otio_exporter.py) getrennt. Sie darf niemals build_edit_plan(), save_edit_plan()
oder export_otio_timeline() aufrufen und schreibt ausschließlich unter
_otio/voiceover_generation/.
"""

from __future__ import annotations
