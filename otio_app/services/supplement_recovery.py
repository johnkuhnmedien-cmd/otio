"""Bestandsaufnahme: beschaffte Assets eines Altprojekts zurück ins Inventar.

Vor der Vereinheitlichung entfernte ein Ordner-Sync die Inventarzeilen
beschaffter Assets wieder (``is_external_inventory_media_path`` kannte die
Enhanced-Ablage nicht). Die Dateien selbst blieben liegen — verloren ging nur
die Zeile.

Dieses Modul rekonstruiert sie aus drei Quellen, die der Sync nie angefasst hat:

1. **Acceptance-Listen aller Sprachen** —
   ``{LANG}/voiceover_generation/stock/accepted_supplements.json``. Beste Quelle:
   Provider, Provider-Asset-ID, Lizenz, Gap und lokaler Pfad stehen dort.
2. **Clean-Media-Manifeste** — ``clean_media/{Ordner}.json``. Ein Eintrag, dessen
   ``original_path`` außerhalb des Medienordners liegt, ist beschafftes Material.
   Diese Quelle kennt den Ordner exakt.
3. **Stock-Downloads** — ``{LANG}/voiceover_generation/stock/downloads/…`` für
   Dateien, die es nie in Clean Media oder eine Acceptance-Liste geschafft haben.

Anschließend läuft jedes gefundene Asset durch dasselbe Eingangstor wie ein
frischer Fund (``supplement_inventory.ingest_supplement_asset``) und bekommt die
reguläre v3-Analyse. Wiederholte Läufe sind billig: liegt bereits eine aktuelle
Analyse vor, wird sie nicht erneut angefordert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from otio_app.analysis_models import CleanMediaManifest, is_supplement_asset
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project
from otio_app.project_layout import (
    get_clean_media_manifest_dir,
    get_clean_media_output_dir,
    get_folder_inventory_path,
    safe_folder_slug,
)
from otio_app.services.inventory_loader import load_folder_inventory_file
from otio_app.services.media_utils import MEDIA_EXTENSIONS
from otio_app.services.supplement_inventory import (
    INTAKE_SOURCE_FUNNEL,
    INTAKE_SOURCE_INBOX,
    SupplementProvenance,
    ingest_supplement_asset,
)

__all__ = [
    "RecoverableSupplement",
    "SupplementRecoveryReport",
    "recover_supplements_into_inventory",
    "scan_recoverable_supplements",
]

_LEDGER_SOURCE_PREFIX = "accepted"
_CLEAN_SOURCE = "clean_manifest"
_DOWNLOAD_SOURCE = "stock_download"


@dataclass(frozen=True)
class RecoverableSupplement:
    media_path: Path
    folder_name: str
    provenance: SupplementProvenance
    #: ``accepted:{LANG}`` | ``clean_manifest`` | ``stock_download``
    source: str
    in_inventory: bool


@dataclass
class SupplementRecoveryReport:
    scanned: int = 0
    recovered: int = 0
    analyzed: int = 0
    already_complete: int = 0
    failed: int = 0
    unresolved: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    recovered_by_folder: dict[str, int] = field(default_factory=dict)

    @property
    def has_work(self) -> bool:
        return bool(self.recovered or self.failed or self.unresolved)


def _is_enhanced(project: Project) -> bool:
    return project.work_dir_path.name == DEFAULT_ENHANCED_WORK_SUBDIR


def _known_folders(project: Project) -> list[str]:
    """Ordnernamen aus Projektauswahl und vorhandenen Inventar-JSONs."""
    names: list[str] = []
    for name in (project.asset_subdir_names or []) + (
        project.selected_asset_subdirs or []
    ):
        if name and name not in names:
            names.append(name)
    inventory_dir = project.work_dir_path / "inventory"
    if inventory_dir.is_dir():
        for path in sorted(inventory_dir.glob("*.json")):
            item = load_folder_inventory_file(path)
            if item is not None and item.folder and item.folder not in names:
                names.append(item.folder)
    return names


def _folder_by_slug(project: Project) -> dict[str, str]:
    return {safe_folder_slug(name).casefold(): name for name in _known_folders(project)}


def _folder_from_clean_path(project: Project, media_path: Path) -> str:
    """``clean/{Ordner-Slug}/datei.mp4`` → Ordnername."""
    clean_root = get_clean_media_output_dir(project.work_dir_path)
    try:
        relative = media_path.resolve().relative_to(clean_root.resolve())
    except (OSError, ValueError):
        return ""
    parts = relative.parts
    if not parts:
        return ""
    return _folder_by_slug(project).get(parts[0].casefold(), "")


def _folder_from_gap_id(project: Project, gap_id: str) -> str:
    """``Yellowstone_gap_003`` → ``Yellowstone`` (Kapitel-Prefix aus dem Cut)."""
    text = (gap_id or "").strip()
    marker = "_gap_"
    index = text.lower().find(marker)
    if index <= 0:
        return ""
    return _folder_by_slug(project).get(
        safe_folder_slug(text[:index]).casefold(), ""
    )


def _clean_manifests(project: Project) -> list[tuple[str, CleanMediaManifest]]:
    manifest_dir = get_clean_media_manifest_dir(project.work_dir_path)
    if not manifest_dir.is_dir():
        return []
    out: list[tuple[str, CleanMediaManifest]] = []
    slugs = _folder_by_slug(project)
    for path in sorted(manifest_dir.glob("*.json")):
        try:
            manifest = CleanMediaManifest.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            continue
        folder = manifest.folder or slugs.get(path.stem.casefold(), "")
        if folder:
            out.append((folder, manifest))
    return out


def _folder_from_clean_manifest(project: Project, media_path: Path) -> str:
    target = str(media_path)
    for folder, manifest in _clean_manifests(project):
        for entry in manifest.entries:
            if entry.clean_path and str(entry.clean_path) == target:
                return folder
    return ""


def _resolve_folder(
    project: Project,
    media_path: Path,
    *,
    gap_id: str = "",
) -> str:
    for candidate in (
        _folder_from_clean_path(project, media_path),
        _folder_from_clean_manifest(project, media_path),
        _folder_from_gap_id(project, gap_id),
    ):
        if candidate:
            return candidate
    return ""


def _inventory_supplement_paths(project: Project) -> set[str]:
    paths: set[str] = set()
    for folder in _known_folders(project):
        item = load_folder_inventory_file(
            get_folder_inventory_path(project.work_dir_path, folder)
        )
        for asset in getattr(item, "assets", None) or []:
            if is_supplement_asset(asset) and asset.path:
                paths.add(asset.path)
    return paths


def _usable_media(path: Path | str | None) -> Path | None:
    text = str(path or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return None
    candidate = Path(text)
    if candidate.suffix.lower() not in MEDIA_EXTENSIONS:
        return None
    try:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    except OSError:
        return None
    return None


def _provenance_from_candidate(candidate, *, intake_source: str) -> SupplementProvenance:
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        provider_identity_for_candidate,
    )

    identity = provider_identity_for_candidate(candidate)
    asset_id = str(getattr(candidate, "candidate_id", "") or "").strip()
    if not asset_id and identity is not None:
        asset_id = identity.stable_asset_id
    license_metadata = {
        "license": str(getattr(candidate, "license", "") or ""),
        "attribution": str(
            getattr(candidate, "attribution", "")
            or getattr(candidate, "creator", "")
            or ""
        ),
    }
    if identity is not None:
        license_metadata["provider"] = identity.provider
        license_metadata["provider_asset_id"] = identity.provider_asset_id
    return SupplementProvenance(
        asset_id=asset_id or "supplement_unknown",
        asset_origin=str(getattr(candidate, "provider", "") or "") or "supplement",
        provider=str(getattr(candidate, "provider", "") or ""),
        provider_asset_id=identity.provider_asset_id if identity else "",
        source_url=str(
            getattr(candidate, "source_page", "")
            or getattr(candidate, "download_url", "")
            or ""
        ),
        media_type=str(getattr(candidate, "media_type", "") or ""),
        license_metadata=license_metadata,
        supplement_validation_status=str(
            getattr(candidate, "media_validation_status", "") or ""
        ),
        approved_for_cut_plan=True,
        intake_source=intake_source,
        fallback_description=str(getattr(candidate, "title", "") or ""),
    )


def _provenance_from_path(media_path: Path, *, intake_source: str) -> SupplementProvenance:
    """Notbehelf ohne Ledger — Provider aus dem Dateinamen ableiten."""
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        parse_provider_identity_from_path,
    )

    identity = parse_provider_identity_from_path(media_path)
    if identity is not None:
        return SupplementProvenance(
            asset_id=identity.stable_asset_id,
            asset_origin=identity.provider,
            provider=identity.provider,
            provider_asset_id=identity.provider_asset_id,
            license_metadata={
                "provider": identity.provider,
                "provider_asset_id": identity.provider_asset_id,
            },
            intake_source=intake_source,
        )
    return SupplementProvenance(
        asset_id=f"supplement_{safe_folder_slug(media_path.stem)}",
        asset_origin="supplement",
        intake_source=intake_source,
    )


def _language_stock_dirs(project: Project) -> list[tuple[str, Path]]:
    """``[(LANG, stock_dir)]`` für alle Sprachen dieses Medienordners."""
    from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        _SHARED_WORK_DIR_NAMES,
    )
    from otio_app.services.without_voiceover_enhanced.paths import STOCK_SUBDIR

    work = project.work_dir_path
    if not work.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(work.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.lower() in _SHARED_WORK_DIR_NAMES:
            continue
        stock = child / VOICEOVER_GENERATION_SUBDIR / STOCK_SUBDIR
        if stock.is_dir():
            out.append((child.name, stock))
    return out


def _scan_ledgers(
    project: Project,
    known_paths: set[str],
    found: dict[str, RecoverableSupplement],
    report: SupplementRecoveryReport,
) -> None:
    from otio_app.services.without_voiceover_enhanced.io_utils import load_model
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        ACCEPTED_SUPPLEMENTS_FILENAME,
    )

    for lang, stock_dir in _language_stock_dirs(project):
        document = load_model(
            stock_dir / ACCEPTED_SUPPLEMENTS_FILENAME, AcceptedSupplementsDocument
        )
        if document is None:
            continue
        for candidate in document.supplements or []:
            media = _usable_media(getattr(candidate, "local_media_path", None))
            if media is None:
                continue
            key = str(media)
            if key in found:
                continue
            gap_id = str(getattr(candidate, "gap_id", "") or "")
            folder = _resolve_folder(project, media, gap_id=gap_id)
            if not folder:
                report.unresolved.append(
                    f"{media.name} (Acceptance-Liste {lang}, Gap {gap_id or '?'}) "
                    "— Ordner nicht bestimmbar"
                )
                continue
            found[key] = RecoverableSupplement(
                media_path=media,
                folder_name=folder,
                provenance=_provenance_from_candidate(
                    candidate, intake_source=INTAKE_SOURCE_FUNNEL
                ),
                source=f"{_LEDGER_SOURCE_PREFIX}:{lang}",
                in_inventory=key in known_paths,
            )


def _scan_clean_manifests(
    project: Project,
    known_paths: set[str],
    found: dict[str, RecoverableSupplement],
) -> None:
    """Clean-Dateien, deren Original außerhalb des Medienordners lag."""
    root = project.project_root_path
    for folder, manifest in _clean_manifests(project):
        folder_dir = root / folder
        for entry in manifest.entries:
            media = _usable_media(entry.clean_path)
            if media is None:
                continue
            key = str(media)
            if key in found:
                continue
            original = Path(str(entry.original_path or ""))
            try:
                original.relative_to(folder_dir)
            except ValueError:
                pass
            else:
                # Clean-Fassung eines Originals — kein beschafftes Material.
                continue
            found[key] = RecoverableSupplement(
                media_path=media,
                folder_name=folder,
                provenance=_provenance_from_path(
                    media, intake_source=INTAKE_SOURCE_INBOX
                ),
                source=_CLEAN_SOURCE,
                in_inventory=key in known_paths,
            )


def _scan_stock_downloads(
    project: Project,
    known_paths: set[str],
    found: dict[str, RecoverableSupplement],
    report: SupplementRecoveryReport,
) -> None:
    for lang, stock_dir in _language_stock_dirs(project):
        downloads = stock_dir / "downloads"
        if not downloads.is_dir():
            continue
        for gap_dir in sorted(downloads.iterdir()):
            if not gap_dir.is_dir():
                continue
            for media in sorted(gap_dir.rglob("*")):
                candidate = _usable_media(media)
                if candidate is None:
                    continue
                key = str(candidate)
                if key in found:
                    continue
                folder = _resolve_folder(project, candidate, gap_id=gap_dir.name)
                if not folder:
                    report.unresolved.append(
                        f"{candidate.name} (Download {lang}/{gap_dir.name}) "
                        "— Ordner nicht bestimmbar"
                    )
                    continue
                found[key] = RecoverableSupplement(
                    media_path=candidate,
                    folder_name=folder,
                    provenance=_provenance_from_path(
                        candidate, intake_source=INTAKE_SOURCE_FUNNEL
                    ),
                    source=_DOWNLOAD_SOURCE,
                    in_inventory=key in known_paths,
                )


def scan_recoverable_supplements(
    project: Project,
    *,
    folder_names: Iterable[str] | None = None,
) -> tuple[list[RecoverableSupplement], SupplementRecoveryReport]:
    """Findet beschaffte Assets, die im geteilten Inventar fehlen könnten.

    Liefert auch Assets, die bereits im Inventar stehen (``in_inventory``) —
    deren Analysestand prüft ``supplement_inventory.list_supplement_assets``.
    """
    report = SupplementRecoveryReport()
    if not _is_enhanced(project):
        return [], report

    known_paths = _inventory_supplement_paths(project)
    found: dict[str, RecoverableSupplement] = {}
    _scan_ledgers(project, known_paths, found, report)
    _scan_clean_manifests(project, known_paths, found)
    _scan_stock_downloads(project, known_paths, found, report)

    items = list(found.values())
    if folder_names is not None:
        wanted = {name for name in folder_names if name}
        items = [item for item in items if item.folder_name in wanted]
    report.scanned = len(items)
    return items, report


def recover_supplements_into_inventory(
    project: Project,
    *,
    folder_names: Iterable[str] | None = None,
    use_api: bool = True,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> SupplementRecoveryReport:
    """Trägt gefundene Assets nach und analysiert sie wie Originale."""
    items, report = scan_recoverable_supplements(project, folder_names=folder_names)
    if dry_run or not items:
        return report

    for item in items:
        try:
            result = ingest_supplement_asset(
                project,
                folder_name=item.folder_name,
                media_path=item.media_path,
                provenance=item.provenance,
                use_api=use_api,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            report.failures.append(f"{item.media_path.name}: {exc}")
            continue

        report.recovered += 1
        report.recovered_by_folder[item.folder_name] = (
            report.recovered_by_folder.get(item.folder_name, 0) + 1
        )
        if result.status == "analyzed":
            report.analyzed += 1
        elif result.status == "cached":
            report.already_complete += 1
        elif not result.has_full_analysis:
            report.failed += 1
            report.failures.append(
                f"{item.media_path.name}: ohne Analyse übernommen"
                + (f" ({result.error})" if result.error else "")
            )
    return report
