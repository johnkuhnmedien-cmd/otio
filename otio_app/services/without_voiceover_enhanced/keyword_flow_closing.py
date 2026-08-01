"""Keyword-Flow: Closing Primary + Fallback Validierung."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import UnifiedCutPlanDocument
from otio_app.services.without_voiceover_enhanced.timeline_resolver import AssetCatalog


class KeywordFlowClosingError(ValueError):
    pass


def validate_keyword_flow_closing(
    plan: UnifiedCutPlanDocument,
    *,
    catalog: AssetCatalog | None = None,
) -> list[str]:
    """Prüft Primary/Fallback Closing (strong|acceptable, unterschiedlich)."""
    errors: list[str] = []
    if not plan.slots:
        return errors
    last = plan.slots[-1]
    fit = str(last.asset_fit or "none").strip().lower()
    primary = str(last.local_asset_id or "").strip()
    if fit not in {"strong", "acceptable"} or not primary:
        errors.append(
            "Keyword Flow: Primary Closing (letzter Slot) muss strong/acceptable "
            "mit gültiger local_asset_id sein."
        )
    fallback = str(plan.closing_fallback_asset_id or "").strip()
    if not fallback:
        errors.append(
            "Keyword Flow: closing_fallback_asset_id fehlt "
            "(anderer strong/acceptable Closer)."
        )
    elif primary and fallback == primary:
        errors.append(
            "Keyword Flow: closing_fallback_asset_id darf nicht dem Primary Closing "
            "entsprechen."
        )
    if catalog is not None and primary:
        entry, err = _lookup(catalog, primary)
        if entry is None:
            errors.append(f"Keyword Flow: Primary Closing nicht auflösbar ({err}).")
    if catalog is not None and fallback and fallback != primary:
        entry, err = _lookup(catalog, fallback)
        if entry is None:
            errors.append(f"Keyword Flow: Fallback Closing nicht auflösbar ({err}).")
    # Fallback-Fit ist nicht als Slot modelliert — Prompt fordert strong/acceptable;
    # technische Auflösbarkeit wird hier geprüft.
    return errors


def _lookup(catalog: AssetCatalog, asset_id: str):
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    return lookup_catalog_entry(catalog, asset_id)
