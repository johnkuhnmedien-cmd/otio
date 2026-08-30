"""Python-Finalisierung: Anchors → Sekunden, Validierung, Reparaturprotokoll."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    probe_duration_seconds,
    probe_media_timing,
)
from otio_app.services.without_voiceover_enhanced.asset_identity import (
    canonicalize_inventory_asset_id,
    is_legacy_ambiguous_asset_id,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
    resolve_timing_seconds,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    find_local_media_for_candidate_id,
    is_http_url,
    list_export_ready_supplements,
)
from otio_app.services.without_voiceover_enhanced.media_hold import (
    MediaHoldError,
    ensure_last_frame_hold,
    ensure_still_hold_video,
    ensure_video_padded_hold,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    FinalCutPlanDocument,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    final_cut_plan_path,
    narration_timeline_path,
    repair_log_path,
    resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    source_seconds_to_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    sentence_index_by_id,
)

# Technisches Minimum (Frame-Sicherheit); redaktionelle min/max kommen aus Settings.
TECH_MIN_SHOT_SECONDS = 0.4
TECH_MAX_SHOT_SECONDS = 120.0


class TimelineResolveError(RuntimeError):
    pass


@dataclass
class AssetCatalog:
    """Eindeutige Asset-Einträge; Kollisionen und Legacy-Aliase separat."""

    by_id: dict[str, dict] = field(default_factory=dict)
    collisions: list[str] = field(default_factory=list)
    legacy_to_ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


_RESOLUTION_STEM_SUFFIX_RE = re.compile(r"_\d{3,5}x\d{3,5}$", re.IGNORECASE)
_STILL_MEDIA_KINDS = frozenset({"image", "photo", "still"})


def still_image_path_from_catalog_entry(
    entry: dict[str, Any] | None,
    media_path: Path | str | None = None,
) -> Path | None:
    """Echtes Foto hinter einem Katalogeintrag — nicht das 5s-Clean-MP4.

    Motion-MP4s (auch falsch als ``image`` getaggt) liefern ``None``, damit
    ffmpeg nicht ``-loop 1`` auf Video anwendet.
    """
    candidates: list[Path] = []
    if entry:
        raw = str(entry.get("original_image_path") or "").strip()
        if raw:
            candidates.append(Path(raw))
        path_raw = str(entry.get("path") or "").strip()
        if path_raw:
            candidates.append(Path(path_raw))
    if media_path is not None and str(media_path).strip():
        candidates.append(Path(str(media_path)))
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file() and is_image_media(path) and not is_video_media(path):
                return path.resolve()
        except OSError:
            continue
    return None


def _catalog_still_image_for_paths(
    project: Project,
    folder: str,
    *paths: Path | str | None,
) -> Path | None:
    """Foto-Original aus Inventarpfad oder Clean-Manifest."""
    from otio_app.services.clean_media import original_still_image_for_clean

    for raw in paths:
        if raw is None or not str(raw).strip():
            continue
        path = Path(str(raw))
        try:
            if path.is_file() and is_image_media(path) and not is_video_media(path):
                return path.resolve()
        except OSError:
            pass
        try:
            found = original_still_image_for_clean(project, folder, path)
        except Exception:  # noqa: BLE001
            found = None
        if found is not None:
            return found
    return None


def _resolve_local_path(project: Project, raw: str | Path) -> Path:
    path = Path(str(raw)).expanduser()
    if path.is_file():
        return path.resolve()
    roots: list[Path] = [
        Path(project.project_root).expanduser(),
        project.work_dir_path.expanduser(),
    ]
    try:
        roots.append(project.language_work_dir_path.expanduser())
    except Exception:  # noqa: BLE001
        pass
    try:
        from otio_app.services.without_voiceover_enhanced.paths import (
            iter_language_editorial_dirs,
        )

        roots.extend(iter_language_editorial_dirs(project))
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            candidate = (root / path).resolve()
        except OSError:
            continue
        if candidate.is_file():
            return candidate

    # Absoluter DE-Pfad, Datei liegt inzwischen unter IT (oder umgekehrt).
    try:
        from otio_app.services.without_voiceover_enhanced.paths import (
            iter_language_editorial_dirs,
        )

        text = str(path)
        posix = text.replace("\\", "/")
        for lang_dir in iter_language_editorial_dirs(project):
            token = f"/{lang_dir.name}/"
            if token.lower() in posix.lower():
                continue
            swapped = _swap_language_folder_in_path(text, lang_dir.name)
            if swapped is not None and swapped.is_file():
                return swapped.resolve()
    except Exception:  # noqa: BLE001
        pass
    return path


def _swap_language_folder_in_path(raw: str, target_lang: str) -> Path | None:
    """``…/_otio_enhanced/DE/voiceover_generation/…`` → ``…/IT/…``."""
    posix = raw.replace("\\", "/")
    marker = "/voiceover_generation/"
    idx = posix.lower().rfind(marker)
    if idx < 0:
        return None
    head = posix[:idx]
    tail = posix[idx:]
    slash = head.rfind("/")
    if slash < 0:
        return None
    swapped = f"{head[:slash]}/{target_lang}{tail}"
    if raw.startswith("\\\\") or (len(raw) >= 2 and raw[1] == ":"):
        # Windows-Pfad: POSIX-Tausch reicht nicht — nur POSIX-Fälle.
        if "\\" in raw and "/" not in raw:
            swapped = swapped.replace("/", "\\")
    return Path(swapped)


def _catalog_media_stem_key(path: Path | str) -> str:
    """Dateiname ohne Extension und ohne ``_3840x2160`` — jpg und Clean-mp4 treffen sich."""
    stem = Path(str(path)).stem.strip()
    if not stem:
        return ""
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return _RESOLUTION_STEM_SUFFIX_RE.sub("", collapsed).strip("_")


def _stem_slug_variants(stem: str) -> list[str]:
    """Slug-Varianten für Stem-Legacy-IDs.

    Slim / ``asset_id_for_path`` kollabiert Nicht-Alnum (inkl. ``&`` + Spaces)
    zu einem ``_``. Die ältere char-weise Variante erzeugt bei ``&`` dagegen
    ``___`` — beides als Alias registrieren, Lookup bleibt kompatibel.
    """
    text = (stem or "").strip()
    if not text:
        return []
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    raw = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_").lower()
    out: list[str] = []
    for slug in (collapsed, raw):
        if slug and slug not in out:
            out.append(slug)
    return out


def _stem_legacy_asset_ids(path: Path | str) -> list[str]:
    """Legacy-IDs aus Dateiname — inkl. ohne Clean-Suffix ``_3840x2160``."""
    stem = Path(path).stem.strip()
    aliases: list[str] = []
    for slug in _stem_slug_variants(stem):
        aliases.append(f"asset_{slug}")
        stripped = _RESOLUTION_STEM_SUFFIX_RE.sub("", slug).strip("_")
        if stripped and stripped != slug:
            aliases.append(f"asset_{stripped}")
    # stabil, ohne Duplikate
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        if alias not in seen:
            seen.add(alias)
            out.append(alias)
    return out


def _slim_id_filename_map(project: Project, folder: str) -> dict[str, str]:
    """Slim-Inventar: ``local_asset_id`` → Dateiname (LLM-Cut-Quelle)."""
    from otio_app.project_layout import get_folder_inventory_path
    from otio_app.services.inventory_prompt_view import (
        load_slim_folder_inventory_file,
        slim_inventory_path_for,
    )

    slim_path = slim_inventory_path_for(
        get_folder_inventory_path(project.work_dir_path, folder)
    )
    doc = load_slim_folder_inventory_file(slim_path)
    if doc is None:
        return {}
    out: dict[str, str] = {}
    for item in doc.get("assets") or []:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "").strip()
        file_name = str(item.get("file") or "").strip()
        if asset_id and file_name:
            out[asset_id] = file_name
    return out


def _preferred_stem_legacy_id(path: Path | str) -> str:
    """Cut-Plan-/Slim-kompatible Stem-ID (kollabiert, ohne ``_3840x2160``)."""
    aliases = _stem_legacy_asset_ids(path)
    if not aliases:
        return ""
    # `_stem_legacy_asset_ids` listet zuerst die kollabierte Slim-Form; darunter
    # Varianten ohne Auflösungs-Suffix bevorzugen.
    without_resolution: list[str] = []
    for alias in aliases:
        slug = alias[6:] if alias.startswith("asset_") else alias
        if not _RESOLUTION_STEM_SUFFIX_RE.search(slug):
            without_resolution.append(alias)
    return without_resolution[0] if without_resolution else aliases[0]


def _catalog_folder_names(project: Project) -> list[str]:
    """Ordner für den Asset-Katalog — nicht nur die UI-Auswahl.

    LLM-Cuts laufen auch für Kapitel außerhalb von ``selected_asset_subdirs``;
    Python Timing muss dieselben Inventare sehen, sonst „Unbekannte Asset-ID“.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        text = (raw or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        names.append(text)

    for raw in list(project.selected_asset_subdirs or []):
        _add(str(raw))
    for raw in list(project.asset_subdir_names or []):
        _add(str(raw))
    try:
        from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
            list_body_chapter_names,
        )

        for raw in list_body_chapter_names(project):
            _add(str(raw))
    except Exception:  # noqa: BLE001
        pass
    return names


def _asset_number_from_id_or_name(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(r"(?:^|_)asset0*([0-9]+)(?:_|$)", raw, flags=re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    match = re.search(r"asset0*([0-9]+)", Path(raw).stem, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _iter_folder_media_candidates(project: Project, folder: str) -> list[Path]:
    """Top-Level + Clean + Supplemental — für Recovery und Disk-Index."""
    from otio_app.services.clean_media import (
        discover_supplemental_media_paths,
        list_clean_files_in_folder,
    )
    from otio_app.services.media_inventory_cache import discover_folder_media_paths

    found: list[Path] = []
    seen: set[str] = set()

    def _push(path: Path | None) -> None:
        if path is None:
            return
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    try:
        for path in discover_folder_media_paths(project, folder):
            _push(path)
    except Exception:  # noqa: BLE001
        pass
    try:
        for path in list_clean_files_in_folder(project, folder):
            _push(path)
    except Exception:  # noqa: BLE001
        pass
    try:
        for path in discover_supplemental_media_paths(project, folder):
            _push(path)
    except Exception:  # noqa: BLE001
        pass
    return found


def _recover_inventory_media_path(
    project: Project,
    folder: str,
    *,
    raw_path: str | Path,
    asset_id: str = "",
    candidates: list[Path] | None = None,
) -> Path | None:
    """Wenn Inventory-Pfad fehlt (verwaistes Clean): Original/Clean per Stem/Nummer suchen."""
    from otio_app.services.clean_media import (
        clean_file_is_present,
        find_clean_file_for_media,
        media_asset_number,
        media_stem_key,
    )

    name = Path(str(raw_path)).name or Path(str(raw_path)).stem
    if not name:
        return None
    stem_key = media_stem_key(Path(name))
    base_key = _RESOLUTION_STEM_SUFFIX_RE.sub("", stem_key).strip("_")
    number = media_asset_number(Path(name))
    if number is None:
        number = _asset_number_from_id_or_name(asset_id) or _asset_number_from_id_or_name(
            name
        )

    search_paths = (
        candidates
        if candidates is not None
        else _iter_folder_media_candidates(project, folder)
    )
    for candidate in search_paths:
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        cand_key = media_stem_key(candidate)
        cand_base = _RESOLUTION_STEM_SUFFIX_RE.sub("", cand_key).strip("_")
        if cand_key == stem_key or (base_key and cand_base == base_key):
            return candidate.resolve()
        if number is not None and media_asset_number(candidate) == number:
            return candidate.resolve()

    fake_original = Path(project.project_root).expanduser() / folder / name
    try:
        clean = find_clean_file_for_media(project, folder, fake_original)
    except Exception:  # noqa: BLE001
        clean = None
    if clean_file_is_present(clean):
        try:
            return Path(clean).resolve()
        except OSError:
            return Path(clean).expanduser()
    stock_id = str(asset_id or "").strip()
    if stock_id:
        recovered_stock = find_local_media_for_candidate_id(
            project, candidate_id=stock_id, folder_name=folder
        )
        if recovered_stock is not None and recovered_stock.is_file():
            return recovered_stock.resolve()
    return None


def _probe_entry(
    project: Project,
    *,
    path: Path,
    folder: str,
    asset_id: str,
    usable_in: float | None,
    media_type_hint: str,
    fps: float,
    known_duration: float | None = None,
    probe_cache: dict[str, dict] | None = None,
    original_image_path: Path | None = None,
) -> dict:
    hint = str(media_type_hint or "").strip().lower()
    still_original = original_image_path
    if still_original is None and is_image_media(path) and not is_video_media(path):
        still_original = path
    is_still = still_original is not None or hint in _STILL_MEDIA_KINDS
    kind = "image" if is_still else "video"
    type_label = "photo" if kind == "image" else (hint or "video")
    try:
        cache_key = str(path.expanduser().resolve())
    except OSError:
        cache_key = str(path)

    if probe_cache is not None and cache_key in probe_cache:
        cached = dict(probe_cache[cache_key])
        cached["folder"] = folder
        cached["canonical_id"] = asset_id
        cached["media_type"] = type_label or cached.get("media_type") or kind
        cached["media_kind"] = kind
        if still_original is not None:
            cached["original_image_path"] = str(still_original)
        if usable_in is not None:
            cached["usable_in_s"] = float(usable_in)
        return cached

    # Clean-Dateien starten bei TC 00:00:00:00 — volles Timecode-ffprobe sparen,
    # wenn die Dauer schon aus dem Inventar bekannt ist.
    path_text = cache_key.replace("\\", "/").lower()
    is_clean = "/clean/" in path_text
    duration: float | None = float(known_duration) if known_duration is not None else None
    start_sec = 0.0
    rate = float(fps)

    if duration is not None and is_clean:
        pass
    else:
        try:
            timing = probe_media_timing(path, default_rate=fps)
            if duration is None:
                duration = timing.duration_sec
            start_sec = float(timing.start_sec or 0.0)
            rate = float(timing.rate or fps)
        except Exception:  # noqa: BLE001 — Katalog darf an einem kaputten Clip nicht sterben
            start_sec = 0.0
            rate = float(fps)
        if duration is None:
            try:
                duration = probe_duration_seconds(path)
            except Exception:  # noqa: BLE001
                duration = None

    entry = {
        "path": str(path),
        "duration_seconds": float(duration) if duration else None,
        "usable_in_s": float(usable_in) if usable_in is not None else None,
        "folder": folder,
        "media_type": type_label,
        "media_kind": kind,
        "available_start_seconds": start_sec,
        "media_rate": rate,
        "canonical_id": asset_id,
    }
    if still_original is not None:
        entry["original_image_path"] = str(still_original)
    if probe_cache is not None:
        probe_cache[cache_key] = dict(entry)
    return entry


def _catalog_path_kind(path: str) -> str:
    """stock_download | clean | other — für Supplement-Doppelpfade."""
    text = str(path or "").replace("\\", "/").lower()
    if "/stock/downloads/" in text:
        return "stock_download"
    if "/clean/" in text:
        return "clean"
    return "other"


def build_asset_catalog(
    project: Project,
    *,
    fps: float = 25.0,
    folder_names: Iterable[str] | None = None,
) -> AssetCatalog:
    """Baut eindeutigen Katalog; doppelte explizite IDs → collisions.

    Ausnahme: dieselbe ID unter ``stock/downloads`` und ``clean/`` — Clean gewinnt
    (Supplement-Download + Clean-Kopie), kein Hard-Collision.

    ``folder_names``: optional nur diese Ordner indexieren (Kapitel-Timing).
    Ohne Angabe: selected + asset_subdir_names + Körper-Kapitel.
    """
    result = AssetCatalog()
    explicit_paths: dict[str, list[str]] = defaultdict(list)
    folder_media_cache: dict[str, list[Path]] = {}
    probe_cache: dict[str, dict] = {}

    def _folder_candidates(folder: str) -> list[Path]:
        cached = folder_media_cache.get(folder)
        if cached is None:
            cached = _iter_folder_media_candidates(project, folder)
            folder_media_cache[folder] = cached
        return cached

    def _stem_already_registered(path: Path | str) -> bool:
        for stem_alias in _stem_legacy_asset_ids(path):
            if stem_alias in result.by_id or (result.legacy_to_ids.get(stem_alias) or []):
                return True
        return False

    def _register(
        entry_id: str,
        entry: dict,
        *,
        raw_id: str,
        alias_paths: Iterable[Path | str] | None = None,
    ) -> None:
        path = str(entry["path"])
        if entry_id in result.by_id and result.by_id[entry_id]["path"] != path:
            existing_path = str(result.by_id[entry_id]["path"])
            kinds = {
                _catalog_path_kind(existing_path),
                _catalog_path_kind(path),
            }
            if kinds == {"stock_download", "clean"}:
                # Clean ist kanonisch; Download-Pfad stillschweigend verwerfen.
                if _catalog_path_kind(path) == "clean":
                    result.by_id[entry_id] = entry
                return
            explicit_paths[entry_id].append(path)
            explicit_paths[entry_id].append(existing_path)
            return
        if entry_id in result.by_id:
            return
        result.by_id[entry_id] = entry
        if is_legacy_ambiguous_asset_id(raw_id):
            if entry_id not in result.legacy_to_ids[raw_id]:
                result.legacy_to_ids[raw_id].append(entry_id)
        # Stem-Legacy aus Dateiname(n) — Clean ``…_3840x2160`` muss weiterhin
        # Cut-Plan-IDs wie ``asset_caddo_lake_asset10`` treffen.
        stem_sources = [path]
        if alias_paths:
            stem_sources.extend(str(p) for p in alias_paths if p)
        for source in stem_sources:
            for stem_legacy in _stem_legacy_asset_ids(source):
                if entry_id not in result.legacy_to_ids[stem_legacy]:
                    result.legacy_to_ids[stem_legacy].append(entry_id)

    # Altbestand: Accepted noch auf stock/downloads → auf clean umbiegen.
    try:
        from otio_app.services.without_voiceover_enhanced.local_media_service import (
            reconcile_accepted_supplement_paths,
        )

        reconcile_accepted_supplement_paths(project)
    except Exception:  # noqa: BLE001
        pass

    if folder_names is None:
        folders = _catalog_folder_names(project)
    else:
        folders = []
        seen_folders: set[str] = set()
        for raw in folder_names:
            text = (raw or "").strip()
            if text and text not in seen_folders:
                seen_folders.add(text)
                folders.append(text)

    for folder in folders:
        inventory = load_folder_inventory(project, folder)
        assets_iter: list[object] = (
            list(getattr(inventory, "assets", []) or []) if inventory is not None else []
        )
        registered_before = len(result.by_id)
        missing_inventory_paths = 0

        for asset in assets_iter:
            raw_path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if raw_path is None:
                continue
            existing = str(getattr(asset, "asset_id", "") or "").strip()
            inventory_path = _resolve_local_path(project, raw_path)
            if not inventory_path.is_file():
                missing_inventory_paths += 1
                recovered = _recover_inventory_media_path(
                    project,
                    folder,
                    raw_path=raw_path,
                    asset_id=existing,
                    candidates=_folder_candidates(folder),
                )
                if recovered is None:
                    continue
                inventory_path = recovered
            if is_http_url(str(inventory_path)):
                continue
            # Clean bevorzugen (TC 00:00:00:00), Original behalten wenn kein Clean.
            path = inventory_path
            try:
                from otio_app.services.clean_media import resolve_effective_media_path

                preferred = resolve_effective_media_path(
                    project, folder, inventory_path
                )
                if preferred.is_file():
                    path = preferred
            except Exception:  # noqa: BLE001
                path = inventory_path
            if not path.is_file():
                continue
            # Kanonisches Inventar oft ohne asset_id — Slim/LLM nutzt Stem-IDs.
            if not existing:
                existing = (
                    _preferred_stem_legacy_id(inventory_path)
                    or _preferred_stem_legacy_id(path)
                )
            canonical = canonicalize_inventory_asset_id(
                project,
                path=path,
                folder_name=folder,
                existing_id=existing,
            )
            if existing and not is_legacy_ambiguous_asset_id(existing):
                # Explizite ID: Kollision prüfen (gleiche ID, anderer Pfad).
                if existing in result.by_id and result.by_id[existing]["path"] != str(path):
                    existing_path = str(result.by_id[existing]["path"])
                    kinds = {
                        _catalog_path_kind(existing_path),
                        _catalog_path_kind(str(path)),
                    }
                    if kinds == {"stock_download", "clean"}:
                        if _catalog_path_kind(str(path)) != "clean":
                            continue
                        # Clean ersetzt stock — unten normal registrieren.
                    else:
                        explicit_paths[existing].extend(
                            [existing_path, str(path)]
                        )
                        continue
            duration = getattr(asset, "duration_sec", None)
            if duration is None:
                duration = getattr(asset, "duration_seconds", None)
            usable_in = getattr(asset, "usable_in_s", None)
            still_original = _catalog_still_image_for_paths(
                project, folder, inventory_path, path, raw_path
            )
            if still_original is not None:
                media_type = "photo"
            else:
                media_type = getattr(asset, "media_type", None) or (
                    "photo" if is_image_media(path) else "video"
                )
            entry = _probe_entry(
                project,
                path=path,
                folder=folder,
                asset_id=canonical,
                usable_in=float(usable_in) if usable_in is not None else None,
                media_type_hint=str(media_type or "video").lower(),
                fps=fps,
                known_duration=float(duration) if duration is not None else None,
                probe_cache=probe_cache,
                original_image_path=still_original,
            )
            if duration is not None and entry["duration_seconds"] is None:
                entry["duration_seconds"] = float(duration)
            # Nach Stem-Backfill ist ``canonical`` oft schon die Cut-Plan-Legacy-ID
            # (canonicalize behält nichtleere existing_id).
            register_id = (
                existing
                if existing and not is_legacy_ambiguous_asset_id(existing)
                else canonical
            )
            entry["canonical_id"] = register_id
            alias_paths = [inventory_path, path]
            _register(
                register_id,
                entry,
                raw_id=existing or canonical,
                alias_paths=alias_paths,
            )
            if register_id != canonical:
                _register(
                    canonical,
                    entry,
                    raw_id=existing or canonical,
                    alias_paths=alias_paths,
                )

        # Disk/Clean/Supplemental nur nachindexieren, wenn Inventar leer/lückenhaft
        # ist — sonst kostet jeder Timing-Lauf unnötig ffprobe über alle Clips.
        need_disk_fallback = (
            not assets_iter
            or missing_inventory_paths > 0
            or len(result.by_id) == registered_before
        )
        if need_disk_fallback:
            for media_path in _folder_candidates(folder):
                try:
                    if not media_path.is_file():
                        continue
                except OSError:
                    continue
                if is_http_url(str(media_path)):
                    continue
                if _stem_already_registered(media_path):
                    continue
                path = media_path
                try:
                    from otio_app.services.clean_media import resolve_effective_media_path

                    preferred = resolve_effective_media_path(
                        project, folder, media_path
                    )
                    if preferred.is_file():
                        path = preferred
                except Exception:  # noqa: BLE001
                    path = media_path
                if not path.is_file():
                    continue
                if _stem_already_registered(path):
                    continue
                stem_legacy = (
                    _preferred_stem_legacy_id(media_path)
                    or _preferred_stem_legacy_id(path)
                )
                canonical = canonicalize_inventory_asset_id(
                    project,
                    path=path,
                    folder_name=folder,
                    existing_id=stem_legacy,
                )
                if canonical in result.by_id:
                    continue
                register_id = stem_legacy or canonical
                still_original = _catalog_still_image_for_paths(
                    project, folder, media_path, path
                )
                entry = _probe_entry(
                    project,
                    path=path,
                    folder=folder,
                    asset_id=register_id,
                    usable_in=None,
                    media_type_hint=(
                        "photo"
                        if still_original is not None or is_image_media(path)
                        else "video"
                    ),
                    fps=fps,
                    probe_cache=probe_cache,
                    original_image_path=still_original,
                )
                entry["canonical_id"] = register_id
                _register(
                    register_id,
                    entry,
                    raw_id=register_id,
                    alias_paths=[media_path, path],
                )
                if register_id != canonical:
                    _register(
                        canonical,
                        entry,
                        raw_id=register_id,
                        alias_paths=[media_path, path],
                    )

        # Slim-IDs (LLM-Cut) immer als Alias auf indexierte Dateien legen.
        # Besonders relevant bei Ordner-/Dateinamen mit ``&``: Slim kollabiert
        # Sonderzeichen, ältere Stem-Aliasse erzeugten ``___``.
        # Funnel-Fotos: Inventar/Slim zeigen oft ``.jpg``, Clean liegt als ``.mp4``.
        slim_by_file = _slim_id_filename_map(project, folder)
        if slim_by_file:
            entries_by_name: dict[str, dict] = {}
            entries_by_stem: dict[str, dict] = {}
            for entry in result.by_id.values():
                if str(entry.get("folder") or "") != folder:
                    continue
                name = Path(str(entry.get("path") or "")).name.lower()
                if name and name not in entries_by_name:
                    entries_by_name[name] = entry
                stem_key = _catalog_media_stem_key(entry.get("path") or "")
                if stem_key and stem_key not in entries_by_stem:
                    entries_by_stem[stem_key] = entry
            for slim_id, file_name in slim_by_file.items():
                if not slim_id or slim_id in result.by_id:
                    continue
                if result.legacy_to_ids.get(slim_id):
                    continue
                entry = entries_by_name.get(file_name.lower())
                if entry is None:
                    entry = entries_by_stem.get(_catalog_media_stem_key(file_name))
                if entry is None:
                    continue
                _register(slim_id, entry, raw_id=slim_id)

    def _entry_for_path(path: Path) -> dict | None:
        try:
            want = str(path.resolve())
        except OSError:
            want = str(path)
        for entry in result.by_id.values():
            raw = str(entry.get("path") or "")
            if not raw:
                continue
            try:
                have = str(Path(raw).resolve())
            except OSError:
                have = raw
            if have == want:
                return entry
        return None

    def _register_supplement_candidate(supplement: object, *, folder_hint: str = "") -> None:
        candidate_id = str(getattr(supplement, "candidate_id", "") or "").strip()
        if not candidate_id or candidate_id in result.by_id:
            return
        local_path = str(getattr(supplement, "local_media_path", "") or "").strip()
        path: Path | None = None
        if local_path and not is_http_url(local_path):
            resolved = _resolve_local_path(project, local_path)
            if resolved.is_file():
                path = resolved
        if path is None or not path.is_file():
            recovered = find_local_media_for_candidate_id(
                project, candidate_id=candidate_id, folder_name=folder_hint
            )
            if recovered is not None and recovered.is_file():
                path = recovered
        if path is None or not path.is_file():
            return
        existing = _entry_for_path(path)
        if existing is not None:
            _register(candidate_id, existing, raw_id=candidate_id)
            return
        duration = getattr(supplement, "duration_seconds", None)
        media_type = str(getattr(supplement, "media_type", "") or "photo").lower()
        entry = _probe_entry(
            project,
            path=path,
            folder="",
            asset_id=candidate_id,
            usable_in=None,
            media_type_hint=media_type,
            fps=fps,
            known_duration=float(duration) if duration is not None else None,
            probe_cache=probe_cache,
        )
        entry["supplement"] = True
        entry["export_ready"] = True
        if duration is not None:
            entry["duration_seconds"] = duration
        _register(candidate_id, entry, raw_id=candidate_id)

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        for supplement in accepted.supplements:
            _register_supplement_candidate(supplement)

    for supplement in list_export_ready_supplements(project):
        _register_supplement_candidate(supplement)

    for asset_id, paths in sorted(explicit_paths.items()):
        unique_paths = sorted(set(paths))
        if len(unique_paths) < 2:
            continue
        listed = "; ".join(unique_paths)
        result.collisions.append(
            f"Asset-ID '{asset_id}' zeigt auf mehrere lokale Pfade: {listed}. "
            "Inventar sowie Lauf 2 und Lauf 3 neu erzeugen."
        )
        # Mehrdeutige ID aus Katalog entfernen — kein stilles first/last.
        result.by_id.pop(asset_id, None)
    return result


def _asset_catalog(project: Project) -> dict[str, dict]:
    """Kompatibilitäts-Wrapper (eindeutige IDs)."""
    return build_asset_catalog(project).by_id


def lookup_catalog_entry(
    catalog: AssetCatalog,
    asset_id: str,
) -> tuple[dict | None, str | None]:
    """Gibt (entry, error) zurück — nie stilles first/last bei Mehrdeutigkeit."""
    key = (asset_id or "").strip()
    if not key:
        return None, "Leere Asset-ID."
    if key in catalog.by_id:
        return catalog.by_id[key], None
    aliases = catalog.legacy_to_ids.get(key) or []
    if len(aliases) == 1:
        return catalog.by_id[aliases[0]], None
    if len(aliases) > 1:
        paths = [catalog.by_id[a]["path"] for a in aliases if a in catalog.by_id]
        return None, (
            f"Mehrdeutige Legacy-Asset-ID '{key}' trifft "
            f"{len(paths)} Dateien: {'; '.join(paths)}. "
            "Inventar sowie Lauf 2 und Lauf 3 neu erzeugen "
            "(eindeutige Ordner-Scoped-IDs erforderlich)."
        )
    return None, f"Unbekannte Asset-ID: {key}"


def _is_intro_folder(folder: str | None) -> bool:
    name = (folder or "").strip().lower()
    return name in {"intro", "introduction"} or name.startswith("intro_")


def _entry_audio_duration(entry: NarrationTimelineEntry) -> float:
    if entry.audio_duration_seconds is not None:
        return max(0.0, float(entry.audio_duration_seconds))
    span = max(0.0, float(entry.end_seconds) - float(entry.start_seconds))
    for pause in entry.intra_pauses:
        span = max(0.0, span - float(pause.pause_seconds))
    return span


def _anchor_to_seconds(
    timeline: NarrationTimelineDocument,
    anchor: NarrationAnchor,
    *,
    sentence_index: dict[str, SentenceTiming],
) -> float:
    entry_map = {entry.segment_id: entry for entry in timeline.entries}
    entry = entry_map.get(anchor.segment_id)
    if entry is None:
        raise TimelineResolveError(f"Unbekannte Segment-ID: {anchor.segment_id}")

    sentence_id = str(anchor.sentence_id or "").strip()
    if sentence_id:
        sentence = sentence_index.get(sentence_id)
        if sentence is None:
            raise TimelineResolveError(f"Unbekannte Sentence-ID: {sentence_id}")
        if sentence.segment_id != anchor.segment_id:
            raise TimelineResolveError(
                f"Sentence {sentence_id} gehört zu {sentence.segment_id}, "
                f"nicht zu {anchor.segment_id}."
            )
        span = max(0.0, float(sentence.end_seconds) - float(sentence.start_seconds))
        offset = max(0.0, min(float(anchor.offset_seconds), span))
        source = float(sentence.start_seconds) + offset
        return source_seconds_to_timeline(entry, source)

    audio_dur = _entry_audio_duration(entry)
    offset = max(0.0, min(float(anchor.offset_seconds), audio_dur))
    return source_seconds_to_timeline(entry, offset)


# E2E-3: kein Audio-Waisenclip; Split verwerfen wenn Rest zu kurz.
MIN_AUDIO_CLIP_SECONDS = 0.5


def _min_audio_clip_seconds(fps: float = 25.0) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    frame = 1.0 / rate
    return max(MIN_AUDIO_CLIP_SECONDS, 2.0 * frame)


def _normalize_audio_media_key(audio_path: str | None) -> str:
    """Stabiler Schlüssel für dieselbe Kapitel-WAV (Pfad-Varianten → eine Datei)."""
    raw = str(audio_path or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).replace("\\", "/").casefold()


def coalesce_same_media_audio_segments(
    segments: list[ResolvedAudioSegment],
    *,
    epsilon: float = 1e-3,
) -> list[ResolvedAudioSegment]:
    """Eine Timeline-Clip-Kette pro Kapitel-WAV (Intro + Körper).

    Keyword Flow / Chapter-TTS liefert **eine** Audiodatei pro Ordner; Script-
    Segmente und ggf. Intra-Splits erzeugen sonst mehrere OTIO-Clips derselben
    Datei (in Resolve oft alle als ``Intro.wav`` sichtbar). Abstehende Clips
    ohne eingefügte Pause und mit kontinuierlichem Source-Range werden zu
    einem Clip zusammengezogen — analog „eine Datei = ein Narrationsclip“.
    """
    if len(segments) <= 1:
        return list(segments)

    merged: list[ResolvedAudioSegment] = []
    for segment in segments:
        if not merged:
            merged.append(segment.model_copy(deep=True))
            continue
        prev = merged[-1]
        same_media = _normalize_audio_media_key(
            prev.audio_path
        ) == _normalize_audio_media_key(segment.audio_path)
        if not same_media or not prev.audio_path:
            merged.append(segment.model_copy(deep=True))
            continue

        prev_pause = max(0.0, float(prev.pause_after_seconds or 0.0))
        # Eingefügte Timeline-Stille zwischen Stücken → nicht verschlucken
        # (A/V-Sync). Keyword Flow setzt pause_after=0.
        if prev_pause > epsilon:
            merged.append(segment.model_copy(deep=True))
            continue

        expected_timeline = float(prev.timeline_end_seconds) + prev_pause
        timeline_ok = abs(float(segment.timeline_start_seconds) - expected_timeline) <= (
            epsilon
        )
        prev_source_end = float(
            prev.source_end_seconds
            if prev.source_end_seconds is not None
            else prev.timeline_end_seconds - prev.timeline_start_seconds
        )
        next_source_start = float(segment.source_start_seconds or 0.0)
        source_ok = abs(next_source_start - prev_source_end) <= epsilon
        if not (timeline_ok and source_ok):
            merged.append(segment.model_copy(deep=True))
            continue

        next_source_end = float(
            segment.source_end_seconds
            if segment.source_end_seconds is not None
            else next_source_start
            + max(
                0.0,
                float(segment.timeline_end_seconds) - float(segment.timeline_start_seconds),
            )
        )
        prev.timeline_end_seconds = round(float(segment.timeline_end_seconds), 6)
        prev.source_end_seconds = round(max(prev_source_end, next_source_end), 6)
        prev.pause_after_seconds = round(float(segment.pause_after_seconds or 0.0), 6)
        prev.split_label = ""
        if not prev.chapter_id and segment.chapter_id:
            prev.chapter_id = segment.chapter_id
    return merged


def _build_resolved_audio_segments(
    *,
    timeline: NarrationTimelineDocument,
    timing_map: dict,
    fps: float = 25.0,
) -> list[ResolvedAudioSegment]:
    """Segment-MP3s; Intra-Pausen → Silence-Mid-Split + Gap (kein Time-Stretch).

    E2E-3 Guard: Split verwerfen bzw. an vorige Grenze mergen, wenn der Rest
    nach dem Splitpunkt < 0.5s (oder < 2 Frames) wäre — kein 0.04s-Waisenclip.

    Anschließend: Clips derselben Kapitel-WAV ohne Pause-Gap zu einem Clip
    zusammenziehen (Intro.wav / Dublin.wav …).
    """
    min_clip = _min_audio_clip_seconds(fps)
    audio_segments: list[ResolvedAudioSegment] = []
    for entry in timeline.entries:
        timing = timing_map.get(entry.segment_id)
        if timing is None:
            continue
        audio_path = timing.audio_path
        audio_dur = _entry_audio_duration(entry)
        base_source = max(
            0.0, float(getattr(timing, "source_start_seconds", 0.0) or 0.0)
        )
        timing_end = float(getattr(timing, "source_end_seconds", 0.0) or 0.0)
        if timing_end > base_source + 1e-9:
            audio_dur = min(audio_dur, timing_end - base_source) if audio_dur > 0 else (
                timing_end - base_source
            )
        intra = sorted(entry.intra_pauses, key=lambda p: p.source_split_seconds)
        if not intra:
            audio_segments.append(
                ResolvedAudioSegment(
                    segment_id=entry.segment_id,
                    audio_path=audio_path,
                    timeline_start_seconds=entry.start_seconds,
                    timeline_end_seconds=entry.end_seconds,
                    pause_after_seconds=entry.pause_after_seconds,
                    source_start_seconds=round(base_source, 6),
                    source_end_seconds=round(base_source + audio_dur, 6),
                )
            )
            continue

        source_cursor = 0.0
        timeline_cursor = float(entry.start_seconds)
        folded_trailing_pause = 0.0
        for pause in intra:
            split = max(source_cursor, min(float(pause.source_split_seconds), audio_dur))
            piece_dur = max(0.0, split - source_cursor)
            remainder_after = max(0.0, audio_dur - split)
            # Zu kurzer Rest → Split verwerfen, Pause ans Segmentende falten.
            if remainder_after + 1e-9 < min_clip:
                folded_trailing_pause += float(pause.pause_seconds)
                continue
            # Zu kurzes Vorderstück → Split verwerfen (an vorige Grenze mergen).
            if piece_dur + 1e-9 < min_clip:
                folded_trailing_pause += float(pause.pause_seconds)
                continue
            audio_segments.append(
                ResolvedAudioSegment(
                    segment_id=entry.segment_id,
                    audio_path=audio_path,
                    timeline_start_seconds=round(timeline_cursor, 6),
                    timeline_end_seconds=round(timeline_cursor + piece_dur, 6),
                    pause_after_seconds=round(float(pause.pause_seconds), 6),
                    source_start_seconds=round(base_source + source_cursor, 6),
                    source_end_seconds=round(base_source + split, 6),
                    split_label=f"after:{pause.after_sentence_id}",
                )
            )
            timeline_cursor += piece_dur + float(pause.pause_seconds)
            source_cursor = split

        remainder = max(0.0, audio_dur - source_cursor)
        pause_after = float(entry.pause_after_seconds) + folded_trailing_pause
        if remainder + 1e-9 < min_clip and source_cursor > 1e-9:
            # Rest an letzte Piece-Grenze mergen statt Waisenclip.
            if audio_segments and audio_segments[-1].segment_id == entry.segment_id:
                prev = audio_segments[-1]
                prev.source_end_seconds = round(base_source + audio_dur, 6)
                prev.timeline_end_seconds = round(
                    prev.timeline_end_seconds + remainder, 6
                )
                prev.pause_after_seconds = round(
                    float(prev.pause_after_seconds) + pause_after, 6
                )
                if prev.split_label == "tail" or not prev.split_label:
                    prev.split_label = prev.split_label or "merged_tail"
            else:
                audio_segments.append(
                    ResolvedAudioSegment(
                        segment_id=entry.segment_id,
                        audio_path=audio_path,
                        timeline_start_seconds=round(timeline_cursor, 6),
                        timeline_end_seconds=round(
                            timeline_cursor + max(remainder, audio_dur), 6
                        ),
                        pause_after_seconds=round(pause_after, 6),
                        source_start_seconds=round(base_source, 6),
                        source_end_seconds=round(base_source + audio_dur, 6),
                    )
                )
            continue

        audio_segments.append(
            ResolvedAudioSegment(
                segment_id=entry.segment_id,
                audio_path=audio_path,
                timeline_start_seconds=round(timeline_cursor, 6),
                timeline_end_seconds=round(timeline_cursor + remainder, 6),
                pause_after_seconds=round(pause_after, 6),
                source_start_seconds=round(base_source + source_cursor, 6),
                source_end_seconds=round(base_source + audio_dur, 6),
                split_label="tail" if source_cursor > 1e-9 else "",
            )
        )
    return coalesce_same_media_audio_segments(audio_segments)


def _seconds_to_frame(seconds: float, fps: float) -> float:
    """Frame-Rundung: snappe auf Framegrenze (deterministisch)."""
    frame = round(seconds * fps)
    return round(frame / fps, 6)


def _seconds_floor_to_frame(seconds: float, fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return round(math.floor(float(seconds) * rate + 1e-9) / rate, 6)


def _seconds_ceil_to_frame(seconds: float, fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return round(math.ceil(float(seconds) * rate - 1e-9) / rate, 6)


def _frame_duration(fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return 1.0 / rate


def _chapters_from_locked(locked) -> list[tuple[str, list[str]]]:
    """Kapitel in Skriptreihenfolge: aufeinanderfolgende Segmente mit gleichem Ordner.

    Segmente ohne folder_name bilden ein gemeinsames Default-Kapitel — nicht
    je Segment ein Pseudo-Kapitel — damit kapitelübergreifende Ankerprüfung
    echte Ordnergrenzen meint.
    """
    chapters: list[tuple[str, list[str]]] = []
    for segment in sorted(locked.segments, key=lambda s: int(s.sequence_index)):
        folder = (segment.folder_name or "").strip()
        chapter_id = folder or "_default"
        if not chapters or chapters[-1][0] != chapter_id:
            chapters.append((chapter_id, [segment.segment_id]))
        else:
            chapters[-1][1].append(segment.segment_id)
    return chapters


def build_shot_continuity_table(
    shots: list[ResolvedShot],
    *,
    fps: float,
) -> list[dict]:
    """Diagnosetabelle für visuelle Gaps/Overlaps zwischen benachbarten Shots."""
    ordered = sorted(shots, key=_resolved_shot_sort_key)
    frame = _frame_duration(fps)
    rows: list[dict] = []
    for index, shot in enumerate(ordered):
        next_start = (
            ordered[index + 1].timeline_start_seconds
            if index + 1 < len(ordered)
            else None
        )
        gap = (
            None
            if next_start is None
            else round(next_start - shot.timeline_end_seconds, 6)
        )
        status = "ok"
        if gap is not None:
            if gap > frame + 1e-9:
                status = "gap_error"
            elif gap < -(frame + 1e-9):
                status = "overlap_error"
            elif abs(gap) > 1e-9:
                status = "frame_snap_candidate"
        rows.append(
            {
                "chapter_id": shot.chapter_id or shot.folder_name,
                "shot_id": shot.shot_id,
                "timeline_start": shot.timeline_start_seconds,
                "timeline_end": shot.timeline_end_seconds,
                "next_shot_start": next_start,
                "gap_or_overlap_seconds": gap,
                "asset_id": shot.asset_id,
                "resolved_media_path": shot.resolved_media_path,
                "source_start": shot.source_start_seconds,
                "source_end": shot.source_end_seconds,
                "repair_or_error": status,
            }
        )
    return rows


def _apply_visual_continuity_rules(
    ordered: list[ResolvedShot],
    *,
    project: Project,
    fps: float,
    repairs: list[str],
    errors: list[str],
) -> None:
    """Ein-Frame-Snap reparieren; größere Gaps/Overlaps blockieren."""
    if len(ordered) < 2:
        return
    frame = _frame_duration(fps)
    for prev, curr in zip(ordered, ordered[1:]):
        delta = curr.timeline_start_seconds - prev.timeline_end_seconds
        chapter = curr.chapter_id or prev.chapter_id or prev.folder_name or "?"
        if abs(delta) <= 1e-9:
            continue
        if 0.0 < delta <= frame + 1e-9:
            prev.timeline_end_seconds = round(curr.timeline_start_seconds, 6)
            repairs.append(
                f"Ein-Frame-Gap {delta:.6f}s zwischen {prev.shot_id} und "
                f"{curr.shot_id} (Kapitel {chapter}) — Snap auf Anschluss."
            )
            try:
                _reapply_hold_for_timeline_span(
                    project,
                    prev,
                    fps=fps,
                    repairs=repairs,
                    label="Ein-Frame-Gap",
                )
            except TimelineResolveError as exc:
                errors.append(str(exc))
            continue
        if -frame - 1e-9 <= delta < 0.0:
            curr.timeline_start_seconds = round(prev.timeline_end_seconds, 6)
            repairs.append(
                f"Ein-Frame-Overlap {delta:.6f}s zwischen {prev.shot_id} und "
                f"{curr.shot_id} (Kapitel {chapter}) — Snap auf Anschluss."
            )
            continue
        if delta > frame + 1e-9:
            errors.append(
                f"Visuelle Lücke in Kapitel {chapter}: "
                f"{prev.timeline_end_seconds:.3f}s–{curr.timeline_start_seconds:.3f}s "
                f"({delta:.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )
            continue
        if delta < -(frame + 1e-9):
            # may_overlap_pause darf keine Video-Clip-Überlappung erlauben.
            errors.append(
                f"Visuelle Überlappung in Kapitel {chapter}: "
                f"{curr.timeline_start_seconds:.3f}s–{prev.timeline_end_seconds:.3f}s "
                f"({abs(delta):.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )


def _segment_to_chapter_map(locked) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for chapter_id, segment_ids in _chapters_from_locked(locked):
        for segment_id in segment_ids:
            mapping[segment_id] = chapter_id
    return mapping


def _is_bridge_resolved_shot(shot: ResolvedShot) -> bool:
    return (
        str(shot.shot_id).startswith("bridge_")
        or str(shot.editorial_function or "").strip().lower() == "chapter_transition"
    )


def _canonical_plan_shot_id(shot_id: str) -> str:
    """Parent-Slot für synthetische Teile (z. B. ``slot_007__shortfall``).

    Shortfall-/Closing-Fallback-Teile müssen dieselbe Kapitel-/Segment-Zuordnung
    wie der Asset-Kopf erhalten — sonst verschiebt die Kapitelhülle nur den Kopf
    und erzeugt Überlappung + Lücke von Vorlauf-Länge.
    """
    text = str(shot_id or "").strip()
    for suffix in ("__shortfall", "__closing_fallback"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _resolved_shot_sort_key(shot: ResolvedShot) -> tuple:
    """Dichte Reihenfolge: Parent vor Shortfall/Fallback bei gleichem Start."""
    sid = str(shot.shot_id or "")
    if sid.endswith("__shortfall"):
        tail = 1
    elif sid.endswith("__closing_fallback"):
        tail = 2
    else:
        tail = 0
    parent = _canonical_plan_shot_id(sid)
    return (float(shot.timeline_start_seconds), parent, tail, sid)


def _closing_fallback_asset_for_chapter(
    chapter_id: str,
    *,
    closing_fallback_asset_id: str | None = None,
    closing_fallback_by_chapter: dict[str, str] | None = None,
) -> str:
    """Fallback-Closer für ein Kapitel (Map hat Vorrang vor Einzel-Feld)."""
    by_chapter = closing_fallback_by_chapter or {}
    mapped = str(by_chapter.get(chapter_id) or "").strip()
    if mapped:
        return mapped
    return str(closing_fallback_asset_id or "").strip()


def _make_independent_envelope_shot(
    project: Project,
    template: ResolvedShot,
    *,
    shot_id: str,
    timeline_start: float,
    timeline_end: float,
    editorial_function: str,
    fps: float,
    repairs: list[str],
    errors: list[str],
    label: str,
) -> ResolvedShot | None:
    """Eigener Vorlauf-/Nachlauf-Shot (nicht Verlängerung des VO-Content-Shots)."""
    need = max(0.0, float(timeline_end) - float(timeline_start))
    if need <= 1e-9:
        return None
    shot = ResolvedShot(
        shot_id=shot_id,
        asset_id=str(template.asset_id or ""),
        timeline_start_seconds=round(float(timeline_start), 6),
        timeline_end_seconds=round(float(timeline_end), 6),
        source_start_seconds=float(template.source_start_seconds or 0.0),
        source_end_seconds=float(template.source_end_seconds or 0.0),
        editorial_function=editorial_function,
        folder_name=str(template.folder_name or template.chapter_id or ""),
        chapter_id=str(template.chapter_id or template.folder_name or ""),
        resolved_media_path=template.resolved_media_path,
        resolved_media_kind=template.resolved_media_kind,
        resolved_available_start_seconds=template.resolved_available_start_seconds,
        resolved_media_duration_seconds=template.resolved_media_duration_seconds,
        hold_mode=template.hold_mode or "",
        asset_fit=template.asset_fit,
        asset_fit_reason=f"independent envelope ({editorial_function})",
        open_gap=False,
    )
    try:
        _reapply_hold_for_timeline_span(
            project,
            shot,
            fps=fps,
            repairs=repairs,
            label=label,
        )
    except TimelineResolveError as exc:
        errors.append(str(exc))
        return None
    return shot


def _make_last_frame_postroll_shot(
    project: Project,
    template: ResolvedShot,
    *,
    shot_id: str,
    timeline_start: float,
    timeline_end: float,
    fps: float,
    repairs: list[str],
    errors: list[str],
    label: str,
) -> ResolvedShot | None:
    """5s-Nachlauf als Freeze des letzten Bildes, wenn Motion-Video zu kurz ist."""
    need = max(0.0, float(timeline_end) - float(timeline_start))
    if need <= 1e-9:
        return None
    path = Path(str(template.resolved_media_path or "").strip())
    if not path.is_file():
        errors.append(
            f"{label}: letzter Frame unmöglich — Medienpfad fehlt "
            f"({template.resolved_media_path})."
        )
        return None
    try:
        hold = ensure_last_frame_hold(
            project, path, duration_seconds=need, fps=fps
        )
    except MediaHoldError as exc:
        errors.append(f"{label}: {exc}")
        return None
    shot = ResolvedShot(
        shot_id=shot_id,
        asset_id=str(template.asset_id or ""),
        timeline_start_seconds=round(float(timeline_start), 6),
        timeline_end_seconds=round(float(timeline_end), 6),
        source_start_seconds=0.0,
        source_end_seconds=round(need, 6),
        editorial_function="technical_chapter_postroll",
        folder_name=str(template.folder_name or template.chapter_id or ""),
        chapter_id=str(template.chapter_id or template.folder_name or ""),
        resolved_media_path=str(hold),
        resolved_media_kind="video",
        resolved_available_start_seconds=0.0,
        resolved_media_duration_seconds=round(need, 6),
        hold_mode="freeze_video",
        asset_fit=template.asset_fit or "acceptable",
        asset_fit_reason="chapter postroll (last frame hold)",
        open_gap=False,
    )
    repairs.append(
        f"{shot_id}: {label} — letzter Frame {need:.1f}s gehalten ({hold.name})."
    )
    return shot


def _make_envelope_shot_from_asset_id(
    project: Project,
    *,
    asset_id: str,
    catalog: AssetCatalog,
    shot_id: str,
    timeline_start: float,
    timeline_end: float,
    editorial_function: str,
    chapter_id: str,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    repairs: list[str],
    errors: list[str],
    label: str,
) -> ResolvedShot | None:
    """Vorlauf-/Nachlauf-Shot aus LLM-Asset-ID (Katalog), nie Content-Kopie."""
    need = max(0.0, float(timeline_end) - float(timeline_start))
    if need <= 1e-9:
        return None
    asset = str(asset_id or "").strip()
    if not asset:
        errors.append(f"{label}: Asset-ID fehlt.")
        return None
    entry, lookup_err = lookup_catalog_entry(catalog, asset)
    if entry is None:
        errors.append(
            f"{label}: Asset {asset!r} nicht auflösbar ({lookup_err})."
        )
        return None
    try:
        shot = _resolve_shot_media(
            project,
            shot_id=shot_id,
            asset_id=str(entry.get("canonical_id") or asset),
            entry=entry,
            timeline_start=float(timeline_start),
            timeline_end=float(timeline_end),
            fps=fps,
            head_trim=head_trim,
            short_tolerance=short_tolerance,
            editorial_function=editorial_function,
            may_overlap_pause=False,
            repairs=repairs,
        )
    except TimelineResolveError as exc:
        errors.append(str(exc))
        return None
    shot.folder_name = chapter_id
    shot.chapter_id = chapter_id
    shot.asset_fit = "strong"
    shot.asset_fit_reason = f"LLM envelope ({editorial_function})"
    shot.open_gap = False
    return shot


def _apply_chapter_envelopes(
    project: Project,
    *,
    locked,
    final: FinalCutPlanDocument,
    ordered: list[ResolvedShot],
    audio_segments: list[ResolvedAudioSegment],
    preroll: float,
    postroll: float,
    fps: float,
    repairs: list[str],
    errors: list[str],
    narration_timeline: NarrationTimelineDocument | None = None,
    include_chapter: Callable[[str], bool] | None = None,
    catalog: AssetCatalog | None = None,
    closing_fallback_asset_id: str | None = None,
    closing_fallback_by_chapter: dict[str, str] | None = None,
    head_trim: float = 0.0,
    short_tolerance: float = 0.0,
    enable_map_opener: bool = False,
    map_decisions: dict[str, Any] | None = None,
    independent_envelope_shots: bool | None = None,
    intro_opener_asset_id: str | None = None,
    intro_closing_asset_id: str | None = None,
) -> list[ResolvedChapterEnvelope]:
    """Kapitelhüllen: Vor-/Nachlauf vor/nach dem VO-Carpet.

    E2E-4:
    - ``chapter_video_end = chapter_audio_end + postroll`` am chapter_close
    - ``chapter_video_start(N+1) = chapter_video_end(N)`` (kein Bridge-Slot)
    - ``chapter_audio_end`` aus Audio-Segment-Ende (ceil), Start floor

    Intro (und optional ``independent_envelope_shots=True``): eigener Vorlauf-
    und Nachlauf-Shot — Content-Shots starten erst mit VO / enden mit VO.
    Intro-Assets kommen von ``intro_opener_asset_id`` /
    ``intro_closing_asset_id`` (LLM) — nie Kopie von First/Last-Content.
    Kapitel-Default: Vor-/Nachlauf verlängert Opening/Closing-Content-Shot.

    ``include_chapter``: optional nur bestimmte Kapitel (z. B. Intro-only Resolve).
    Bei abschließender Narrations-Lücke kann ``closing_fallback_asset_id`` /
    ``closing_fallback_by_chapter`` einen Reserve-Closer einfügen.
    """
    chapters = _chapters_from_locked(locked)
    if include_chapter is not None:
        chapters = [
            (chapter_id, segment_ids)
            for chapter_id, segment_ids in chapters
            if include_chapter(chapter_id)
        ]
    if not chapters:
        return []

    shot_start_segment = {
        shot.shot_id: next(
            (
                plan.narration_start_anchor.segment_id
                for plan in final.shots
                if plan.shot_id == _canonical_plan_shot_id(shot.shot_id)
            ),
            "",
        )
        for shot in ordered
    }
    raw_shot_times = {
        shot.shot_id: (shot.timeline_start_seconds, shot.timeline_end_seconds)
        for shot in ordered
    }
    timeline_by_seg = {
        e.segment_id: e for e in (narration_timeline.entries if narration_timeline else [])
    }
    min_clip = _min_audio_clip_seconds(fps)

    narration_expected = bool(locked.segments)
    cursor = 0.0
    envelopes: list[ResolvedChapterEnvelope] = []
    frame = _frame_duration(fps)

    for _chapter_index, (chapter_id, segment_ids) in enumerate(chapters):
        seg_set = set(segment_ids)
        ch_audios = [a for a in audio_segments if a.segment_id in seg_set]
        assigned = [
            shot
            for shot in ordered
            if shot_start_segment.get(shot.shot_id, "") in seg_set
        ]
        # E2E-4: Bridge-Slots werden nicht mehr erzeugt; Legacy herausfiltern.
        ch_shots = [s for s in assigned if not _is_bridge_resolved_shot(s)]

        if narration_expected and not ch_audios:
            errors.append(
                f"Kapitel {chapter_id}: Narration erwartet, aber keine "
                "Audio-Segmente vorhanden."
            )
            continue
        if not ch_shots:
            errors.append(
                f"Kapitel {chapter_id}: kein visueller Inhalts-Shot für die Kapitelhülle."
            )
            continue

        for audio in ch_audios:
            audio.chapter_id = chapter_id
        for shot in ch_shots:
            shot.chapter_id = chapter_id
            if not shot.folder_name:
                shot.folder_name = chapter_id

        # chapter_audio aus realen Clips / Segment-Ende (keine Stub-Clips < min).
        real_audios = [
            a
            for a in ch_audios
            if (a.timeline_end_seconds - a.timeline_start_seconds) + 1e-9 >= min_clip
        ]
        if not real_audios:
            real_audios = list(ch_audios)

        # chapter_audio aus Narration-Segmentenden (defensiv gegen Stub-Clips).
        last_seg = segment_ids[-1] if segment_ids else ""
        first_seg = segment_ids[0] if segment_ids else ""
        entry = timeline_by_seg.get(last_seg)
        first_entry = timeline_by_seg.get(first_seg)
        if first_entry is not None and entry is not None:
            # E2E-4: Audio-Segment-Ränder floor/ceil — nicht Satzende.
            raw_audio_start = _seconds_floor_to_frame(float(first_entry.start_seconds), fps)
            raw_audio_end = _seconds_ceil_to_frame(float(entry.end_seconds), fps)
        elif real_audios:
            raw_audio_start = _seconds_floor_to_frame(
                min(a.timeline_start_seconds for a in real_audios), fps
            )
            raw_audio_end = _seconds_ceil_to_frame(
                max(a.timeline_end_seconds for a in real_audios), fps
            )
        else:
            raw_audio_start = min(s.timeline_start_seconds for s in ch_shots)
            raw_audio_end = max(s.timeline_end_seconds for s in ch_shots)

        raw_first_shot_start = min(raw_shot_times[s.shot_id][0] for s in ch_shots)
        raw_last_shot_end = max(raw_shot_times[s.shot_id][1] for s in ch_shots)

        # E2E-4: Ausklang / Nachlauf-Toleranz — kein Hard-Blocker innerhalb Band.
        # Überlänge bis postroll (+ Slack) = Closing-Hold, auf Audio-Ende klemmen;
        # Envelope hängt den Nachlauf danach an.
        ausklang_tol = max(frame * 3.0, 0.25)
        # Slack ~1.5s: Frame-Rundung / Satz→Audio-Drift / leichte Editorial-Überhänge.
        overhang_tol = max(0.0, float(postroll)) + max(ausklang_tol, 1.5)
        lead_tol = max(0.0, float(preroll)) + max(ausklang_tol, 1.5)

        if raw_last_shot_end < raw_audio_end - ausklang_tol - 1e-9:
            gap_msg = (
                f"Abschließende visuelle Lücke während der Narration in Kapitel "
                f"{chapter_id}: letzter Shot endet bei {raw_last_shot_end:.3f}s, "
                f"Audio bei {raw_audio_end:.3f}s "
                f"(>{ausklang_tol:.3f}s Ausklang-Toleranz)."
            )
            fallback_id = _closing_fallback_asset_for_chapter(
                chapter_id,
                closing_fallback_asset_id=closing_fallback_asset_id,
                closing_fallback_by_chapter=closing_fallback_by_chapter,
            )
            last_raw = max(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][1], s.shot_id),
            )
            inserted = False
            if fallback_id and catalog is not None:
                if fallback_id == str(last_raw.asset_id or "").strip():
                    errors.append(
                        f"{gap_msg} closing_fallback_asset_id={fallback_id!r} "
                        "ist identisch mit dem letzten Shot — Reserve unbrauchbar."
                    )
                else:
                    fallback_entry, lookup_err = lookup_catalog_entry(
                        catalog, fallback_id
                    )
                    if fallback_entry is None:
                        errors.append(
                            f"{gap_msg} closing_fallback_asset_id={fallback_id!r} "
                            f"nicht auflösbar ({lookup_err})."
                        )
                    else:
                        parent_id = _canonical_plan_shot_id(last_raw.shot_id)
                        fallback_shot_id = f"{parent_id}__closing_fallback"
                        try:
                            fallback_shot = _resolve_shot_media(
                                project,
                                shot_id=fallback_shot_id,
                                asset_id=str(
                                    fallback_entry.get("canonical_id")
                                    or fallback_id
                                ),
                                entry=fallback_entry,
                                timeline_start=raw_last_shot_end,
                                timeline_end=raw_audio_end,
                                fps=fps,
                                head_trim=head_trim,
                                short_tolerance=short_tolerance,
                                editorial_function="chapter_close_fallback",
                                may_overlap_pause=False,
                                repairs=repairs,
                            )
                        except TimelineResolveError as exc:
                            errors.append(
                                f"{gap_msg} Closing-Fallback {fallback_id!r} "
                                f"fehlgeschlagen: {exc}"
                            )
                        else:
                            fallback_shot.folder_name = chapter_id
                            fallback_shot.chapter_id = chapter_id
                            fallback_shot.asset_fit = "acceptable"
                            fallback_shot.asset_fit_reason = (
                                "closing_fallback_asset_id — Reserve für "
                                "abschließende Narrations-Lücke"
                            )
                            ordered.append(fallback_shot)
                            ch_shots.append(fallback_shot)
                            raw_shot_times[fallback_shot.shot_id] = (
                                raw_last_shot_end,
                                raw_audio_end,
                            )
                            shot_start_segment[fallback_shot.shot_id] = (
                                shot_start_segment.get(last_raw.shot_id, "")
                            )
                            raw_last_shot_end = raw_audio_end
                            inserted = True
                            repairs.append(
                                f"Kapitel {chapter_id}: Closing-Fallback "
                                f"{fallback_id} als {fallback_shot_id} "
                                f"({raw_shot_times[fallback_shot.shot_id][0]:.3f}s"
                                f"–{raw_audio_end:.3f}s) eingefügt."
                            )
            if not inserted:
                if not fallback_id:
                    errors.append(
                        f"{gap_msg} Kein closing_fallback_asset_id gesetzt "
                        "(LLM Cut erneut ausführen)."
                    )
                elif catalog is None:
                    errors.append(
                        f"{gap_msg} Asset-Katalog fehlt für Closing-Fallback "
                        f"{fallback_id!r}."
                    )
        elif raw_last_shot_end + 1e-9 < raw_audio_end:
            last_raw = max(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][1], s.shot_id),
            )
            old_start, old_end = raw_shot_times[last_raw.shot_id]
            raw_shot_times[last_raw.shot_id] = (old_start, raw_audio_end)
            repairs.append(
                f"Kapitel {chapter_id}: Closing-Shot {last_raw.shot_id} "
                f"Ende {old_end:.6f}s → Audio-Ende {raw_audio_end:.6f}s."
            )
            raw_last_shot_end = raw_audio_end
        elif raw_last_shot_end > raw_audio_end + overhang_tol + 1e-9:
            errors.append(
                f"Abschließende visuelle Überlänge in Kapitel {chapter_id}: "
                f"letzter Shot endet bei {raw_last_shot_end:.3f}s, "
                f"Audio bei {raw_audio_end:.3f}s "
                f"(>{overhang_tol:.3f}s Nachlauf-Toleranz)."
            )
        elif raw_last_shot_end > raw_audio_end + 1e-9:
            last_raw = max(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][1], s.shot_id),
            )
            old_start, old_end = raw_shot_times[last_raw.shot_id]
            raw_shot_times[last_raw.shot_id] = (old_start, raw_audio_end)
            repairs.append(
                f"Kapitel {chapter_id}: Closing-Überlänge innerhalb Nachlauf "
                f"({old_end - raw_audio_end:.3f}s ≤ {overhang_tol:.3f}s) — "
                f"{last_raw.shot_id} Ende {old_end:.6f}s → Audio-Ende "
                f"{raw_audio_end:.6f}s."
            )
            raw_last_shot_end = raw_audio_end

        if raw_first_shot_start > raw_audio_start + lead_tol + 1e-9:
            errors.append(
                f"Führende visuelle Lücke in Kapitel {chapter_id}: "
                f"erster Shot startet bei {raw_first_shot_start:.3f}s, "
                f"Audio bei {raw_audio_start:.3f}s "
                f"(>{lead_tol:.3f}s Vorlauf-Toleranz)."
            )
        elif raw_first_shot_start > raw_audio_start + 1e-9:
            first_raw = min(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][0], s.shot_id),
            )
            _old_start, old_end = raw_shot_times[first_raw.shot_id]
            raw_shot_times[first_raw.shot_id] = (raw_audio_start, old_end)
            repairs.append(
                f"Kapitel {chapter_id}: führende Lücke innerhalb Vorlauf — "
                f"{first_raw.shot_id} Start {raw_first_shot_start:.6f}s → "
                f"{raw_audio_start:.6f}s."
            )
            raw_first_shot_start = raw_audio_start

        raw_span = max(0.0, raw_audio_end - raw_audio_start)

        chapter_preroll = float(preroll)
        chapter_postroll = float(postroll)
        map_shot: ResolvedShot | None = None
        if enable_map_opener and not _is_intro_folder(chapter_id):
            from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
                decide_map_opener,
            )

            decision = decide_map_opener(project, chapter_id)
            if map_decisions is not None:
                map_decisions[chapter_id] = {
                    "status": decision.status,
                    "warning": decision.warning,
                    "asset_id": decision.asset_id,
                    "media_path": decision.media_path,
                    "source_duration_seconds": decision.source_duration_seconds,
                    "opener_seconds": decision.opener_seconds,
                }
            # ``missing`` ist der Normalfall ohne Karten — nicht jeder Rhythmus-
            # Kapitel-Repair-Log mit „keine Map gefunden“ füllen.
            if decision.warning and decision.status not in {"missing", "skipped_intro"}:
                repairs.append(decision.warning)
            if decision.status == "used" and decision.media_path:
                chapter_preroll = float(decision.opener_seconds)
                map_shot = ResolvedShot(
                    shot_id=f"{safe_folder_slug(chapter_id)}_map_opener",
                    asset_id=str(decision.asset_id or "map_opener"),
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=float(decision.opener_seconds),
                    source_start_seconds=0.0,
                    source_end_seconds=float(decision.opener_seconds),
                    editorial_function="technical_chapter_map_opener",
                    folder_name=chapter_id,
                    chapter_id=chapter_id,
                    resolved_media_path=str(decision.media_path),
                    resolved_media_kind="video",
                    resolved_available_start_seconds=0.0,
                    resolved_media_duration_seconds=float(
                        decision.source_duration_seconds or decision.opener_seconds
                    ),
                    asset_fit="strong",
                    asset_fit_reason="map opener (audio ignored)",
                )
                repairs.append(
                    f"Kapitel {chapter_id}: Map-Opener {decision.opener_seconds:.1f}s "
                    f"vor VO ({decision.asset_id})."
                )

        chapter_video_start = _seconds_to_frame(cursor, fps)
        chapter_audio_start = _seconds_to_frame(
            chapter_video_start + chapter_preroll, fps
        )
        chapter_audio_end = _seconds_to_frame(chapter_audio_start + raw_span, fps)
        chapter_video_end = _seconds_to_frame(
            chapter_audio_end + chapter_postroll, fps
        )

        for audio in ch_audios:
            delta_start = audio.timeline_start_seconds - raw_audio_start
            delta_end = audio.timeline_end_seconds - raw_audio_start
            audio.timeline_start_seconds = round(chapter_audio_start + delta_start, 6)
            audio.timeline_end_seconds = round(chapter_audio_start + delta_end, 6)

        for shot in ch_shots:
            old_start, old_end = raw_shot_times[shot.shot_id]
            shot.timeline_start_seconds = round(
                chapter_audio_start + (old_start - raw_audio_start), 6
            )
            shot.timeline_end_seconds = round(
                chapter_audio_start + (old_end - raw_audio_start), 6
            )
            if shot.timeline_end_seconds < shot.timeline_start_seconds:
                shot.timeline_end_seconds = shot.timeline_start_seconds

        if map_shot is not None:
            map_shot.timeline_start_seconds = round(chapter_video_start, 6)
            map_shot.timeline_end_seconds = round(chapter_audio_start, 6)
            map_shot.chapter_id = chapter_id
            map_shot.folder_name = chapter_id
            ordered.append(map_shot)
            ch_shots.append(map_shot)
            raw_shot_times[map_shot.shot_id] = (
                raw_audio_start - float(chapter_preroll),
                raw_audio_start,
            )

        content_shots = [
            s
            for s in ch_shots
            if str(s.editorial_function or "")
            not in {
                "technical_chapter_map_opener",
                "technical_chapter_preroll",
                "technical_chapter_postroll",
            }
        ]
        if not content_shots:
            content_shots = list(ch_shots)
        content_first = min(
            content_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id)
        )
        content_last = max(
            content_shots, key=lambda s: (s.timeline_end_seconds, s.shot_id)
        )

        # Intro: immer eigene Vorlauf-/Nachlauf-Shots. Optional global erzwingen.
        use_independent_envelopes = (
            True
            if independent_envelope_shots is True
            else (
                False
                if independent_envelope_shots is False
                else _is_intro_folder(chapter_id)
            )
        )

        preroll_hold_id = ""
        postroll_hold_id = ""
        slug = safe_folder_slug(chapter_id)

        content_asset_ids = {
            str(s.asset_id or "").strip()
            for s in content_shots
            if str(s.asset_id or "").strip()
        }
        opener_asset = str(intro_opener_asset_id or "").strip()
        closing_hold_asset = str(intro_closing_asset_id or "").strip()
        is_intro_chapter = _is_intro_folder(chapter_id)

        # Map-Opener ersetzt den Settings-Vorlauf — kein zweiter Preroll-Shot.
        if map_shot is not None and chapter_preroll > 1e-9:
            preroll_hold_id = map_shot.shot_id
        elif use_independent_envelopes and chapter_preroll > 1e-9:
            preroll_shot = None
            if is_intro_chapter:
                # Intro: LLM-Opener — niemals First-Content-Asset klonen.
                if not opener_asset:
                    errors.append(
                        f"Kapitel {chapter_id}: intro_opener_asset_id fehlt — "
                        "Vorlauf darf kein Content-Asset kopieren."
                    )
                elif opener_asset in content_asset_ids:
                    errors.append(
                        f"Kapitel {chapter_id}: intro_opener_asset_id="
                        f"{opener_asset!r} kollidiert mit einem VO-Content-Asset."
                    )
                elif catalog is None:
                    errors.append(
                        f"Kapitel {chapter_id}: Asset-Katalog fehlt für "
                        f"intro_opener_asset_id={opener_asset!r}."
                    )
                else:
                    preroll_shot = _make_envelope_shot_from_asset_id(
                        project,
                        asset_id=opener_asset,
                        catalog=catalog,
                        shot_id=f"{slug}_preroll",
                        timeline_start=chapter_video_start,
                        timeline_end=chapter_audio_start,
                        editorial_function="technical_chapter_preroll",
                        chapter_id=chapter_id,
                        fps=fps,
                        head_trim=head_trim,
                        short_tolerance=short_tolerance,
                        repairs=repairs,
                        errors=errors,
                        label=f"Kapitel-{chapter_id}-Vorlauf-Shot",
                    )
            elif content_first.resolved_media_path:
                preroll_shot = _make_independent_envelope_shot(
                    project,
                    content_first,
                    shot_id=f"{slug}_preroll",
                    timeline_start=chapter_video_start,
                    timeline_end=chapter_audio_start,
                    editorial_function="technical_chapter_preroll",
                    fps=fps,
                    repairs=repairs,
                    errors=errors,
                    label=f"Kapitel-{chapter_id}-Vorlauf-Shot",
                )
            if preroll_shot is not None:
                ordered.append(preroll_shot)
                ch_shots.append(preroll_shot)
                preroll_hold_id = preroll_shot.shot_id
                repairs.append(
                    f"Kapitel {chapter_id}: unabhängiger Vorlauf-Shot "
                    f"{chapter_preroll:.1f}s vor VO ({preroll_shot.asset_id})."
                )
        elif (
            chapter_preroll > 1e-9
            and content_first.timeline_start_seconds > chapter_video_start + 1e-9
        ):
            # Legacy: Content-Opening in den Vorlauf verlängern.
            content_first.timeline_start_seconds = round(chapter_video_start, 6)
            preroll_hold_id = content_first.shot_id
            try:
                _reapply_hold_for_timeline_span(
                    project,
                    content_first,
                    fps=fps,
                    repairs=repairs,
                    label=f"Kapitel-{chapter_id}-Vorlauf",
                )
            except TimelineResolveError as exc:
                errors.append(str(exc))
        elif chapter_preroll > 1e-9:
            preroll_hold_id = content_first.shot_id

        if use_independent_envelopes and chapter_postroll > 1e-9:
            # Content endet mit VO; eigener Nachlauf-Shot danach.
            if content_last.timeline_end_seconds > chapter_audio_end + 1e-9:
                content_last.timeline_end_seconds = round(chapter_audio_end, 6)
            postroll_shot = None
            if is_intro_chapter:
                if not closing_hold_asset:
                    errors.append(
                        f"Kapitel {chapter_id}: intro_closing_asset_id fehlt — "
                        "Nachlauf darf kein Content-Asset kopieren."
                    )
                elif closing_hold_asset in content_asset_ids:
                    errors.append(
                        f"Kapitel {chapter_id}: intro_closing_asset_id="
                        f"{closing_hold_asset!r} kollidiert mit einem "
                        "VO-Content-Asset."
                    )
                elif (
                    opener_asset
                    and closing_hold_asset
                    and opener_asset == closing_hold_asset
                ):
                    errors.append(
                        f"Kapitel {chapter_id}: intro_opener_asset_id und "
                        "intro_closing_asset_id müssen verschieden sein."
                    )
                elif catalog is None:
                    errors.append(
                        f"Kapitel {chapter_id}: Asset-Katalog fehlt für "
                        f"intro_closing_asset_id={closing_hold_asset!r}."
                    )
                else:
                    postroll_shot = _make_envelope_shot_from_asset_id(
                        project,
                        asset_id=closing_hold_asset,
                        catalog=catalog,
                        shot_id=f"{slug}_postroll",
                        timeline_start=chapter_audio_end,
                        timeline_end=chapter_video_end,
                        editorial_function="technical_chapter_postroll",
                        chapter_id=chapter_id,
                        fps=fps,
                        head_trim=head_trim,
                        short_tolerance=short_tolerance,
                        repairs=repairs,
                        errors=errors,
                        label=f"Kapitel-{chapter_id}-Nachlauf-Shot",
                    )
            elif content_last.resolved_media_path:
                postroll_shot = _make_independent_envelope_shot(
                    project,
                    content_last,
                    shot_id=f"{slug}_postroll",
                    timeline_start=chapter_audio_end,
                    timeline_end=chapter_video_end,
                    editorial_function="technical_chapter_postroll",
                    fps=fps,
                    repairs=repairs,
                    errors=errors,
                    label=f"Kapitel-{chapter_id}-Nachlauf-Shot",
                )
            if postroll_shot is not None:
                ordered.append(postroll_shot)
                ch_shots.append(postroll_shot)
                postroll_hold_id = postroll_shot.shot_id
                repairs.append(
                    f"Kapitel {chapter_id}: unabhängiger Nachlauf-Shot "
                    f"{chapter_postroll:.1f}s nach VO ({postroll_shot.asset_id})."
                )
        elif (
            chapter_postroll > 1e-9
            and content_last.timeline_end_seconds < chapter_video_end - 1e-9
        ):
            previous_end = float(content_last.timeline_end_seconds)
            content_last.timeline_end_seconds = round(chapter_video_end, 6)
            postroll_hold_id = content_last.shot_id
            try:
                _reapply_hold_for_timeline_span(
                    project,
                    content_last,
                    fps=fps,
                    repairs=repairs,
                    label=f"Kapitel-{chapter_id}-Nachlauf",
                )
            except TimelineResolveError:
                # Motion-Video darf nicht per tpad verlängert werden. Der
                # Nachlauf muss trotzdem auf der Videospur liegen — sonst
                # startet das nächste VO 5s nach der Map.
                content_last.timeline_end_seconds = round(
                    max(previous_end, chapter_audio_end), 6
                )
                postroll_shot = _make_last_frame_postroll_shot(
                    project,
                    content_last,
                    shot_id=f"{slug}_postroll",
                    timeline_start=chapter_audio_end,
                    timeline_end=chapter_video_end,
                    fps=fps,
                    repairs=repairs,
                    errors=errors,
                    label=f"Kapitel-{chapter_id}-Nachlauf",
                )
                if postroll_shot is not None:
                    if content_last.timeline_end_seconds > chapter_audio_end + 1e-9:
                        content_last.timeline_end_seconds = round(
                            chapter_audio_end, 6
                        )
                    ordered.append(postroll_shot)
                    ch_shots.append(postroll_shot)
                    postroll_hold_id = postroll_shot.shot_id
                else:
                    # Freeze ging nicht: Slot trotzdem bis Video-Ende belegen,
                    # damit der OTIO-Export die 5s als Gap legt statt zu
                    # verschieben.
                    content_last.timeline_end_seconds = round(
                        chapter_video_end, 6
                    )
                    postroll_hold_id = content_last.shot_id
        elif chapter_postroll > 1e-9:
            postroll_hold_id = content_last.shot_id

        first = min(ch_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))
        last = max(ch_shots, key=lambda s: (s.timeline_end_seconds, s.shot_id))

        if _is_bridge_resolved_shot(last) or str(last.shot_id).startswith("bridge_"):
            errors.append(
                f"Kapitel {chapter_id}: last_shot_id darf keine Bridge sein "
                f"({last.shot_id})."
            )

        envelopes.append(
            ResolvedChapterEnvelope(
                chapter_id=chapter_id,
                folder_name=chapter_id,
                chapter_video_start=round(chapter_video_start, 6),
                chapter_audio_start=round(chapter_audio_start, 6),
                chapter_audio_end=round(chapter_audio_end, 6),
                chapter_video_end=round(chapter_video_end, 6),
                preroll_seconds=round(chapter_preroll, 6),
                postroll_seconds=round(chapter_postroll, 6),
                first_shot_id=first.shot_id,
                last_shot_id=last.shot_id,
                preroll_hold_shot_id=preroll_hold_id,
                postroll_hold_shot_id=postroll_hold_id,
                segment_ids=list(segment_ids),
            )
        )

        # E2E-4: nächstes Kapitel beginnt exakt am Video-Ende (Nachlauf→Vorlauf).
        cursor = chapter_video_end

    # Envelope-Validierung: preroll/postroll je Kapitel == Settings (Intro ausgenommen).
    # Keyword-Flow Map-Opener ersetzt den Settings-Vorlauf (typisch 9.0s).
    map_opener_chapters = {
        str(shot.chapter_id or shot.folder_name or "")
        for shot in ordered
        if str(shot.editorial_function or "") == "technical_chapter_map_opener"
    }
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        KEYWORD_FLOW_MAP_OPENER_SEC,
    )

    for env in envelopes:
        if _is_intro_folder(env.chapter_id):
            continue
        expected_preroll = (
            float(KEYWORD_FLOW_MAP_OPENER_SEC)
            if env.chapter_id in map_opener_chapters
            else float(preroll)
        )
        if abs(env.preroll_seconds - expected_preroll) > 1e-3:
            errors.append(
                f"Kapitel {env.chapter_id}: preroll {env.preroll_seconds:.2f}s "
                f"≠ erwartet {expected_preroll:.2f}s."
            )
        if abs(env.postroll_seconds - float(postroll)) > 1e-3:
            errors.append(
                f"Kapitel {env.chapter_id}: postroll {env.postroll_seconds:.2f}s "
                f"≠ Settings {postroll:.2f}s."
            )
        if str(env.last_shot_id).startswith("bridge_"):
            errors.append(
                f"Kapitel {env.chapter_id}: last_shot_id ist Bridge "
                f"({env.last_shot_id})."
            )
        if env.postroll_hold_shot_id and str(env.postroll_hold_shot_id).startswith(
            "bridge_"
        ):
            errors.append(
                f"Kapitel {env.chapter_id}: postroll_hold_shot_id ist Bridge "
                f"({env.postroll_hold_shot_id})."
            )

    for index in range(1, len(envelopes)):
        prev = envelopes[index - 1]
        cur = envelopes[index]
        if abs(cur.chapter_video_start - prev.chapter_video_end) > 1e-3:
            errors.append(
                f"Kapitelwechsel {prev.chapter_id}→{cur.chapter_id}: "
                f"video_start {cur.chapter_video_start:.3f}s ≠ "
                f"prev video_end {prev.chapter_video_end:.3f}s."
            )

    if envelopes:
        repairs.append(
            f"Kapitelhüllen: {len(envelopes)} Kapitel mit Vorlauf {preroll:.2f}s "
            f"und Nachlauf {postroll:.2f}s pro Kapitel "
            f"(Intro: eigene Vor-/Nachlauf-Shots; Kapitel: Content-Hold; "
            f"kein Bridge-Slot)."
        )
    return envelopes


def _count_chapter_continuity(
    envelopes: list[ResolvedChapterEnvelope],
    ordered: list[ResolvedShot],
    *,
    fps: float,
) -> None:
    frame = _frame_duration(fps)
    by_chapter: dict[str, list[ResolvedShot]] = defaultdict(list)
    for shot in ordered:
        key = shot.chapter_id or shot.folder_name or ""
        by_chapter[key].append(shot)
    for envelope in envelopes:
        shots = sorted(
            by_chapter.get(envelope.chapter_id, []),
            key=_resolved_shot_sort_key,
        )
        gaps = 0
        overlaps = 0
        for prev, curr in zip(shots, shots[1:]):
            delta = curr.timeline_start_seconds - prev.timeline_end_seconds
            if delta > frame + 1e-9:
                gaps += 1
            elif delta < -(frame + 1e-9):
                overlaps += 1
        envelope.visual_gap_count = gaps
        envelope.visual_overlap_count = overlaps


def _reapply_hold_for_timeline_span(
    project: Project,
    shot: ResolvedShot,
    *,
    fps: float,
    repairs: list[str],
    label: str,
) -> None:
    """Passt Source an Timeline-Span an.

    Stills dürfen zu Resolve-tauglichem Hold-Video werden.
    Motion-Video wird nie per tpad/Freeze verlängert — fail-closed.
    """
    need = max(0.0, shot.timeline_end_seconds - shot.timeline_start_seconds)
    source_span = max(0.0, shot.source_end_seconds - shot.source_start_seconds)
    if need <= source_span + 1e-6:
        return
    path = Path(shot.resolved_media_path or "")
    if not path.is_file():
        raise TimelineResolveError(
            f"{shot.shot_id}: {label} unmöglich — Medienpfad fehlt "
            f"({shot.resolved_media_path})."
        )
    available_start = float(shot.resolved_available_start_seconds or 0.0)
    media_dur = shot.resolved_media_duration_seconds
    if media_dur is not None:
        available_end = available_start + float(media_dur)
        # Zuerst: Source nach vorne schieben, wenn Datei lang genug.
        if float(media_dur) + 1e-6 >= need:
            shot.source_start_seconds = round(available_start, 6)
            shot.source_end_seconds = round(available_start + need, 6)
            if shot.source_end_seconds <= available_end + 1e-6:
                repairs.append(
                    f"{shot.shot_id}: {label} — Source auf Dateianfang geschoben."
                )
                return

    # Still → Hold-Video (Foto braucht Dauer in Resolve).
    if is_image_media(path):
        try:
            hold = ensure_still_hold_video(
                project, path, duration_seconds=need, fps=fps
            )
        except MediaHoldError as exc:
            raise TimelineResolveError(
                f"{shot.shot_id}: {label}-Still-Hold fehlgeschlagen: {exc}"
            ) from exc
        shot.resolved_media_path = str(hold)
        shot.resolved_media_kind = "video"
        shot.resolved_available_start_seconds = 0.0
        shot.resolved_media_duration_seconds = need
        shot.source_start_seconds = 0.0
        shot.source_end_seconds = round(need, 6)
        shot.hold_mode = "freeze_video"
        repairs.append(
            f"{shot.shot_id}: {label}-Still-Hold-Video {need:.2f}s ({hold.name})."
        )
        return

    # Bereits ein Still-Hold darf verlängert werden (weiterhin Foto-Freeze).
    if str(shot.hold_mode or "") == "freeze_video":
        try:
            hold = ensure_video_padded_hold(
                project, path, target_duration_seconds=need, fps=fps
            )
        except MediaHoldError as exc:
            raise TimelineResolveError(
                f"{shot.shot_id}: {label}-Still-Hold-Verlängerung fehlgeschlagen: {exc}"
            ) from exc
        shot.resolved_media_path = str(hold)
        shot.resolved_media_kind = "video"
        shot.resolved_available_start_seconds = 0.0
        shot.resolved_media_duration_seconds = need
        shot.source_start_seconds = 0.0
        shot.source_end_seconds = round(need, 6)
        repairs.append(
            f"{shot.shot_id}: {label}-Still-Hold verlängert auf {need:.2f}s "
            f"({hold.name})."
        )
        return

    usable = float(media_dur) if media_dur is not None else source_span
    raise TimelineResolveError(
        f"{shot.shot_id}: {label} — Motion-Video zu kurz für Timeline-Span "
        f"({usable:.2f}s nutzbar < {need:.2f}s nötig). "
        "Kein Video-Hold/tpad: kürzeren Shot planen, längeres Asset wählen "
        "oder coverage_gap setzen."
    )


def detect_one_to_one_sentence_asset(final: FinalCutPlanDocument, segment_count: int) -> bool:
    """True wenn Shotanzahl == Segmentanzahl und jeder Shot genau ein Segment spannt."""
    if len(final.shots) != segment_count or segment_count == 0:
        return False
    return all(
        shot.narration_start_anchor.segment_id == shot.narration_end_anchor.segment_id
        and shot.narration_start_anchor.offset_seconds == 0.0
        for shot in final.shots
    )


def _resolve_shot_media(
    project: Project,
    *,
    shot_id: str,
    asset_id: str,
    entry: dict,
    timeline_start: float,
    timeline_end: float,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    editorial_function: str,
    may_overlap_pause: bool,
    repairs: list[str],
) -> ResolvedShot:
    """Berechnet Source-Ranges inkl. Embedded-TC und Hold-Medien."""
    duration = max(0.0, timeline_end - timeline_start)
    media_path = Path(str(entry["path"]))
    available_start = float(entry.get("available_start_seconds") or 0.0)
    media_duration = entry.get("duration_seconds")
    media_kind = str(entry.get("media_kind") or "").lower()
    if not media_kind:
        media_kind = "image" if is_image_media(media_path) else "video"
    hold_mode = ""
    resolved_path = media_path

    # Echte Fotos halten die Slot-Dauer — auch wenn Clean daraus ein 5s-MP4
    # gemacht hat. Motion-MP4s (Győr: Dauer 0, fälschlich image getaggt) dürfen
    # nicht über ffmpeg ``-loop 1``.
    still_source = still_image_path_from_catalog_entry(entry, media_path)
    treat_as_still = still_source is not None
    if treat_as_still:
        # Stills: Hold-Video über die volle Timeline-Dauer (Resolve-sicher).
        try:
            hold_path = ensure_still_hold_video(
                project,
                still_source,
                duration_seconds=max(duration, TECH_MIN_SHOT_SECONDS),
                fps=fps,
            )
        except MediaHoldError as exc:
            raise TimelineResolveError(f"{shot_id}: {exc}") from exc
        resolved_path = hold_path
        hold_mode = "freeze_video"
        available_start = 0.0
        media_duration = max(duration, TECH_MIN_SHOT_SECONDS)
        media_kind = "video"
        source_start = 0.0
        source_end = duration
        repairs.append(
            f"{shot_id}: Still → Hold-Video {duration:.2f}s ({hold_path.name})."
        )
    else:
        if media_duration is None or float(media_duration or 0) <= 0:
            probed = probe_duration_seconds(media_path)
            if probed is None or probed <= 0:
                raise TimelineResolveError(
                    f"{shot_id}: Video ohne nutzbare Dauer ({media_path.name}). "
                    "ffprobe lieferte keine Sekunden — Still-Hold gilt nur für Fotos, "
                    f"nicht für {media_path.suffix or 'Video'}."
                )
            media_duration = probed
            repairs.append(
                f"{shot_id}: Mediendauer fehlte im Inventar — "
                f"ffprobe {probed:.2f}s ({media_path.name})."
            )
        media_kind = "video"
        media_duration_f = float(media_duration)
        usable_in = entry.get("usable_in_s")
        trim = head_trim
        if usable_in is not None:
            trim = max(trim, max(0.0, float(usable_in)))
        if trim >= media_duration_f:
            raise TimelineResolveError(
                f"{shot_id}: Asset {asset_id}: Head-Trim/usable_in ({trim}s) "
                f">= Mediendauer ({media_duration_f}s) · Pfad {media_path}."
            )
        usable = media_duration_f - trim
        need = duration
        # Fix 1: timeline_end darf hier NIE gekürzt werden.
        # Innerhalb-Toleranz → Grenzen-Klemme (unified resolve_timed_slots).
        # Über Toleranz → harter Fehler (is_short / Gap-Pfad).
        if need > usable + 1e-6:
            shortfall = need - usable
            if shortfall <= short_tolerance + 1e-6:
                raise TimelineResolveError(
                    f"{shot_id}: Asset {asset_id} knapp über usable "
                    f"(nutzbar {usable:.2f}s < nötig {need:.2f}s; shortfall "
                    f"{shortfall:.2f}s ≤ Toleranz {short_tolerance:.1f}s) — "
                    "muss in der Grenzen-Klemme gekürzt werden, nicht in der "
                    f"Media-Auflösung. Pfad {media_path}."
                )
            raise TimelineResolveError(
                f"{shot_id}: Asset {asset_id} zu kurz "
                f"(nutzbar {usable:.2f}s < nötig {need:.2f}s; Toleranz "
                f"{short_tolerance:.1f}s). Kein Video-Hold: kürzeren Shot "
                f"planen, anderes Asset wählen oder coverage_gap. "
                f"Pfad {media_path}."
            )

        boundary_span = max(0.0, float(timeline_end) - float(timeline_start))
        if abs(boundary_span - need) > 1e-6:
            raise TimelineResolveError(
                f"{shot_id}: Interner Fehler — resolved duration {need:.6f}s "
                f"≠ boundary span {boundary_span:.6f}s."
            )

        # Mitte der nutzbaren Zone; Source im Embedded-TC-Raum.
        content_start = trim + max(0.0, (usable - need) / 2.0)
        source_start = available_start + content_start
        source_end = source_start + need
        available_end = available_start + media_duration_f
        if source_end > available_end + 1e-6:
            # Nach links schieben, sofern möglich.
            shift = source_end - available_end
            source_start = max(available_start + trim, source_start - shift)
            source_end = source_start + need
        if source_start < available_start - 1e-6 or source_end > available_end + 1e-6:
            raise TimelineResolveError(
                f"{shot_id}: Source-Range außerhalb der verfügbaren Range für "
                f"{asset_id} (source {source_start:.3f}–{source_end:.3f}, "
                f"available {available_start:.3f}–{available_end:.3f}) · "
                f"Pfad {media_path}."
            )

    return ResolvedShot(
        shot_id=shot_id,
        asset_id=asset_id,
        timeline_start_seconds=timeline_start,
        timeline_end_seconds=timeline_end,
        source_start_seconds=round(source_start, 6),
        source_end_seconds=round(source_end, 6),
        editorial_function=editorial_function,
        may_overlap_pause=may_overlap_pause,
        resolved_media_path=str(resolved_path),
        resolved_media_kind=media_kind,
        resolved_media_duration_seconds=(
            round(float(media_duration), 6) if media_duration is not None else None
        ),
        resolved_available_start_seconds=round(available_start, 6),
        folder_name=str(entry.get("folder") or ""),
        hold_mode=hold_mode,
    )


def resolve_final_timeline(project: Project) -> ResolvedTimelineDocument:
    locked = require_locked_script(project)
    final = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    timings = load_segment_timings(project)
    if final is None:
        raise TimelineResolveError("Finaler Cut Plan fehlt.")
    if timeline is None:
        raise TimelineResolveError("Narrationstimeline fehlt.")
    if timings is None:
        raise TimelineResolveError("Segment-Timings fehlen.")

    errors: list[str] = []
    repairs: list[str] = []
    fps = float(project.fps)
    catalog = build_asset_catalog(project, fps=fps)
    errors.extend(catalog.collisions)
    options = load_cut_plan_options(project)
    editorial_min = max(TECH_MIN_SHOT_SECONDS, float(options.shot_min_sec))
    editorial_max = min(
        TECH_MAX_SHOT_SECONDS,
        max(editorial_min, float(options.shot_max_sec)),
    )
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    known_segments = {s.segment_id for s in locked.segments}
    preroll = resolve_timing_seconds(
        mode=options.voiceover_preroll_mode,
        setting_max=options.voiceover_preroll_sec,
        llm_value=final.voiceover_preroll_sec,
    )
    postroll = resolve_timing_seconds(
        mode=options.voiceover_postroll_mode,
        setting_max=options.voiceover_postroll_sec,
        llm_value=final.voiceover_postroll_sec,
    )

    # One-sentence-one-asset is allowed when editorial; no hard reject.
    # Kept as an optional note for debugging / transparency only.
    if detect_one_to_one_sentence_asset(final, len(locked.segments)):
        repairs.append(
            "Hinweis: Shotstruktur ist durchgängig 1 Segment → 1 Shot "
            "(erlaubt, aber oft weniger abwechslungsreich)."
        )

    timing_map = {
        item.segment_id: item
        for item in timings.segments
        if item.segment_id in known_segments
    }
    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    audio_segments = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map=timing_map,
        fps=fps,
    )
    segment_to_chapter = _segment_to_chapter_map(locked)

    resolved_shots: list[ResolvedShot] = []
    for shot in final.shots:
        if shot.narration_start_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_start_anchor.segment_id}")
            continue
        if shot.narration_end_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_end_anchor.segment_id}")
            continue
        start_seg = shot.narration_start_anchor.segment_id
        end_seg = shot.narration_end_anchor.segment_id
        start_chapter = segment_to_chapter.get(start_seg, "")
        end_chapter = segment_to_chapter.get(end_seg, "")
        if start_chapter and end_chapter and start_chapter != end_chapter:
            errors.append(
                f"{shot.shot_id}: Start- und Endanker in unterschiedlichen Kapiteln "
                f"(Startanker {start_seg} → Kapitel {start_chapter}, "
                f"Endanker {end_seg} → Kapitel {end_chapter})."
            )
            continue
        entry, lookup_error = lookup_catalog_entry(catalog, shot.asset_id)
        if entry is None:
            accepted = load_model(
                accepted_supplements_path(project), AcceptedSupplementsDocument
            )
            if accepted is not None and any(
                s.candidate_id == shot.asset_id for s in accepted.supplements
            ):
                errors.append(
                    f"{shot.shot_id}: Supplement {shot.asset_id} ist nicht "
                    "export_ready (lokale Mediendatei fehlt oder ist ungültig)."
                )
            else:
                errors.append(f"{shot.shot_id}: {lookup_error}")
            continue
        media_path = Path(str(entry.get("path") or ""))
        if is_http_url(str(media_path)):
            errors.append(
                f"{shot.shot_id}: Asset {shot.asset_id} besitzt eine Web-URL "
                f"statt lokaler Datei ({media_path})."
            )
            continue
        if not media_path.is_file():
            errors.append(
                f"{shot.shot_id}: lokale Datei fehlt für {shot.asset_id}: {media_path}"
            )
            continue

        try:
            start = _anchor_to_seconds(
                timeline,
                shot.narration_start_anchor,
                sentence_index=sentence_index,
            )
            end = _anchor_to_seconds(
                timeline,
                shot.narration_end_anchor,
                sentence_index=sentence_index,
            )
        except TimelineResolveError as exc:
            errors.append(f"{shot.shot_id}: {exc}")
            continue
        start = _seconds_to_frame(start, fps)
        end = _seconds_to_frame(end, fps)
        if end <= start:
            end = _seconds_to_frame(start + editorial_min, fps)
            repairs.append(
                f"{shot.shot_id}: Ende vor/gleich Start — auf Mindestlänge "
                f"{editorial_min}s gesetzt."
            )
        duration = end - start
        if duration < editorial_min:
            end = _seconds_to_frame(start + editorial_min, fps)
            repairs.append(
                f"{shot.shot_id}: unter shot_min ({editorial_min}s) — verlängert."
            )
            duration = end - start
        if duration > editorial_max:
            end = _seconds_to_frame(start + editorial_max, fps)
            repairs.append(
                f"{shot.shot_id}: über shot_max ({editorial_max}s) — gekürzt."
            )
            duration = end - start

        try:
            resolved_shot = _resolve_shot_media(
                project,
                shot_id=shot.shot_id,
                asset_id=str(entry.get("canonical_id") or shot.asset_id),
                entry=entry,
                timeline_start=start,
                timeline_end=end,
                fps=fps,
                head_trim=head_trim,
                short_tolerance=short_tolerance,
                editorial_function=shot.editorial_function,
                may_overlap_pause=shot.may_overlap_pause,
                repairs=repairs,
            )
        except TimelineResolveError as exc:
            errors.append(str(exc))
            continue
        resolved_shots.append(resolved_shot)

    ordered = sorted(resolved_shots, key=_resolved_shot_sort_key)

    # Vor-/Nachlauf pro Kapitel (Hülle), nicht einmal global.
    chapter_envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=ordered,
        audio_segments=audio_segments,
        preroll=preroll,
        postroll=postroll,
        fps=fps,
        repairs=repairs,
        errors=errors,
        narration_timeline=timeline,
    )
    ordered = sorted(ordered, key=_resolved_shot_sort_key)
    _apply_visual_continuity_rules(
        ordered,
        project=project,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    ordered = sorted(ordered, key=_resolved_shot_sort_key)
    _count_chapter_continuity(chapter_envelopes, ordered, fps=fps)

    def _is_technical_hold(shot: ResolvedShot) -> bool:
        return str(shot.editorial_function or "").startswith("technical_chapter_")

    editorial_shots = [shot for shot in ordered if not _is_technical_hold(shot)]

    # Benachbarte redaktionelle Shots dürfen nicht dasselbe Asset teilen
    # (Opening≠nächster, Closing≠vorheriger, inkl. Kapitelgrenzen).
    for prev, curr in zip(editorial_shots, editorial_shots[1:]):
        if not prev.asset_id or prev.asset_id != curr.asset_id:
            continue
        prev_folder = str(
            prev.folder_name
            or (catalog.by_id.get(prev.asset_id) or {}).get("folder")
            or ""
        )
        if _is_intro_folder(prev_folder):
            continue
        errors.append(
            f"Benachbarte Shots nutzen dasselbe Asset {prev.asset_id}: "
            f"{prev.shot_id} → {curr.shot_id} "
            f"(Opening/Closing und Reuse-Abstand: kein Direkt-Reuse)."
        )

    # Max asset usage (Intro und technische Kapitel-Holds zählen nicht).
    usage_counts = Counter(shot.asset_id for shot in editorial_shots)
    for asset_id, count in sorted(usage_counts.items()):
        folder = str((catalog.by_id.get(asset_id) or {}).get("folder") or "")
        if _is_intro_folder(folder):
            continue
        if count > int(options.max_asset_usage):
            errors.append(
                f"Asset {asset_id} wird {count}× genutzt "
                f"(max_asset_usage={options.max_asset_usage}; Intro zählt nicht)."
            )

    # Wiederverwendungsabstand: Direkt-Reuse (0) ist fail-closed; sonst soft Repair.
    reuse_distance = int(options.min_asset_reuse_distance_shots)
    if reuse_distance > 0:
        last_index: dict[str, int] = {}
        for index, shot in enumerate(editorial_shots):
            folder = str(
                shot.folder_name
                or (catalog.by_id.get(shot.asset_id) or {}).get("folder")
                or ""
            )
            if _is_intro_folder(folder):
                continue
            prev_index = last_index.get(shot.asset_id)
            if prev_index is not None:
                gap_shots = index - prev_index - 1
                if gap_shots < reuse_distance:
                    message = (
                        f"{shot.shot_id}: Asset {shot.asset_id} erneut nach "
                        f"{gap_shots} Shots (min Abstand {reuse_distance})."
                    )
                    if gap_shots == 0:
                        errors.append(message)
                    else:
                        repairs.append(message)
            last_index[shot.asset_id] = index

    repairs.extend(assess_cut_rhythm(final, ordered))

    chapter_count = max(1, len(chapter_envelopes))
    total = timeline.total_duration_seconds + (preroll + postroll) * chapter_count
    if ordered:
        total = max(total, ordered[-1].timeline_end_seconds)
    if audio_segments:
        total = max(
            total,
            max(a.timeline_end_seconds + a.pause_after_seconds for a in audio_segments),
        )
    if chapter_envelopes:
        total = max(total, chapter_envelopes[-1].chapter_video_end)

    document = ResolvedTimelineDocument(
        script_version=locked.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=ordered,
        chapters=chapter_envelopes,
        voiceover_preroll_sec=round(preroll, 6),
        voiceover_postroll_sec=round(postroll, 6),
        repairs=repairs,
        errors=errors,
    )
    write_json(resolved_timeline_path(project), document)
    write_json(repair_log_path(project), {"repairs": repairs, "errors": errors})
    if errors:
        raise TimelineResolveError("; ".join(errors))
    return document
