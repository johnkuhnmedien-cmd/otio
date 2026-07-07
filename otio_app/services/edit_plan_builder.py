"""Schnittplan aus Voice-over, Zuordnung und Inventar erzeugen."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from enum import Enum
from pathlib import Path

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanRulesDocument,
    EditPlanSettings,
    EditPlanShot,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
)
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_MISSING,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_edit_plan_path,
    get_folder_edit_plan_path,
)
from otio_app.services.edit_plan_rules import (
    apply_edit_plan_rules,
    export_rule_options,
    gemini_prompt_text,
    load_edit_plan_rules,
)
from otio_app.services.asset_usage import (
    filter_assets_by_usage,
    max_asset_usage_limit,
)
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    get_default_gemini_model,
    plan_passage_assets,
)
from otio_app.services.inventory_hash import compute_folder_inventory_hash
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.supplement_coverage import (
    COVERAGE_LOCAL_GOOD,
    COVERAGE_LOCAL_WEAK,
    COVERAGE_SUPPLEMENT_REQUIRED,
    coverage_to_supplement_request,
    evaluate_segment_coverage,
    score_asset_match,
)
from otio_app.services.supplement_requests import upsert_requests
from otio_app.defaults import DEFAULT_COVERAGE_THRESHOLD
from otio_app.services.shot_timing import (
    TimedPart,
    allocate_time_by_text,
    shots_from_timed_parts,
)
from otio_app.services.timeline_plan_builder import (
    assign_global_timeline_positions,
    build_timeline_items_for_folder,
    shots_from_timeline_items,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


class EditPlanLocationState(str, Enum):
    OPEN = "open"
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class EditPlanLocationStatus:
    folder_name: str
    state: EditPlanLocationState
    shot_count: int = 0


def resolve_edit_plan_location_state(
    folder_name: str,
    saved: EditPlanDocument | None,
    draft: EditPlanDocument | None = None,
) -> EditPlanLocationStatus:
    """Ermittelt den Status eines Ortes aus gespeicherter Datei und optionalem Entwurf."""
    effective = draft or saved
    if effective is None or not effective.shots:
        return EditPlanLocationStatus(folder_name=folder_name, state=EditPlanLocationState.OPEN)
    if effective.confirmed:
        return EditPlanLocationStatus(
            folder_name=folder_name,
            state=EditPlanLocationState.CONFIRMED,
            shot_count=len(effective.shots),
        )
    return EditPlanLocationStatus(
        folder_name=folder_name,
        state=EditPlanLocationState.DRAFT,
        shot_count=len(effective.shots),
    )


def get_mapped_folders(project: Project) -> list[str]:
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None:
        return []
    return sorted(
        {
            entry.folder
            for entry in mapping.entries
            if entry.folder and entry.confirmed
        }
    )


def load_voice_analysis(project: Project) -> VoiceAnalysisDocument:
    path = project.voice_analysis_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Voice-over-Analyse fehlt: {path}. Bitte zuerst unter „① Analysen“ ausführen."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return VoiceAnalysisDocument.model_validate(payload)


def local_split_passage(text: str, splitters: list[str]) -> list[str]:
    """Einfache Text-Trennung als Fallback ohne Gemini."""
    remaining = text.strip()
    if not remaining:
        return []
    for splitter in splitters:
        if splitter in remaining:
            pieces = [piece.strip() for piece in remaining.split(splitter) if piece.strip()]
            if len(pieces) > 1:
                return pieces
    return [remaining]


def _validate_asset_path(asset_path: str | None, allowed_paths: set[str]) -> str | None:
    if not asset_path:
        return None
    if asset_path in allowed_paths:
        return asset_path
    return None


def _best_matching_asset(
    passage_text: str,
    assets: list[dict[str, str]],
) -> dict[str, str] | None:
    """Wählt das inhaltlich am besten passende Asset für einen Textabschnitt.

    Vorher griff der lokale Fallback (ohne Gemini bzw. bei Gemini-Netzwerkfehlern)
    immer blind auf `assets[0]` zu — unabhängig vom Inhalt. Das führte dazu, dass
    frisch supplementierte Assets (die inhaltlich oft am besten zu genau der
    Passage passen, für die sie angefordert wurden) für die eigentliche Narration
    NIE gewählt wurden, sondern ungenutzt blieben und anschließend vom generischen
    Outro-/Filler-Auswahlmechanismus „eingesammelt“ wurden — was wie eine feste
    Bindung an eine andere Stelle (Ausklingen) wirkte.
    """
    if not assets:
        return None
    scored = [
        (
            score_asset_match(
                passage_text=passage_text,
                visual_requirement=passage_text,
                description=asset.get("description") or Path(asset["path"]).stem,
            ),
            index,
        )
        for index, asset in enumerate(assets)
    ]
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    best_index = scored[0][1]
    return assets[best_index]


def _parts_from_gemini_or_local(
    passage_text: str,
    folder_name: str,
    assets: list[dict[str, str]],
    language: str,
    settings: EditPlanSettings,
    *,
    use_api: bool,
    gemini_model: str | None,
    gemini_prompt: str = "",
) -> list[dict]:
    if use_api:
        try:
            parts = plan_passage_assets(
                passage_text,
                folder_name,
                assets,
                language,
                model=gemini_model or settings.gemini_model,
                extra_instructions=gemini_prompt,
            )
            if parts:
                return parts
        except GeminiNotConfiguredError:
            raise
        except Exception:
            # Netzwerk-/DNS-Probleme der Gemini-API dürfen den Schnittplan-Workflow
            # nicht abbrechen. In diesem Fall planen wir lokal weiter.
            pass

    texts = local_split_passage(passage_text, settings.text_splitters)
    return [
        {
            "text": piece,
            "motif": piece[:80],
            "asset_path": (_best_matching_asset(piece, assets) or {}).get("path"),
            "confidence": "low",
        }
        for piece in texts
    ]


def build_edit_plan(
    project: Project,
    settings: EditPlanSettings | None = None,
    *,
    use_api: bool = True,
    folder_names: list[str] | None = None,
    rules_doc: EditPlanRulesDocument | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> EditPlanDocument:
    """Erzeugt einen Schnittplan-Vorschlag für bestätigte Voice-over-Zuordnungen."""
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        raise ValueError(
            "Voice-over-Zuordnung fehlt oder ist nicht bestätigt. "
            "Bitte zuerst unter „② Zuordnung“ speichern."
        )

    voice_doc = load_voice_analysis(project)
    plan_settings = settings or EditPlanSettings(
        shot_min_sec=DEFAULT_SHOT_MIN_SEC,
        shot_max_sec=DEFAULT_SHOT_MAX_SEC,
        audio_offset_sec=DEFAULT_AUDIO_OFFSET_SEC,
        fallback_order=list(DEFAULT_FALLBACK_ORDER),
        gemini_model=get_default_gemini_model(),
    )
    rules_doc = rules_doc or load_edit_plan_rules(project)
    gemini_prompt = gemini_prompt_text(rules_doc)
    trim_leading_sec = export_rule_options(rules_doc).trim_leading_sec
    export_opts = export_rule_options(rules_doc)
    plan_settings = plan_settings.model_copy(
        update={
            "video_head_trim_sec": trim_leading_sec,
            "video_head_trim_policy": "fixed_trim" if trim_leading_sec > 0 else "disabled",
            "voiceover_trim_policy": "disabled",
            "voiceover_trim_start_sec": 0.0,
            "voiceover_trim_end_sec": 0.0,
        }
    )

    voice_files = {entry.path: entry for entry in voice_doc.files}
    mapping_by_voice = {
        entry.voice_file: entry.folder
        for entry in mapping.entries
        if entry.folder and entry.confirmed
    }

    if folder_names is not None:
        allowed = set(folder_names)
        mapping_by_voice = {
            voice: folder
            for voice, folder in mapping_by_voice.items()
            if folder in allowed
        }

    primary_folder: str | None = None
    if folder_names is not None and len(folder_names) == 1:
        primary_folder = folder_names[0]

    shots: list[EditPlanShot] = []
    assets_by_folder: dict[str, list[str]] = {}
    assets_payload_by_folder: dict[str, list[dict[str, str]]] = {}
    segment_coverages: list = []
    supplement_request_ids: list[str] = []
    pending_supplement_requests: list = []
    inventory_hashes: dict[str, str] = {}
    max_count = max_asset_usage_limit(rules_doc)
    usage_by_folder: dict[str, dict[str, int]] = {}

    for voice_path, folder_name in mapping_by_voice.items():
        voice_entry = voice_files.get(voice_path)
        if voice_entry is None:
            continue

        folder_inventory = load_folder_inventory(project, folder_name)
        inventory_hashes[folder_name] = compute_folder_inventory_hash(folder_inventory)
        asset_payload = [
            {
                "path": asset.path,
                "description": asset.description,
                "asset_id": asset.asset_id or asset_id_for_path(asset.path),
                "asset_origin": asset.asset_origin or "local_original",
                "rights_status": asset.rights_status,
                "provider": asset.provider,
                "media_type": asset.media_type,
                "source_url": asset.source_url,
                "supplement_request_id": asset.supplement_request_id,
            }
            for asset in folder_inventory.assets
            if asset.description or asset.path
        ]
        allowed_paths = {asset["path"] for asset in asset_payload}
        assets_by_folder[folder_name] = [asset["path"] for asset in asset_payload]
        assets_payload_by_folder[folder_name] = list(asset_payload)
        usage_by_folder[folder_name] = {}

        total_segments = sum(1 for segment in voice_entry.segments if segment.text.strip())
        beat_index = 0
        for segment in voice_entry.segments:
            if not segment.text.strip():
                continue
            beat_index += 1
            beat_id = f"beat_{beat_index:03d}"
            if progress_callback is not None:
                # Wird VOR dem (potenziell langsamen, z.B. Gemini 3.1 Pro
                # Preview) API-Call pro Segment aufgerufen — damit die UI bei
                # vielen Segmenten sichtbaren Fortschritt zeigen kann, statt
                # minutenlang ohne Rückmeldung zu blockieren.
                progress_callback(folder_name, beat_index, total_segments)
            coverage = evaluate_segment_coverage(
                beat_id=beat_id,
                segment=segment,
                folder_name=folder_name,
                voice_file=voice_path,
                assets=folder_inventory.assets,
            )
            supplement_request = coverage_to_supplement_request(coverage)
            if supplement_request is not None:
                coverage = coverage.model_copy(
                    update={"supplement_request_id": supplement_request.supplement_request_id}
                )
                pending_supplement_requests.append(supplement_request)
                supplement_request_ids.append(supplement_request.supplement_request_id)
            segment_coverages.append(coverage)

            folder_usage = usage_by_folder[folder_name]
            allowed_assets = filter_assets_by_usage(
                asset_payload,
                usage=folder_usage,
                max_count=max_count,
            )
            raw_parts = _parts_from_gemini_or_local(
                segment.text,
                folder_name,
                allowed_assets,
                voice_doc.language,
                plan_settings,
                use_api=use_api,
                gemini_model=plan_settings.gemini_model,
                gemini_prompt=gemini_prompt,
            )
            texts = [str(part.get("text", "")).strip() for part in raw_parts]
            time_ranges = allocate_time_by_text(
                segment.start_sec,
                segment.end_sec,
                texts,
            )
            timed_parts: list[TimedPart] = []
            for part, (start_sec, end_sec) in zip(raw_parts, time_ranges):
                asset_path = _validate_asset_path(
                    part.get("asset_path"),
                    allowed_paths,
                )
                asset_meta = next(
                    (asset for asset in asset_payload if asset["path"] == asset_path),
                    None,
                )
                if asset_path and coverage.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED:
                    description = asset_meta.get("description", "") if asset_meta else ""
                    match_score = score_asset_match(
                        passage_text=segment.text,
                        visual_requirement=coverage.visual_requirement,
                        description=description,
                        must_show=coverage.must_show,
                    )
                    if match_score < DEFAULT_COVERAGE_THRESHOLD:
                        asset_path = None
                        asset_meta = None
                asset_id = ""
                asset_origin = ""
                supplement_request_id = ""
                rights_status = ""
                source_url = ""
                provider = ""
                asset_source = FALLBACK_SOURCE_MISSING
                if asset_meta is not None:
                    asset_id = str(asset_meta.get("asset_id", ""))
                    asset_origin = str(asset_meta.get("asset_origin", "local_original"))
                    supplement_request_id = str(asset_meta.get("supplement_request_id", ""))
                    rights_status = str(asset_meta.get("rights_status", ""))
                    source_url = str(asset_meta.get("source_url", ""))
                    provider = str(asset_meta.get("provider", ""))
                    media_type = str(asset_meta.get("media_type", ""))
                    asset_source = asset_origin if asset_origin else FALLBACK_SOURCE_LOCAL
                elif asset_path:
                    asset_id = asset_id_for_path(asset_path)
                    asset_source = FALLBACK_SOURCE_LOCAL
                timed_parts.append(
                    TimedPart(
                        text=str(part.get("text", "")).strip(),
                        motif=str(part.get("motif", "")).strip(),
                        start_sec=start_sec,
                        end_sec=end_sec,
                        asset_path=asset_path,
                        confidence=str(part.get("confidence")) if part.get("confidence") else None,
                    )
                )

            normalized = shots_from_timed_parts(
                timed_parts,
                min_sec=plan_settings.shot_min_sec,
                max_sec=plan_settings.shot_max_sec,
            )
            for part in normalized:
                source = FALLBACK_SOURCE_LOCAL if part.asset_path else FALLBACK_SOURCE_MISSING
                meta = next(
                    (asset for asset in asset_payload if asset["path"] == part.asset_path),
                    None,
                )
                asset_id = ""
                asset_origin = ""
                supplement_request_id = ""
                rights_status = ""
                source_url = ""
                provider = ""
                media_type = ""
                if meta is not None:
                    asset_id = str(meta.get("asset_id", ""))
                    asset_origin = str(meta.get("asset_origin", ""))
                    supplement_request_id = str(meta.get("supplement_request_id", ""))
                    rights_status = str(meta.get("rights_status", ""))
                    source_url = str(meta.get("source_url", ""))
                    provider = str(meta.get("provider", ""))
                    media_type = str(meta.get("media_type", ""))
                elif part.asset_path:
                    asset_id = asset_id_for_path(part.asset_path)

                if (
                    asset_id
                    and max_count is not None
                    and usage_by_folder[folder_name].get(asset_id, 0) >= max_count
                ):
                    part = dataclass_replace(part, asset_path=None)
                    source = FALLBACK_SOURCE_MISSING
                    meta = None
                    asset_id = ""
                    asset_origin = ""
                    supplement_request_id = ""
                    rights_status = ""
                    source_url = ""
                    provider = ""
                    media_type = ""

                if asset_id:
                    usage_by_folder[folder_name][asset_id] = (
                        usage_by_folder[folder_name].get(asset_id, 0) + 1
                    )

                shots.append(
                    EditPlanShot(
                        voice_file=voice_path,
                        folder=folder_name,
                        voice_start_sec=part.start_sec,
                        voice_end_sec=part.end_sec,
                        duration_sec=max(0.0, part.end_sec - part.start_sec),
                        asset_path=part.asset_path,
                        asset_source=meta.get("asset_origin", source) if meta else source,
                        asset_id=asset_id,
                        asset_origin=asset_origin,
                        supplement_request_id=supplement_request_id,
                        rights_status=rights_status,
                        source_url=source_url,
                        provider=provider,
                        media_type=media_type,
                        motif=part.motif,
                        passage_text=part.text,
                        confidence=part.confidence,
                        beat_id=beat_id,
                        coverage_status=coverage.coverage_status,
                    )
                )

    # Volle Asset-Payloads (inkl. asset_origin/rights_status/provider/...)
    # übergeben, nicht nur Pfade — sonst blieben bei einer regelbedingten
    # Asset-Neuzuweisung (max_asset_usage/Min. Abstand) die Metadaten des
    # VORHER zugewiesenen Assets fälschlich stehen.
    shots = apply_edit_plan_rules(shots, rules_doc, assets_payload_by_folder)

    # Die Coverage-Bewertung pro Beat ist nur eine grobe Vorab-Schätzung
    # (Keyword-Heuristik auf den GESAMTEN Beat-Text, bevor Gemini/Regeln den
    # tatsächlichen Shot-Asset zuweisen). Sobald feststeht, dass alle aus einem
    # Beat entstandenen Shots am Ende wirklich ein lokales Asset bekommen haben,
    # darf die Coverage nicht mehr SUPPLEMENT_REQUIRED anzeigen — sonst entsteht
    # der widersprüchliche Zustand "0 Shots ohne Asset" + "N Beats offen".
    shots_by_beat: dict[tuple[str, str, str], list[EditPlanShot]] = {}
    for shot in shots:
        if shot.beat_id:
            shots_by_beat.setdefault((shot.folder, shot.voice_file, shot.beat_id), []).append(shot)

    def _beat_resolved(folder: str, voice_file: str, beat_id: str) -> bool:
        beat_shots = shots_by_beat.get((folder, voice_file, beat_id), [])
        return bool(beat_shots) and all(shot.asset_path for shot in beat_shots)

    resolved_request_ids: set[str] = set()
    reconciled_coverages: list = []
    for coverage in segment_coverages:
        if coverage.coverage_status in {COVERAGE_SUPPLEMENT_REQUIRED, COVERAGE_LOCAL_WEAK} and _beat_resolved(
            coverage.folder_name, coverage.voice_file, coverage.beat_id
        ):
            if coverage.supplement_request_id:
                resolved_request_ids.add(coverage.supplement_request_id)
            coverage = coverage.model_copy(
                update={"coverage_status": COVERAGE_LOCAL_GOOD, "supplement_request_id": None}
            )
        reconciled_coverages.append(coverage)
    segment_coverages = reconciled_coverages

    shots = [
        shot.model_copy(update={"coverage_status": ""})
        if shot.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
        and _beat_resolved(shot.folder, shot.voice_file, shot.beat_id)
        else shot
        for shot in shots
    ]

    if resolved_request_ids:
        pending_supplement_requests = [
            req for req in pending_supplement_requests if req.supplement_request_id not in resolved_request_ids
        ]
        supplement_request_ids = [
            request_id for request_id in supplement_request_ids if request_id not in resolved_request_ids
        ]

    if pending_supplement_requests:
        upsert_requests(project, pending_supplement_requests)

    timeline_items: list = []
    plan_errors: list[str] = []
    voiceover_plan = None
    item_counter = 1
    grouped: dict[tuple[str, str], list] = {}
    for shot in shots:
        if shot.section_outro:
            continue
        grouped.setdefault((shot.folder, shot.voice_file), []).append(shot)

    for (folder_name, voice_path), folder_shots in grouped.items():
        folder_shots.sort(key=lambda s: (s.voice_start_sec, s.voice_end_sec))
        section_items, section_voiceover, errors = build_timeline_items_for_folder(
            folder_shots,
            folder_name=folder_name,
            voice_file=voice_path,
            settings=plan_settings,
            folder_assets=assets_payload_by_folder.get(folder_name, []),
            trim_leading_sec=trim_leading_sec,
            item_index_start=item_counter,
            opening_title_enabled=export_opts.folder_title_enabled,
            opening_title_font=export_opts.folder_title_font,
            opening_title_duration_sec=export_opts.folder_title_duration_sec,
            opening_title_font_size=export_opts.folder_title_font_size,
            work_dir=project.work_dir_path,
            project=project,
            usage_by_asset_id={},
            max_asset_usage=max_count,
        )
        plan_errors.extend(errors)
        timeline_items.extend(section_items)
        voiceover_plan = section_voiceover
        item_counter += len(section_items)

    if plan_errors and not timeline_items:
        raise ValueError("\n".join(plan_errors))

    shots = shots_from_timeline_items(timeline_items)

    plan_inventory_hash = ""
    if primary_folder:
        plan_inventory_hash = inventory_hashes.get(primary_folder, "")
    elif inventory_hashes:
        plan_inventory_hash = next(iter(inventory_hashes.values()))

    return EditPlanDocument(
        project_id=project.id,
        folder_name=primary_folder,
        confirmed=False,
        settings=plan_settings,
        voiceover=voiceover_plan,
        shots=shots,
        timeline_items=timeline_items,
        segment_coverage=segment_coverages,
        inventory_hash_at_plan_time=plan_inventory_hash,
        supplement_request_ids=sorted(set(supplement_request_ids)),
    )


def _read_edit_plan_file(path: Path) -> EditPlanDocument | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def migrate_legacy_edit_plan(project: Project) -> list[Path]:
    """Teilt eine alte edit_plan.json im Projektroot in pro-Ort-Dateien auf."""
    legacy_path = get_edit_plan_path(project.project_root_path)
    if not legacy_path.is_file():
        return []

    document = _read_edit_plan_file(legacy_path)
    if document is None or not document.shots:
        return []

    saved: list[Path] = []
    by_folder: dict[str, list] = {}
    for shot in document.shots:
        by_folder.setdefault(shot.folder, []).append(shot)

    for folder_name, shots in by_folder.items():
        target = get_folder_edit_plan_path(project.work_dir_path, folder_name)
        if target.is_file():
            continue
        folder_doc = document.model_copy(
            update={
                "folder_name": folder_name,
                "shots": shots,
            }
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(folder_doc.model_dump_json(indent=2), encoding="utf-8")
        saved.append(target)

    if saved:
        backup = legacy_path.with_suffix(".json.migrated")
        legacy_path.rename(backup)
    return saved


def list_saved_edit_plan_folders(project: Project) -> list[str]:
    """Ordnernamen mit gespeicherter Schnittplan-JSON (nach Migration)."""
    migrate_legacy_edit_plan(project)
    edit_plan_dir = get_edit_plan_dir(project.work_dir_path)
    if not edit_plan_dir.is_dir():
        return []

    folders: list[str] = []
    for path in sorted(edit_plan_dir.glob("*.json")):
        document = _read_edit_plan_file(path)
        if document is None:
            continue
        folder_name = document.folder_name or _folder_name_from_shots(document)
        if folder_name:
            folders.append(folder_name)
    return folders


def _folder_name_from_shots(document: EditPlanDocument) -> str | None:
    folders = {shot.folder for shot in document.shots if shot.folder}
    if len(folders) == 1:
        return next(iter(folders))
    return None


def load_edit_plan(project: Project, folder_name: str) -> EditPlanDocument | None:
    from otio_app.services.edit_plan_cache import load_edit_plan_cached

    return load_edit_plan_cached(project, folder_name)


def save_edit_plan(
    project: Project,
    document: EditPlanDocument,
    folder_name: str,
) -> Path:
    path = get_folder_edit_plan_path(project.work_dir_path, folder_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = document.model_copy(
        update={
            "project_id": project.id,
            "folder_name": folder_name,
        }
    )
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    from otio_app.services.edit_plan_cache import invalidate_edit_plan_cache

    invalidate_edit_plan_cache(project.id, folder_name)
    return path


def mapped_folders_have_confirmed_plans(
    project: Project,
    folder_names: list[str],
) -> bool:
    from otio_app.services.edit_plan_cache import mapped_folders_all_confirmed

    return mapped_folders_all_confirmed(project, folder_names)
