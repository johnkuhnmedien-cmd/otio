"""Ein Eingangstor für beschaffte Assets ins geteilte Ordner-Inventar.

Beschafftes Material kommt aus mehreren Quellen: dem Supplement-Funnel in der
App, der Coverage-Gap-Inbox (externe Recherche), manueller Zuweisung und
generierten Bildern. Alle laufen durch ``ingest_supplement_asset``.

Zwei Zusagen dieses Moduls:

1. **Gleiche Parameter wie bei der Erstanalyse.** Jedes Asset läuft durch
   ``asset_analyzer.analyze_supplement_media`` — denselben Prompt, dasselbe
   v3-Schema, dieselbe Signatur wie ein Original. Die Beschaffungsbegründung
   landet in ``supplement_intake_note``, nicht in ``description``.
2. **Sprachunabhängige Haltbarkeit.** Inventar und Analyse-Cache liegen im
   geteilten Arbeitsverzeichnis. Ein zweites Sprachprojekt im selben
   Medienordner sieht das Asset wie jedes Original — mit Dauer, Tags, Motion,
   Framing und Qualitätsprofil.

Ohne API-Schlüssel wird trotzdem ingestiert, aber ohne Analyse. Solche Zeilen
tragen keine v3-Signatur und werden vom Analysen-Tab als offen erkannt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    is_supplement_asset,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.asset_analysis_signature import (
    AssetCacheStatus,
    classify_asset_cache_status,
)
from otio_app.services.inventory_loader import (
    load_folder_inventory_file,
    save_folder_inventory,
)
from otio_app.services.media_inventory_cache import (
    CACHE_SCOPE_SUPPLEMENT,
    load_cached_media_for_asset,
    media_cache_path,
    save_cached_media,
    scan_folder_supplement_cache_assets,
)

__all__ = [
    "INTAKE_SOURCE_FUNNEL",
    "INTAKE_SOURCE_GENERIC_FALLBACK",
    "INTAKE_SOURCE_INBOX",
    "INTAKE_SOURCE_MANUAL",
    "SupplementAnalysisReport",
    "SupplementAssetStatus",
    "SupplementIngestResult",
    "SupplementProvenance",
    "analyze_supplements_for_folder",
    "count_supplements_needing_analysis",
    "ingest_supplement_asset",
    "list_supplement_assets",
    "upsert_supplement_into_inventory",
]

INTAKE_SOURCE_FUNNEL = "funnel"
INTAKE_SOURCE_INBOX = "inbox"
INTAKE_SOURCE_MANUAL = "manual"
INTAKE_SOURCE_GENERIC_FALLBACK = "generic_fallback"

#: Fällt die Analyse aus, bleibt der Ursprung als Herkunft erhalten.
_DEFAULT_ORIGIN = "supplement"

ShouldCancel = Callable[[], bool]
ProgressCallback = Callable[[str, dict], None]


def _noop_progress(_event: str, _payload: dict) -> None:
    return None


@dataclass(frozen=True)
class SupplementProvenance:
    """Alles, was die Analyse nicht aus dem Bild ablesen kann."""

    asset_id: str
    asset_origin: str = _DEFAULT_ORIGIN
    provider: str = ""
    provider_asset_id: str = ""
    source_url: str = ""
    media_type: str = ""
    license_metadata: dict[str, str] = field(default_factory=dict)
    supplement_validation_status: str = ""
    supplement_validation_score: float = 0.0
    approved_for_cut_plan: bool = True
    intake_source: str = INTAKE_SOURCE_FUNNEL
    intake_note: str = ""
    search_query: str = ""
    generated_prompt: str = ""
    supplement_request_id: str = ""
    rights_status: str = ""
    #: Nur relevant, wenn keine Analyse möglich ist.
    fallback_description: str = ""


@dataclass(frozen=True)
class SupplementIngestResult:
    folder_name: str
    asset: AssetMediaAnalysis
    #: ``analyzed`` | ``cached`` | ``not_analyzed`` | ``analysis_failed``
    status: str
    error: str | None = None

    @property
    def has_full_analysis(self) -> bool:
        return self.status in {"analyzed", "cached"}


@dataclass(frozen=True)
class SupplementAssetStatus:
    folder_name: str
    media_path: Path
    asset_id: str
    asset_origin: str
    cache_status: AssetCacheStatus
    in_inventory: bool

    @property
    def needs_analysis(self) -> bool:
        return self.cache_status.status != "current"

    @property
    def reason(self) -> str:
        return ", ".join(self.cache_status.reasons)


@dataclass
class SupplementAnalysisReport:
    folder_name: str = ""
    analyzed: int = 0
    cached: int = 0
    failed: int = 0
    cancelled: bool = False
    failures: list[str] = field(default_factory=list)


def _apply_provenance(
    asset: AssetMediaAnalysis,
    provenance: SupplementProvenance,
) -> AssetMediaAnalysis:
    """Herkunftsfelder über das Analyseergebnis legen."""
    license_metadata = dict(asset.license_metadata or {})
    license_metadata.update(
        {key: value for key, value in (provenance.license_metadata or {}).items() if value}
    )
    if provenance.provider and not license_metadata.get("provider"):
        license_metadata["provider"] = provenance.provider
    if provenance.provider_asset_id and not license_metadata.get("provider_asset_id"):
        license_metadata["provider_asset_id"] = provenance.provider_asset_id

    from otio_app.services.asset_analysis_signature import is_usable_asset_analysis

    updates: dict[str, object] = {
        "asset_id": provenance.asset_id or asset.asset_id,
        "asset_origin": provenance.asset_origin or _DEFAULT_ORIGIN,
        "license_metadata": license_metadata,
        "supplement_intake_source": provenance.intake_source,
        "supplement_intake_note": provenance.intake_note,
        "approved_for_cut_plan": provenance.approved_for_cut_plan,
        "analysis_status": "complete" if is_usable_asset_analysis(asset) else "pending",
    }
    for field_name, value in (
        ("provider", provenance.provider),
        ("source_url", provenance.source_url),
        ("search_query", provenance.search_query),
        ("generated_prompt", provenance.generated_prompt),
        ("supplement_request_id", provenance.supplement_request_id),
        ("rights_status", provenance.rights_status),
        ("supplement_validation_status", provenance.supplement_validation_status),
    ):
        if value:
            updates[field_name] = value
    if provenance.supplement_validation_score:
        updates["supplement_validation_score"] = float(
            provenance.supplement_validation_score
        )
    if provenance.media_type and not asset.media_type:
        updates["media_type"] = provenance.media_type
    return asset.model_copy(update=updates)


def _fallback_asset(
    media_path: Path,
    provenance: SupplementProvenance,
) -> AssetMediaAnalysis:
    """Zeile ohne Analyse — bewusst ohne v3-Signatur, damit sie offen bleibt."""
    from otio_app.services.media_utils import is_image_media

    description = (provenance.fallback_description or "").strip()
    media_type = provenance.media_type or (
        "image" if is_image_media(media_path) else "video"
    )
    return AssetMediaAnalysis(
        path=str(media_path),
        description=description,
        media_type=media_type,
    )


def _matches_existing(
    existing: AssetMediaAnalysis,
    *,
    asset_id: str,
    media_path: Path,
    provider_key: str | None,
) -> bool:
    if existing.path == str(media_path):
        return True
    if asset_id and existing.asset_id == asset_id:
        return True
    if provider_key is None:
        return False
    return _provider_key_for_asset(existing) == provider_key


def _provider_key_for_asset(asset: AssetMediaAnalysis) -> str | None:
    try:
        from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
            provider_identity_for_inventory_asset,
        )
    except Exception:  # noqa: BLE001
        return None
    identity = provider_identity_for_inventory_asset(asset)
    return identity.key if identity is not None else None


def upsert_supplement_into_inventory(
    project: Project,
    *,
    folder_name: str,
    asset: AssetMediaAnalysis,
) -> AssetFolderAnalysis:
    """Schreibt/ersetzt eine Supplement-Zeile im geteilten Ordner-Inventar.

    Dedupe über Pfad, ``asset_id`` und Provider-Identität — dasselbe Stock-Asset
    darf nicht mehrfach im Inventar stehen, sonst greifen ``max_asset_usage``
    und der Reuse-Abstand nicht mehr.
    """
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    media_path = Path(asset.path)
    provider_key = _provider_key_for_asset(asset)
    existing = load_folder_inventory_file(path)

    if existing is None:
        folder_doc = AssetFolderAnalysis(
            folder=folder_name,
            description="",
            media_files=[asset.path],
            frames_used=list(asset.frames_used),
            assets=[asset],
        )
        save_folder_inventory(path, folder_doc)
        return folder_doc

    replaced_paths: set[str] = set()
    assets: list[AssetMediaAnalysis] = []
    for prior in existing.assets or []:
        if _matches_existing(
            prior,
            asset_id=asset.asset_id,
            media_path=media_path,
            provider_key=provider_key,
        ):
            replaced_paths.add(prior.path)
            continue
        assets.append(prior)
    assets.append(asset)

    media_files = [
        entry
        for entry in (existing.media_files or [])
        if entry not in replaced_paths or entry == asset.path
    ]
    if asset.path not in media_files:
        media_files.append(asset.path)

    frames_used = list(existing.frames_used or [])
    for frame in asset.frames_used:
        if frame not in frames_used:
            frames_used.append(frame)

    folder_doc = existing.model_copy(
        update={
            "assets": assets,
            "media_files": media_files,
            "frames_used": frames_used,
        }
    )
    save_folder_inventory(path, folder_doc)
    return folder_doc


def ingest_supplement_asset(
    project: Project,
    *,
    folder_name: str,
    media_path: Path | str,
    provenance: SupplementProvenance,
    use_api: bool = True,
    model: Optional[str] = None,
    analyze: bool = True,
    should_cancel: ShouldCancel | None = None,
) -> SupplementIngestResult:
    """Beschafftes Asset analysieren und ins geteilte Inventar aufnehmen."""
    path = Path(media_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Supplement-Datei fehlt oder ist leer: {path}")

    status = "not_analyzed"
    error: str | None = None
    analyzed: AssetMediaAnalysis | None = None

    if analyze:
        from otio_app.services.asset_analyzer import analyze_supplement_media

        try:
            entry, outcome = analyze_supplement_media(
                project,
                folder_name,
                path,
                use_api=use_api,
                model=model,
                should_cancel=should_cancel,
            )
        except Exception as exc:  # noqa: BLE001
            # Ein fehlgeschlagener Analyselauf darf die Beschaffung nicht
            # verwerfen — die Zeile bleibt offen und wird später nachgeholt.
            error = str(exc)
        else:
            from otio_app.services.asset_analysis_signature import (
                is_usable_asset_analysis,
            )

            # Eine gescheiterte Analyse darf die Beschaffungsdaten nicht
            # überschreiben — sonst stünde ein Platzhaltertext als Beschreibung
            # im Inventar und das Asset verschwände aus der LLM-Sicht.
            usable = is_usable_asset_analysis(entry)
            if outcome == "fehler" or not usable:
                status = "analysis_failed"
                error = entry.error
                analyzed = entry if usable else None
            else:
                status = "analyzed" if outcome == "neu" else "cached"
                analyzed = entry

    base = analyzed if analyzed is not None else _fallback_asset(path, provenance)
    asset = _apply_provenance(base.model_copy(update={"path": str(path)}), provenance)

    # Supplement-Cache ist der haltbare Speicher: aus ihm stellt
    # ``inventory_loader`` verlorene Zeilen ohne neuen LLM-Aufruf wieder her.
    save_cached_media(
        media_cache_path(project, folder_name, path, scope=CACHE_SCOPE_SUPPLEMENT),
        asset,
    )
    upsert_supplement_into_inventory(project, folder_name=folder_name, asset=asset)

    return SupplementIngestResult(
        folder_name=folder_name,
        asset=asset,
        status=status,
        error=error,
    )


def _inventory_supplements(
    project: Project,
    folder_name: str,
) -> list[AssetMediaAnalysis]:
    inventory = load_folder_inventory_file(
        get_folder_inventory_path(project.work_dir_path, folder_name)
    )
    return [
        asset
        for asset in (getattr(inventory, "assets", None) or [])
        if is_supplement_asset(asset)
    ]


def list_supplement_assets(
    project: Project,
    folder_name: str,
    *,
    model: Optional[str] = None,
) -> list[SupplementAssetStatus]:
    """Beschaffte Assets eines Ordners samt Analysestand.

    Quellen sind Inventar und Supplement-Cache. Eine Zeile ohne aktuelle
    v3-Signatur — etwa aus einem Funnel-Import vor dieser Änderung oder aus der
    externen Inbox — gilt als nicht analysiert. Das ist derselbe Maßstab, den
    Originale über ``classify_asset_cache_status`` bereits nutzen.
    """
    from otio_app.services.gemini_client import resolve_gemini_model

    resolved_model = resolve_gemini_model(model)
    inventory_paths = {
        asset.path: asset for asset in _inventory_supplements(project, folder_name)
    }
    cached_paths = {
        asset.path: asset
        for asset in scan_folder_supplement_cache_assets(project, folder_name)
    }

    statuses: list[SupplementAssetStatus] = []
    for raw_path in sorted({*inventory_paths, *cached_paths}):
        path = Path(raw_path)
        if not path.is_file():
            continue
        cached = load_cached_media_for_asset(
            project, folder_name, path, scope=CACHE_SCOPE_SUPPLEMENT
        )
        row = cached or inventory_paths.get(raw_path) or cached_paths.get(raw_path)
        reference = inventory_paths.get(raw_path) or cached_paths.get(raw_path) or row
        statuses.append(
            SupplementAssetStatus(
                folder_name=folder_name,
                media_path=path,
                asset_id=str(getattr(reference, "asset_id", "") or ""),
                asset_origin=str(getattr(reference, "asset_origin", "") or ""),
                cache_status=classify_asset_cache_status(
                    row, path, resolved_model_id=resolved_model
                ),
                in_inventory=raw_path in inventory_paths,
            )
        )
    return statuses


def count_supplements_needing_analysis(
    project: Project,
    folder_names: Iterable[str],
    *,
    model: Optional[str] = None,
) -> int:
    total = 0
    for folder_name in folder_names:
        total += sum(
            1
            for status in list_supplement_assets(project, folder_name, model=model)
            if status.needs_analysis
        )
    return total


def analyze_supplements_for_folder(
    project: Project,
    folder_name: str,
    *,
    use_api: bool = True,
    model: Optional[str] = None,
    should_cancel: ShouldCancel | None = None,
    on_progress: ProgressCallback = _noop_progress,
) -> SupplementAnalysisReport:
    """Holt die reguläre Analyse für offene Supplements eines Ordners nach.

    Die Herkunftsfelder der bestehenden Zeile bleiben erhalten; ersetzt wird nur
    das, was aus dem Bild kommt.
    """
    from otio_app.services.analysis_cancel import AnalysisCancelledError
    from otio_app.services.asset_analyzer import analyze_supplement_media

    report = SupplementAnalysisReport(folder_name=folder_name)
    open_statuses = [
        status
        for status in list_supplement_assets(project, folder_name, model=model)
        if status.needs_analysis
    ]
    if not open_statuses:
        return report

    known = {asset.path: asset for asset in _inventory_supplements(project, folder_name)}
    for index, status in enumerate(open_statuses, start=1):
        if should_cancel is not None and should_cancel():
            report.cancelled = True
            break
        on_progress(
            "supplement_start",
            {
                "folder": folder_name,
                "media_name": status.media_path.name,
                "media_index": index,
                "media_count": len(open_statuses),
            },
        )
        previous = known.get(str(status.media_path))
        provenance = _provenance_from_asset(previous, status)
        try:
            entry, outcome = analyze_supplement_media(
                project,
                folder_name,
                status.media_path,
                use_api=use_api,
                model=model,
                should_cancel=should_cancel,
            )
        except AnalysisCancelledError:
            report.cancelled = True
            break
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.failures.append(f"{folder_name}/{status.media_path.name}: {exc}")
            continue

        if outcome == "fehler":
            report.failed += 1
            report.failures.append(
                f"{folder_name}/{status.media_path.name}: "
                f"{entry.error or 'Analyse fehlgeschlagen'}"
            )
            continue

        asset = _apply_provenance(entry, provenance)
        save_cached_media(
            media_cache_path(
                project, folder_name, status.media_path, scope=CACHE_SCOPE_SUPPLEMENT
            ),
            asset,
        )
        upsert_supplement_into_inventory(
            project, folder_name=folder_name, asset=asset
        )
        if outcome == "cache":
            report.cached += 1
        else:
            report.analyzed += 1
        on_progress(
            "supplement_done",
            {
                "folder": folder_name,
                "media_name": status.media_path.name,
                "media_index": index,
                "media_count": len(open_statuses),
                "outcome": outcome,
            },
        )
    return report


def _provenance_from_asset(
    asset: AssetMediaAnalysis | None,
    status: SupplementAssetStatus,
) -> SupplementProvenance:
    if asset is None:
        return SupplementProvenance(
            asset_id=status.asset_id,
            asset_origin=status.asset_origin or _DEFAULT_ORIGIN,
        )
    return SupplementProvenance(
        asset_id=asset.asset_id or status.asset_id,
        asset_origin=asset.asset_origin or _DEFAULT_ORIGIN,
        provider=asset.provider,
        source_url=asset.source_url,
        media_type=asset.media_type,
        license_metadata=dict(asset.license_metadata or {}),
        supplement_validation_status=asset.supplement_validation_status,
        supplement_validation_score=asset.supplement_validation_score,
        approved_for_cut_plan=asset.approved_for_cut_plan,
        intake_source=asset.supplement_intake_source,
        intake_note=asset.supplement_intake_note,
        search_query=asset.search_query,
        generated_prompt=asset.generated_prompt,
        supplement_request_id=asset.supplement_request_id,
        rights_status=asset.rights_status,
    )
