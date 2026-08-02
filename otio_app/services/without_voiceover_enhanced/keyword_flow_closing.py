"""Keyword-Flow: Closing Primary + Fallback Validierung und Auswahl."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from otio_app.services.without_voiceover_enhanced.models import UnifiedCutPlanDocument
from otio_app.services.without_voiceover_enhanced.timeline_resolver import AssetCatalog


class KeywordFlowClosingError(ValueError):
    pass


def assess_closing_asset_technical(
    catalog: AssetCatalog | None,
    asset_id: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Deterministische technische Eignung eines Closing-Assets (kein Prompt)."""
    aid = str(asset_id or "").strip()
    if not aid:
        return False, "Asset-ID fehlt", None
    if catalog is None:
        return False, "Asset-Katalog fehlt", None
    entry, err = _lookup(catalog, aid)
    if entry is None:
        return False, err or "nicht im Katalog", None
    path_text = str(entry.get("path") or "").strip()
    if not path_text:
        return False, "Pfad fehlt", entry
    lowered = path_text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return False, "HTTP-URL unzulässig", entry
    media = Path(path_text)
    if not media.is_file():
        return False, f"lokale Datei fehlt: {media}", entry
    return True, "ok", entry


def validate_keyword_flow_closing(
    plan: UnifiedCutPlanDocument,
    *,
    catalog: AssetCatalog | None = None,
) -> list[str]:
    """Prüft Primary/Fallback Closing (strong|acceptable, unterschiedlich, technisch)."""
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

    primary_ok = False
    if catalog is not None and primary:
        primary_ok, primary_reason, _ = assess_closing_asset_technical(catalog, primary)
        if not primary_ok:
            # Primary technisch unbrauchbar ist erlaubt, wenn Fallback trägt —
            # Resolve tauscht dann. Hier nur notieren wenn Fallback auch fehlt.
            pass
    fallback_ok = False
    if catalog is not None and fallback and fallback != primary:
        fallback_ok, fallback_reason, _ = assess_closing_asset_technical(
            catalog, fallback
        )
        if not fallback_ok:
            errors.append(
                f"Keyword Flow: Fallback Closing technisch unbrauchbar "
                f"({fallback_reason})."
            )

    if catalog is not None and primary and fallback and fallback != primary:
        if not primary_ok:
            primary_ok, primary_reason, _ = assess_closing_asset_technical(
                catalog, primary
            )
        if not primary_ok and not fallback_ok:
            errors.append(
                "Keyword Flow: Primary und Fallback Closing beide technisch "
                f"ungültig (primary={primary_reason})."
            )
        elif not primary_ok and fallback_ok:
            # Resolve muss Fallback verwenden — kein Plan-Blocker.
            pass
    return errors


def choose_closing_asset_for_resolve(
    *,
    primary_id: str,
    fallback_id: str,
    catalog: AssetCatalog,
    primary_failure: str | None = None,
) -> tuple[str, dict[str, Any], str]:
    """Wählt Closing-Asset: Primary wenn technisch ok, sonst Fallback.

    Raises KeywordFlowClosingError wenn keines verwendbar ist.
    """
    primary = str(primary_id or "").strip()
    fallback = str(fallback_id or "").strip()
    if primary and not primary_failure:
        ok, reason, entry = assess_closing_asset_technical(catalog, primary)
        if ok and entry is not None:
            return primary, entry, "primary"
        primary_failure = reason
    if fallback and fallback != primary:
        ok, reason, entry = assess_closing_asset_technical(catalog, fallback)
        if ok and entry is not None:
            return (
                fallback,
                entry,
                f"fallback (primary unusable: {primary_failure or 'unknown'})",
            )
        raise KeywordFlowClosingError(
            "Keyword Flow: Primary und Fallback Closing beide ungültig "
            f"(primary={primary_failure or 'n/a'}; fallback={reason})."
        )
    raise KeywordFlowClosingError(
        "Keyword Flow: Closing blockiert — "
        f"primary={primary_failure or 'unusable'}; fallback fehlt oder identisch."
    )


def _lookup(catalog: AssetCatalog, asset_id: str):
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    return lookup_catalog_entry(catalog, asset_id)
