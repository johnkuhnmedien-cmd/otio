"""Keyword-Flow: Closing Primary + Fallback (kanonische Medienvalidierung)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from otio_app.services.media_utils import is_image_media, is_video_media
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import UnifiedCutPlanDocument
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    AssetCatalog,
    still_image_path_from_catalog_entry,
)


class KeywordFlowClosingError(ValueError):
    pass


_ALLOWED_FALLBACK_FITS = frozenset({"strong", "acceptable"})
_STILL_MEDIA_KINDS = frozenset({"image", "photo"})


def _is_still_catalog_entry(entry: dict[str, Any]) -> bool:
    """True für Standbilder — die können den Closing-Slot per Hold tragen."""
    still = still_image_path_from_catalog_entry(entry)
    if still is not None:
        return True
    original = str(entry.get("original_image_path") or "").strip()
    if original:
        try:
            orig = Path(original)
            if is_image_media(orig) and not is_video_media(orig):
                return True
        except OSError:
            pass
    kind = str(entry.get("media_kind") or "").strip().lower()
    media_type = str(entry.get("media_type") or "").strip().lower()
    if kind in _STILL_MEDIA_KINDS or media_type in _STILL_MEDIA_KINDS:
        return True
    path_text = str(entry.get("path") or "").strip()
    if path_text:
        try:
            return is_image_media(Path(path_text))
        except OSError:
            return False
    return False


def assess_closing_asset_technical(
    catalog: AssetCatalog | None,
    asset_id: str,
    *,
    min_duration_seconds: float = 0.0,
    expected_folder: str | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Technische Eignung über denselben Medienvertrag wie Enhanced-Export."""
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
    if is_http_url(path_text):
        return False, "HTTP-URL unzulässig", entry
    folder = str(entry.get("folder") or "").strip()
    if expected_folder and folder and folder != expected_folder:
        return (
            False,
            f"Asset gehört zu Kapitel {folder!r}, erwartet {expected_folder!r}",
            entry,
        )
    media_type = str(entry.get("media_type") or entry.get("media_kind") or "video")
    still_source = still_image_path_from_catalog_entry(entry)
    if still_source is not None:
        media_type = "photo"
        path_text = str(still_source)
    elif _is_still_catalog_entry(entry):
        # Inventar hat oft leeres media_type — Stills nicht als Video validieren.
        # Clean-MP4 eines Fotos ohne auffindbares Original: Datei als Video prüfen.
        if is_video_media(Path(path_text)):
            media_type = "video"
        else:
            media_type = "photo"
    status, detail = validate_local_media_path(path_text, media_type=media_type)
    if status != STATUS_EXPORT_READY:
        return False, detail or status, entry
    # Videos brauchen genug Source-Länge; Stills halten den Slot (Ken Burns / Hold).
    if _is_still_catalog_entry(entry):
        return True, "ok (still hold)", entry
    duration = float(entry.get("duration_seconds") or 0.0)
    need = max(0.0, float(min_duration_seconds))
    if need > 0 and duration + 1e-9 < need:
        return (
            False,
            f"Source-Dauer zu kurz ({duration:.2f}s < {need:.2f}s nötig)",
            entry,
        )
    return True, "ok", entry


def validate_keyword_flow_closing(
    plan: UnifiedCutPlanDocument,
    *,
    catalog: AssetCatalog | None = None,
    min_duration_seconds: float = 0.0,
    expected_folder: str | None = None,
    require_fallback_fit: bool = True,
) -> list[str]:
    """Prüft Primary/Fallback Closing inkl. Fit-Feldern und Medienvertrag."""
    errors: list[str] = []
    if not plan.slots:
        return errors
    last = plan.slots[-1]
    fit = str(last.asset_fit or "none").strip().lower()
    primary = str(last.local_asset_id or "").strip()
    if fit not in _ALLOWED_FALLBACK_FITS or not primary:
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

    fb_fit = str(plan.closing_fallback_asset_fit or "").strip().lower()
    if require_fallback_fit:
        if not fb_fit:
            errors.append(
                "Keyword Flow: closing_fallback_asset_fit fehlt "
                "(strong|acceptable Pflicht für neue Keyword-Flow-Pläne)."
            )
        elif fb_fit not in _ALLOWED_FALLBACK_FITS:
            errors.append(
                f"Keyword Flow: closing_fallback_asset_fit={fb_fit!r} unzulässig "
                "(nur strong|acceptable; weak/none blockiert)."
            )
        if not str(plan.closing_fallback_asset_fit_reason or "").strip():
            errors.append(
                "Keyword Flow: closing_fallback_asset_fit_reason fehlt."
            )
        if not str(plan.closing_fallback_visual_intent or "").strip():
            errors.append(
                "Keyword Flow: closing_fallback_visual_intent fehlt."
            )

    primary_ok = False
    primary_reason = "n/a"
    if catalog is not None and primary:
        primary_ok, primary_reason, _ = assess_closing_asset_technical(
            catalog,
            primary,
            min_duration_seconds=min_duration_seconds,
            expected_folder=expected_folder,
        )
    fallback_ok = False
    fallback_reason = "n/a"
    if catalog is not None and fallback and fallback != primary:
        fallback_ok, fallback_reason, _ = assess_closing_asset_technical(
            catalog,
            fallback,
            min_duration_seconds=min_duration_seconds,
            expected_folder=expected_folder,
        )
        if not fallback_ok:
            errors.append(
                f"Keyword Flow: Fallback Closing technisch unbrauchbar "
                f"({fallback_reason})."
            )

    if catalog is not None and primary and fallback and fallback != primary:
        if not primary_ok and not fallback_ok:
            errors.append(
                "Keyword Flow: Primary und Fallback Closing beide technisch "
                f"ungültig (primary={primary_reason}; fallback={fallback_reason})."
            )
    return errors


def choose_closing_asset_for_resolve(
    *,
    primary_id: str,
    fallback_id: str,
    catalog: AssetCatalog,
    primary_failure: str | None = None,
    min_duration_seconds: float = 0.0,
    expected_folder: str | None = None,
    usage_counts: dict[str, int] | None = None,
    max_asset_usage: int = 2,
    plan: UnifiedCutPlanDocument | None = None,
) -> tuple[str, dict[str, Any], str]:
    """Wählt Closing-Asset: Primary wenn technisch/regelkonform, sonst Fallback."""
    primary = str(primary_id or "").strip()
    fallback = str(fallback_id or "").strip()
    counts = usage_counts or {}

    if plan is not None:
        fb_fit = str(plan.closing_fallback_asset_fit or "").strip().lower()
        if not fb_fit:
            raise KeywordFlowClosingError(
                "Keyword Flow: closing_fallback_asset_fit fehlt "
                "(strong|acceptable Pflicht)."
            )
        if fb_fit not in _ALLOWED_FALLBACK_FITS:
            raise KeywordFlowClosingError(
                f"Keyword Flow: Fallback-Fit {fb_fit!r} blockiert Closing "
                "(nur strong|acceptable)."
            )
        if not str(plan.closing_fallback_asset_fit_reason or "").strip():
            raise KeywordFlowClosingError(
                "Keyword Flow: closing_fallback_asset_fit_reason fehlt."
            )
        if not str(plan.closing_fallback_visual_intent or "").strip():
            raise KeywordFlowClosingError(
                "Keyword Flow: closing_fallback_visual_intent fehlt."
            )

    def _usage_ok(asset_id: str) -> tuple[bool, str]:
        used = int(counts.get(asset_id, 0))
        # Closing-Auswahl selbst zählt +1.
        if used + 1 > int(max_asset_usage):
            return (
                False,
                f"Usage-Verstoß: {asset_id} wäre {used + 1}× "
                f"(max_asset_usage={max_asset_usage})",
            )
        return True, "ok"

    if primary and not primary_failure:
        ok, reason, entry = assess_closing_asset_technical(
            catalog,
            primary,
            min_duration_seconds=min_duration_seconds,
            expected_folder=expected_folder,
        )
        if ok and entry is not None:
            usage_ok, usage_reason = _usage_ok(primary)
            if usage_ok:
                return primary, entry, "primary"
            primary_failure = usage_reason
        else:
            primary_failure = reason

    if fallback and fallback != primary:
        ok, reason, entry = assess_closing_asset_technical(
            catalog,
            fallback,
            min_duration_seconds=min_duration_seconds,
            expected_folder=expected_folder,
        )
        if ok and entry is not None:
            usage_ok, usage_reason = _usage_ok(fallback)
            if not usage_ok:
                raise KeywordFlowClosingError(
                    "Keyword Flow: Primary und Fallback Closing beide ungültig "
                    f"(primary={primary_failure or 'n/a'}; fallback={usage_reason})."
                )
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
