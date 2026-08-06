"""Enhanced Funnel: Provider-Identität, Reuse vor Download, Inventar-Cleanup.

Mehrere Funnel-/Cut-Plan-Läufe (auch neue Sprachen im selben Medienordner)
können dasselbe Stock-Asset unter verschiedenen ``asset_id``/``candidate_id``
landen. Dann greifen max_asset_usage und Reuse-Abstand nicht mehr.

Dieses Modul:
1. normalisiert Provider-Identität (``pexels`` + ``123``),
2. findet bereits vorhandene Dateien (Inventar, Accepted inkl. Geschwister-
   Sprachen, ``_supplemental/``, Cut-Plan-Manifest),
3. räumt Inventar-Duplikate auf,
4. liefert stabile Reuse-Keys für Usage-/Abstand-Checks.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project
from otio_app.project_layout import (
    get_folder_inventory_path,
    language_folder_name,
    safe_folder_slug,
)
from otio_app.services.inventory_loader import load_folder_inventory, save_folder_inventory
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    ACCEPTED_SUPPLEMENTS_FILENAME,
    STOCK_SUBDIR,
    assert_enhanced_work_root,
)


# pexels_video_123 / pexels_photo_123 / pixabay_video_9 / supplement_pexels_123
_ASSET_ID_PROVIDER_RE = re.compile(
    r"^(?:supplement_)?(?P<provider>pexels|pixabay|openverse|wikimedia|archive|"
    r"adobe(?:_stock)?)"
    r"(?:_(?:video|photo|image|stock))?"
    r"_(?P<asset_id>[A-Za-z0-9]+)(?:__|$)",
    re.IGNORECASE,
)

# Dateiname: …_pexels_27608379.mp4 / reused_adobe_12345.mov
_FILENAME_PROVIDER_RE = re.compile(
    r"(?:^|_)(?P<provider>pexels|pixabay|openverse|wikimedia|archive|"
    r"adobe(?:_stock)?)"
    r"(?:_stock)?_(?P<asset_id>[A-Za-z0-9]+)(?:\.[^.]+)+$",
    re.IGNORECASE,
)

_SHARED_WORK_DIR_NAMES = frozenset(
    {
        "inventory",
        "clean",
        "clean_media",
        "config",
        "placeholders",
        "frames",
        "exports",
        "llm_runs",
    }
)


def normalize_provider(provider: str | None) -> str:
    text = (provider or "").strip().casefold().replace("-", "_")
    if text in {"adobe", "adobestock"}:
        return "adobe_stock"
    if text == "archive_org":
        return "archive"
    return text


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    provider_asset_id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.provider_asset_id}"

    @property
    def stable_asset_id(self) -> str:
        """Kanonische Asset-ID für Inventar / Usage (über Läufe hinweg)."""
        return f"supplement_{self.provider}_{safe_folder_slug(self.provider_asset_id)}"


@dataclass(frozen=True)
class ExistingProviderAsset:
    identity: ProviderIdentity
    path: Path
    asset_id: str
    source: str  # inventory | accepted | supplemental | cut_plan_manifest


@dataclass
class InventoryDuplicateGroup:
    folder_name: str
    identity: ProviderIdentity
    keep_asset_id: str
    keep_path: str
    remove_asset_ids: list[str] = field(default_factory=list)
    remove_paths: list[str] = field(default_factory=list)


@dataclass
class EnhancedCleanupReport:
    dry_run: bool = True
    groups: list[InventoryDuplicateGroup] = field(default_factory=list)
    inventory_pruned: int = 0
    accepted_rewritten: int = 0
    files_deleted: list[str] = field(default_factory=list)
    alias_notes: list[str] = field(default_factory=list)

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def duplicate_asset_count(self) -> int:
        return sum(len(g.remove_asset_ids) for g in self.groups)


def provider_identity_from_parts(
    provider: str | None,
    provider_asset_id: str | None,
) -> ProviderIdentity | None:
    prov = normalize_provider(provider)
    aid = str(provider_asset_id or "").strip()
    if not prov or not aid or aid.lower() in {"none", "null"}:
        return None
    return ProviderIdentity(provider=prov, provider_asset_id=aid)


def parse_provider_identity_from_asset_id(asset_id: str | None) -> ProviderIdentity | None:
    text = str(asset_id or "").strip()
    if not text:
        return None
    match = _ASSET_ID_PROVIDER_RE.match(text)
    if match is None:
        return None
    return provider_identity_from_parts(
        match.group("provider"), match.group("asset_id")
    )


def parse_provider_identity_from_path(path: str | Path | None) -> ProviderIdentity | None:
    name = Path(str(path or "")).name
    if not name:
        return None
    match = _FILENAME_PROVIDER_RE.search(name)
    if match is None:
        return None
    return provider_identity_from_parts(
        match.group("provider"), match.group("asset_id")
    )


def provider_identity_for_candidate(candidate: StockCandidate) -> ProviderIdentity | None:
    ident = provider_identity_from_parts(
        candidate.provider, candidate.provider_asset_id
    )
    if ident is not None:
        return ident
    return parse_provider_identity_from_asset_id(candidate.candidate_id)


def provider_identity_for_inventory_asset(asset: Any) -> ProviderIdentity | None:
    """Identity aus Inventar-Asset (candidate_id / provider / Metadata / Pfad)."""
    meta = getattr(asset, "license_metadata", None) or {}
    if isinstance(meta, dict):
        ident = provider_identity_from_parts(
            meta.get("provider") or getattr(asset, "provider", None),
            meta.get("provider_asset_id"),
        )
        if ident is not None:
            return ident
    ident = provider_identity_from_parts(
        getattr(asset, "provider", None),
        getattr(asset, "provider_asset_id", None),
    )
    if ident is not None:
        return ident
    ident = parse_provider_identity_from_asset_id(getattr(asset, "asset_id", None))
    if ident is not None:
        return ident
    return parse_provider_identity_from_path(getattr(asset, "path", None))


def reuse_identity_key(
    asset_id: str | None,
    *,
    index: Mapping[str, str] | None = None,
) -> str:
    """Key für max_usage / Reuse-Abstand — Provider-ID wenn erkennbar.

    ``index`` (aus ``build_asset_reuse_key_index``) mappt Funnel-IDs wie
    ``pexels_photo_001`` auf die echte ``provider_asset_id`` (z. B. ``1001``).
    """
    text = str(asset_id or "").strip()
    if not text:
        return ""
    if index and text in index:
        return index[text]
    ident = parse_provider_identity_from_asset_id(text)
    if ident is not None:
        return ident.key
    return text


def build_asset_reuse_key_index(project: Project) -> dict[str, str]:
    """``asset_id → provider:provider_asset_id`` aus Inventar + Accepted."""
    index: dict[str, str] = {}

    folders = list(
        project.asset_subdir_names or project.selected_asset_subdirs or []
    )
    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in inventory.assets or []:
            aid = str(getattr(asset, "asset_id", "") or "").strip()
            if not aid:
                continue
            ident = provider_identity_for_inventory_asset(asset)
            if ident is not None:
                index[aid] = ident.key

    for _lang, accepted_path in _iter_sibling_language_accepted_paths(project):
        doc = load_model(accepted_path, AcceptedSupplementsDocument)
        if doc is None:
            continue
        for supplement in doc.supplements or []:
            cid = str(supplement.candidate_id or "").strip()
            ident = provider_identity_for_candidate(supplement)
            if cid and ident is not None:
                index[cid] = ident.key
            if ident is not None:
                index[ident.stable_asset_id] = ident.key
    return index


def _path_ok(path: Path | str | None) -> Path | None:
    text = str(path or "").strip()
    if not text or is_http_url(text):
        return None
    candidate = Path(text)
    try:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    except OSError:
        return None
    return None


def _iter_sibling_language_accepted_paths(project: Project) -> list[tuple[str, Path]]:
    """Aktuelle + Geschwister-Sprachen mit accepted_supplements.json."""
    work = assert_enhanced_work_root(project)
    current = language_folder_name(project.language)
    out: list[tuple[str, Path]] = []
    if not work.is_dir():
        return out
    for child in sorted(work.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        name = child.name.strip()
        if not name or name.lower() in _SHARED_WORK_DIR_NAMES:
            continue
        path = (
            child
            / VOICEOVER_GENERATION_SUBDIR
            / STOCK_SUBDIR
            / ACCEPTED_SUPPLEMENTS_FILENAME
        )
        if path.is_file():
            out.append((name, path))
    # Aktuelle Sprache zuerst
    out.sort(key=lambda item: (0 if item[0] == current else 1, item[0].lower()))
    return out


def find_existing_enhanced_provider_asset(
    project: Project,
    *,
    provider: str,
    provider_asset_id: str,
    folder_name: str | None = None,
) -> ExistingProviderAsset | None:
    """Erste brauchbare lokale Datei für dieselbe Provider-Asset-ID."""
    identity = provider_identity_from_parts(provider, provider_asset_id)
    if identity is None:
        return None

    # 1) Ordner-Inventar (shared across languages)
    folders: list[str] = []
    if folder_name:
        folders.append(folder_name)
    folders.extend(
        name
        for name in (project.asset_subdir_names or project.selected_asset_subdirs or [])
        if name and name not in folders
    )
    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in inventory.assets or []:
            asset_ident = provider_identity_for_inventory_asset(asset)
            if asset_ident is None or asset_ident.key != identity.key:
                continue
            path = _path_ok(getattr(asset, "path", None))
            if path is None:
                continue
            asset_id = str(getattr(asset, "asset_id", "") or identity.stable_asset_id)
            return ExistingProviderAsset(
                identity=identity,
                path=path,
                asset_id=asset_id,
                source="inventory",
            )

    # 2) Accepted supplements (alle Sprachen)
    for lang, accepted_path in _iter_sibling_language_accepted_paths(project):
        doc = load_model(accepted_path, AcceptedSupplementsDocument)
        if doc is None:
            continue
        for supplement in doc.supplements or []:
            status = str(
                getattr(supplement, "media_validation_status", "") or ""
            ).strip()
            if status and status != STATUS_EXPORT_READY:
                continue
            cand_ident = provider_identity_for_candidate(supplement)
            if cand_ident is None or cand_ident.key != identity.key:
                continue
            path = _path_ok(getattr(supplement, "local_media_path", None))
            if path is None:
                continue
            return ExistingProviderAsset(
                identity=identity,
                path=path,
                asset_id=str(
                    getattr(supplement, "candidate_id", "") or identity.stable_asset_id
                ),
                source=f"accepted:{lang}",
            )

    # 3) Production `_supplemental/`
    if folder_name:
        try:
            from otio_app.services.supplement_dedupe import find_existing_provider_asset

            path = find_existing_provider_asset(
                project,
                folder_name,
                provider=identity.provider,
                provider_asset_id=identity.provider_asset_id,
            )
            if path is not None:
                return ExistingProviderAsset(
                    identity=identity,
                    path=path,
                    asset_id=identity.stable_asset_id,
                    source="supplemental",
                )
        except Exception:  # noqa: BLE001
            pass

    # 4) Voiceover cut-plan supplement manifest (language-scoped)
    try:
        from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
            find_reusable_supplement_manifest_entry,
        )

        entry = find_reusable_supplement_manifest_entry(
            project, identity.provider, identity.provider_asset_id
        )
        if entry is not None:
            path = _path_ok(getattr(entry, "local_path", None))
            if path is not None:
                return ExistingProviderAsset(
                    identity=identity,
                    path=path,
                    asset_id=str(
                        getattr(entry, "asset_id", "") or identity.stable_asset_id
                    ),
                    source="cut_plan_manifest",
                )
    except Exception:  # noqa: BLE001
        pass

    return None


def resolve_or_reuse_candidate_media(
    project: Project,
    candidate: StockCandidate,
    *,
    gap_id: str,
    folder_name: str | None = None,
    download_callable=None,
) -> tuple[Path, bool]:
    """``(media_path, reused)`` — reused=True wenn kein Netz-Download nötig.

    ``candidate_id`` bleibt unverändert (Funnel-Report-Keys); Inventar-Import
    mappt über ``preferred_inventory_asset_id`` auf die stabile/Reuse-ID.
    """
    identity = provider_identity_for_candidate(candidate)
    if identity is not None:
        existing = find_existing_enhanced_provider_asset(
            project,
            provider=identity.provider,
            provider_asset_id=identity.provider_asset_id,
            folder_name=folder_name,
        )
        if existing is not None:
            candidate.local_media_path = str(existing.path)
            return existing.path, True

    if download_callable is not None:
        path = download_callable(project, candidate, gap_id=gap_id)
    else:
        from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
            download_full_candidate_safe,
        )

        path = download_full_candidate_safe(project, candidate, gap_id=gap_id)
    return Path(path), False


def preferred_inventory_asset_id(
    project: Project,
    candidate: StockCandidate,
    *,
    folder_name: str | None = None,
) -> str:
    """Inventar-ID: vorhandene Provider-ID > Funnel-candidate_id > stable.

    Funnel-``candidate_id`` (z. B. ``pexels_photo_001``) wird beibehalten, auch
    wenn die numerische Kodierung von ``provider_asset_id`` abweicht. Usage/
    Abstand zählen über ``reuse_identity_key`` provider-kanonisch.
    """
    identity = provider_identity_for_candidate(candidate)
    cid = str(candidate.candidate_id or "").strip()
    if identity is not None:
        existing = find_existing_enhanced_provider_asset(
            project,
            provider=identity.provider,
            provider_asset_id=identity.provider_asset_id,
            folder_name=folder_name,
        )
        if existing is not None and existing.asset_id:
            return existing.asset_id
        if cid:
            return cid
        return identity.stable_asset_id
    return cid or "supplement_unknown"


def _keeper_inventory_rank(asset: Any) -> tuple:
    path = str(getattr(asset, "path", "") or "")
    clean_bonus = 1 if "/clean/" in path.replace("\\", "/").lower() else 0
    approved = 1 if getattr(asset, "approved_for_cut_plan", False) else 0
    status = str(getattr(asset, "supplement_validation_status", "") or "")
    pass_bonus = 1 if status.upper() == "PASS" else 0
    try:
        size = Path(path).stat().st_size if path else 0
    except OSError:
        size = 0
    # Prefer stable supplement_* ids slightly
    asset_id = str(getattr(asset, "asset_id", "") or "")
    stable_bonus = 1 if asset_id.startswith("supplement_") else 0
    return (clean_bonus, approved, pass_bonus, stable_bonus, size)


def _inventory_folder_names(
    project: Project,
    folder_names: Iterable[str] | None = None,
) -> list[str]:
    """Ordner aus Argument, Projektliste und vorhandenen Inventory-JSONs."""
    if folder_names is not None:
        return [name for name in folder_names if str(name or "").strip()]

    names: list[str] = []
    for name in project.asset_subdir_names or []:
        if name and name not in names:
            names.append(name)
    for name in project.selected_asset_subdirs or []:
        if name and name not in names:
            names.append(name)

    # Alle Inventory-JSONs unter _otio_enhanced/inventory/ — auch wenn die
    # Projektliste unvollständig ist (sonst Scan = 0 und Button bleibt grau).
    try:
        from otio_app.project_layout import get_inventory_dir

        inv_dir = get_inventory_dir(project.work_dir_path)
        if inv_dir.is_dir():
            for path in sorted(inv_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                folder = str(payload.get("folder") or "").strip()
                if folder and folder not in names:
                    names.append(folder)
    except Exception:  # noqa: BLE001
        pass
    return names


def _resolved_path_key(raw_path: str | None) -> str | None:
    path = _path_ok(raw_path)
    if path is None:
        return None
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return f"path:{resolved}"


def _content_hash_key(raw_path: str | None) -> str | None:
    """SHA256 nur für Supplement-/Stock-Downloads (nicht alle Originale)."""
    path = _path_ok(raw_path)
    if path is None:
        return None
    norm = str(path).replace("\\", "/").lower()
    if "stock/downloads" not in norm and "/_supplemental/" not in norm:
        return None
    try:
        from otio_app.services.media_utils import file_sha256

        digest = file_sha256(path)
    except Exception:  # noqa: BLE001
        return None
    if not digest:
        return None
    return f"sha256:{digest}"


def _identity_for_group_key(key: str, sample: Any) -> ProviderIdentity:
    ident = provider_identity_for_inventory_asset(sample)
    if ident is not None:
        return ident
    # Synthetische Identity für Pfad-/Hash-Gruppen (UI + Cleanup).
    kind, _, rest = key.partition(":")
    slug = safe_folder_slug((rest or key)[:48]) or "dup"
    return ProviderIdentity(provider=kind or "dup", provider_asset_id=slug)


def scan_enhanced_inventory_duplicates(
    project: Project,
    folder_names: Iterable[str] | None = None,
) -> list[InventoryDuplicateGroup]:
    """Inventar-Duplikate: gleiche Provider-ID, gleicher Dateipfad oder gleicher Hash."""
    folders = _inventory_folder_names(project, folder_names)
    groups: list[InventoryDuplicateGroup] = []
    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        by_key: dict[str, list[Any]] = {}
        for asset in inventory.assets or []:
            keys: list[str] = []
            ident = provider_identity_for_inventory_asset(asset)
            if ident is not None:
                keys.append(ident.key)
            path_key = _resolved_path_key(getattr(asset, "path", None))
            if path_key:
                keys.append(path_key)
            hash_key = _content_hash_key(getattr(asset, "path", None))
            if hash_key:
                keys.append(hash_key)
            # Deduplizierte Keys pro Asset, damit ein Asset nicht mehrfach
            # in derselben Gruppe landet.
            for key in dict.fromkeys(keys):
                by_key.setdefault(key, []).append(asset)

        # Assets können über mehrere Keys verknüpft sein (provider + path).
        # Union-Find über Asset-IDs, damit z. B. A~B (path) und B~C (provider)
        # eine Gruppe werden.
        parent: dict[str, str] = {}

        def _find(x: str) -> str:
            root = x
            while parent.get(root, root) != root:
                root = parent[root]
            while parent.get(x, x) != x:
                nxt = parent[x]
                parent[x] = root
                x = nxt
            return root

        def _union(a: str, b: str) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[rb] = ra

        asset_by_id: dict[str, Any] = {}
        for assets in by_key.values():
            ids = []
            for asset in assets:
                aid = str(getattr(asset, "asset_id", "") or "").strip()
                if not aid:
                    continue
                asset_by_id[aid] = asset
                parent.setdefault(aid, aid)
                ids.append(aid)
            for left, right in zip(ids, ids[1:]):
                _union(left, right)

        clusters: dict[str, list[Any]] = {}
        for aid, asset in asset_by_id.items():
            clusters.setdefault(_find(aid), []).append(asset)

        for _root, assets in sorted(clusters.items()):
            # Einzigartige Asset-IDs
            unique: dict[str, Any] = {}
            for asset in assets:
                aid = str(getattr(asset, "asset_id", "") or "").strip()
                if aid and aid not in unique:
                    unique[aid] = asset
            if len(unique) < 2:
                continue
            ranked = sorted(unique.values(), key=_keeper_inventory_rank, reverse=True)
            keep = ranked[0]
            remove = ranked[1:]
            # Prefer a real provider key for the group label when available.
            sample_key = next(
                (
                    k
                    for k, bucket in by_key.items()
                    if any(
                        str(getattr(a, "asset_id", "")) == str(keep.asset_id)
                        for a in bucket
                    )
                    and ":" in k
                    and not k.startswith("path:")
                    and not k.startswith("sha256:")
                ),
                None,
            )
            if sample_key is None:
                sample_key = (
                    _resolved_path_key(getattr(keep, "path", None))
                    or _content_hash_key(getattr(keep, "path", None))
                    or f"dup:{keep.asset_id}"
                )
            ident = _identity_for_group_key(sample_key, keep)
            groups.append(
                InventoryDuplicateGroup(
                    folder_name=folder,
                    identity=ident,
                    keep_asset_id=str(keep.asset_id or ident.stable_asset_id),
                    keep_path=str(keep.path or ""),
                    remove_asset_ids=[
                        str(a.asset_id) for a in remove if str(a.asset_id or "").strip()
                    ],
                    remove_paths=[
                        str(a.path)
                        for a in remove
                        if str(a.path or "").strip()
                        and str(a.path) != str(keep.path or "")
                    ],
                )
            )
    return groups


def cleanup_enhanced_inventory_duplicates(
    project: Project,
    folder_names: Iterable[str] | None = None,
    *,
    dry_run: bool = True,
    delete_orphan_files: bool = False,
) -> EnhancedCleanupReport:
    """Klappt Inventar-Duplikate zusammen; optional verwaiste Download-Dateien löschen.

    Cut-Plan-Slots, die entfernte IDs referenzieren, sollten nach Cleanup den
    Cut neu erzeugen — Usage/Abstand zählen aber bereits über
    ``reuse_identity_key`` provider-kanonisch.
    """
    groups = scan_enhanced_inventory_duplicates(project, folder_names)
    report = EnhancedCleanupReport(dry_run=dry_run, groups=groups)
    if dry_run or not groups:
        return report

    remove_ids_global: set[str] = set()
    keep_by_remove: dict[str, str] = {}
    for group in groups:
        inventory = load_folder_inventory(project, group.folder_name)
        if inventory is None:
            continue
        remove_set = set(group.remove_asset_ids)
        remove_ids_global.update(remove_set)
        for rid in group.remove_asset_ids:
            keep_by_remove[rid] = group.keep_asset_id
        keep_paths = {group.keep_path}
        new_assets = []
        for asset in inventory.assets or []:
            aid = str(asset.asset_id or "")
            if aid in remove_set:
                report.inventory_pruned += 1
                continue
            new_assets.append(asset)
            if str(asset.path or "") == group.keep_path:
                keep_paths.add(str(asset.path))
        media_files = [
            m
            for m in (inventory.media_files or [])
            if m == group.keep_path or m not in set(group.remove_paths)
        ]
        if group.keep_path and group.keep_path not in media_files:
            media_files.append(group.keep_path)
        folder_doc = inventory.model_copy(
            update={"assets": new_assets, "media_files": media_files}
        )
        save_folder_inventory(
            get_folder_inventory_path(project.work_dir_path, group.folder_name),
            folder_doc,
        )
        if delete_orphan_files:
            for raw in group.remove_paths:
                path = Path(raw)
                # Nur stock/downloads löschen — nie Clean/Originale.
                norm = str(path).replace("\\", "/").lower()
                if "stock/downloads" not in norm and "/_supplemental/" not in norm:
                    continue
                if not path.is_file():
                    continue
                try:
                    path.unlink()
                    report.files_deleted.append(str(path))
                    sidecar = path.with_suffix(path.suffix + ".asset.json")
                    if sidecar.is_file():
                        sidecar.unlink()
                    # Leeres Kandidaten-Verzeichnis
                    parent = path.parent
                    if parent.is_dir() and not any(parent.iterdir()):
                        shutil.rmtree(parent, ignore_errors=True)
                except OSError:
                    continue

    # Accepted lists: candidate_id auf Keeper umbiegen (alle Sprachen)
    for _lang, accepted_path in _iter_sibling_language_accepted_paths(project):
        doc = load_model(accepted_path, AcceptedSupplementsDocument)
        if doc is None:
            continue
        changed = False
        updated: list[StockCandidate] = []
        seen_keys: set[str] = set()
        for supplement in doc.supplements or []:
            cid = str(supplement.candidate_id or "")
            ident = provider_identity_for_candidate(supplement)
            if cid in keep_by_remove:
                keeper = keep_by_remove[cid]
                supplement = supplement.model_copy(update={"candidate_id": keeper})
                changed = True
                report.accepted_rewritten += 1
                report.alias_notes.append(f"{cid} → {keeper}")
                cid = keeper
            if ident is not None:
                if ident.key in seen_keys:
                    changed = True
                    continue
                seen_keys.add(ident.key)
            updated.append(supplement)
        if changed:
            write_json(
                accepted_path,
                AcceptedSupplementsDocument(
                    script_version=doc.script_version,
                    supplements=updated,
                ),
            )

    return report


def rewrite_plan_asset_ids_to_keepers(
    slots: Iterable[Any],
    keep_by_remove: dict[str, str],
) -> int:
    """Optional: Cut-Plan-Slots von Duplikat-IDs auf Keeper umbiegen."""
    changed = 0
    for slot in slots:
        aid = str(getattr(slot, "local_asset_id", None) or "").strip()
        if aid and aid in keep_by_remove:
            setattr(slot, "local_asset_id", keep_by_remove[aid])
            changed += 1
    return changed
