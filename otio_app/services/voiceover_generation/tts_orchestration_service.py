"""TTS-Orchestrierung pro Ordner und Intro (Phase 6).

Ruft ElevenLabs auf, speichert Audio versioniert (nie überschreiben), schreibt
volle Traceability pro Lauf, aktualisiert das Audio-Manifest und baut das
Alignment. Schreibt niemals EditPlanDocuments und löst nie OTIO-Export aus.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    AUDIO_STATUS_FAILED,
    AUDIO_STATUS_READY,
    AUDIO_STATUS_STALE,
    TTS_RUN_STATUS_FAIL,
    TTS_RUN_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_audio_test_dir,
    get_folder_voiceover_audio_dir,
    get_intro_audio_dir,
    get_intro_tts_run_dir,
    get_tts_run_dir,
    get_voiceover_audio_manifest_path,
)
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.audio_alignment_service import (
    build_folder_alignment,
    build_intro_alignment,
    save_alignment,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    audio_extension_for_output_format,
    build_tts_request_metadata,
    synthesize_speech_with_timestamps,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import load_confirmed_intro_hook
from otio_app.services.voiceover_generation.llm_trace_service import content_hash
from otio_app.services.voiceover_generation.models import (
    ElevenLabsSettings,
    TtsRunManifest,
    VoiceoverAudioItem,
    VoiceoverAudioManifest,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    load_folder_voiceovers_confirmed,
)

__all__ = [
    "load_audio_manifest",
    "save_audio_manifest",
    "get_next_audio_version_path",
    "mark_stale_audio_if_needed",
    "synthesize_intro",
    "synthesize_folder_voiceover",
    "synthesize_all_confirmed_voiceovers",
    "synthesize_test_voice",
]

ProgressCallback = Callable[[str, int, int], None]


def _resolve_order_index(project: Project, folder_name: str) -> int:
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return 0
    entry = next((e for e in plan.recommended_folder_order if e.folder_name == folder_name), None)
    return entry.order_index if entry is not None else 0


def _text_hash(text: str) -> str:
    return content_hash(text)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        text = json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def load_audio_manifest(project: Project) -> VoiceoverAudioManifest:
    path = get_voiceover_audio_manifest_path(project.work_dir_path)
    if not path.is_file():
        return VoiceoverAudioManifest(project_id=project.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceoverAudioManifest.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return VoiceoverAudioManifest(project_id=project.id)


def save_audio_manifest(project: Project, manifest: VoiceoverAudioManifest) -> VoiceoverAudioManifest:
    normalized = manifest.model_copy(update={"project_id": project.id})
    path = get_voiceover_audio_manifest_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def _item_matches(existing: VoiceoverAudioItem, scope: str, folder_name: str) -> bool:
    if existing.scope != scope:
        return False
    if scope == AUDIO_SCOPE_INTRO:
        return True
    return existing.folder_name == folder_name


def _upsert_audio_item(manifest: VoiceoverAudioManifest, item: VoiceoverAudioItem) -> VoiceoverAudioManifest:
    items = [
        existing for existing in manifest.items if not _item_matches(existing, item.scope, item.folder_name)
    ]
    items.append(item)
    return manifest.model_copy(update={"items": items})


def get_next_audio_version_path(project: Project, scope: str, folder_name: str) -> tuple[Path, int]:
    """Nächste Audio-Version (voiceover_v001.mp3, v002, ...). Original-
    Dateien werden nie überschrieben — es wird immer ein neuer Pfad geliefert."""
    settings = load_elevenlabs_settings(project)
    extension, _ = audio_extension_for_output_format(settings.output_format)
    if scope == AUDIO_SCOPE_INTRO:
        audio_dir = get_intro_audio_dir(project.work_dir_path)
    else:
        order_index = _resolve_order_index(project, folder_name)
        audio_dir = get_folder_voiceover_audio_dir(project.work_dir_path, order_index, folder_name)
    audio_dir.mkdir(parents=True, exist_ok=True)

    version = 1
    while (audio_dir / f"voiceover_v{version:03d}{extension}").exists():
        version += 1
    return audio_dir / f"voiceover_v{version:03d}{extension}", version


def mark_stale_audio_if_needed(project: Project) -> VoiceoverAudioManifest:
    """Markiert Manifest-Items als STALE, wenn sich der zugehörige bestätigte
    Text geändert hat. Erzeugt NIEMALS automatisch neues Audio (§10)."""
    manifest = load_audio_manifest(project)
    confirmed_folder_doc = load_folder_voiceovers_confirmed(project)
    confirmed_folder_texts = {
        item.folder_name: item.voiceover_text_full for item in confirmed_folder_doc.items
    }
    confirmed_hook = load_confirmed_intro_hook(project)

    updated_items: list[VoiceoverAudioItem] = []
    changed = False
    for item in manifest.items:
        if item.status not in (AUDIO_STATUS_READY, AUDIO_STATUS_STALE):
            updated_items.append(item)
            continue

        if item.scope == AUDIO_SCOPE_INTRO:
            current_text = confirmed_hook.hook_text if confirmed_hook is not None else None
        else:
            current_text = confirmed_folder_texts.get(item.folder_name)

        if current_text is None:
            updated_items.append(item)
            continue

        current_hash = _text_hash(current_text)
        if current_hash != item.voiceover_text_hash and item.status != AUDIO_STATUS_STALE:
            updated_items.append(item.model_copy(update={"status": AUDIO_STATUS_STALE}))
            changed = True
        else:
            updated_items.append(item)

    manifest = manifest.model_copy(update={"items": updated_items})
    if changed:
        manifest = save_audio_manifest(project, manifest)
    return manifest


def _run_tts_and_update_manifest(
    project: Project,
    *,
    scope: str,
    folder_name: str,
    order_index: int,
    text: str,
    text_hash: str,
    settings: ElevenLabsSettings,
) -> VoiceoverAudioItem:
    tts_run_id = str(uuid.uuid4())
    if scope == AUDIO_SCOPE_INTRO:
        run_dir = get_intro_tts_run_dir(project.work_dir_path, tts_run_id)
    else:
        run_dir = get_tts_run_dir(project.work_dir_path, order_index, folder_name, tts_run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    request_metadata = build_tts_request_metadata(text, settings)
    request_metadata["text_hash"] = text_hash
    _write_json(run_dir / "elevenlabs_tts_request_metadata.json", request_metadata)

    effective_folder_name = folder_name if scope == AUDIO_SCOPE_FOLDER else ""
    effective_order_index = order_index if scope == AUDIO_SCOPE_FOLDER else 0

    try:
        result = synthesize_speech_with_timestamps(text, settings)
    except ElevenLabsTtsError as exc:
        error_path = run_dir / "tts_errors.json"
        _write_json(error_path, {"error": str(exc)})
        manifest_entry = TtsRunManifest(
            tts_run_id=tts_run_id,
            project_id=project.id,
            scope=scope,
            folder_name=effective_folder_name,
            order_index=effective_order_index,
            text_hash=text_hash,
            voice_id=settings.voice_id,
            model_id=settings.model_id,
            output_format=settings.output_format,
            status=TTS_RUN_STATUS_FAIL,
            error_path=str(error_path),
        )
        _write_json(run_dir / "tts_run_manifest.json", manifest_entry)

        item = VoiceoverAudioItem(
            scope=scope,
            folder_name=effective_folder_name,
            order_index=effective_order_index,
            voiceover_text_hash=text_hash,
            tts_run_id=tts_run_id,
            status=AUDIO_STATUS_FAILED,
            error_message=str(exc),
        )
        manifest = load_audio_manifest(project)
        manifest = _upsert_audio_item(manifest, item)
        save_audio_manifest(project, manifest)
        return item

    audio_path, version = get_next_audio_version_path(project, scope, folder_name)
    audio_path.write_bytes(result.audio_bytes)

    _write_json(run_dir / "elevenlabs_tts_response_metadata.json", result.response_metadata)
    timestamps_path = run_dir / "elevenlabs_timestamps.json"
    _write_json(
        timestamps_path,
        {"alignment": result.alignment, "normalized_alignment": result.normalized_alignment},
    )

    duration = probe_duration_seconds(audio_path)
    duration_missing = duration is None
    audio_duration_sec = duration or 0.0

    manifest_entry = TtsRunManifest(
        tts_run_id=tts_run_id,
        project_id=project.id,
        scope=scope,
        folder_name=effective_folder_name,
        order_index=effective_order_index,
        text_hash=text_hash,
        voice_id=settings.voice_id,
        model_id=settings.model_id,
        output_format=settings.output_format,
        status=TTS_RUN_STATUS_PASS,
        audio_path=str(audio_path),
        timestamps_path=str(timestamps_path),
    )
    _write_json(run_dir / "tts_run_manifest.json", manifest_entry)

    alignment_path_str = ""
    try:
        audio_item_for_alignment = VoiceoverAudioItem(
            scope=scope,
            folder_name=effective_folder_name,
            order_index=effective_order_index,
            audio_duration_sec=audio_duration_sec,
            audio_path=str(audio_path),
        )
        if scope == AUDIO_SCOPE_INTRO:
            alignment = build_intro_alignment(project, audio_item_for_alignment, result.alignment)
        else:
            alignment = build_folder_alignment(
                project, folder_name, audio_item_for_alignment, result.alignment
            )
        alignment_path = save_alignment(project, scope, folder_name, alignment)
        alignment_path_str = str(alignment_path)
    except ValueError:
        # Kein bestätigter Text (mehr) vorhanden — Audio bleibt trotzdem erhalten.
        alignment_path_str = ""

    error_message = "ffprobe konnte die Audiodauer nicht ermitteln." if duration_missing else ""

    item = VoiceoverAudioItem(
        scope=scope,
        folder_name=effective_folder_name,
        order_index=effective_order_index,
        voiceover_text_hash=text_hash,
        audio_path=str(audio_path),
        audio_version=version,
        audio_duration_sec=audio_duration_sec,
        timestamps_path=str(timestamps_path),
        alignment_path=alignment_path_str,
        tts_run_id=tts_run_id,
        status=AUDIO_STATUS_READY,
        error_message=error_message,
    )
    manifest = load_audio_manifest(project)
    manifest = manifest.model_copy(
        update={
            "tts_model": settings.model_id,
            "voice_id": settings.voice_id,
            "output_format": settings.output_format,
        }
    )
    manifest = _upsert_audio_item(manifest, item)
    save_audio_manifest(project, manifest)
    return item


def synthesize_folder_voiceover(project: Project, folder_name: str) -> VoiceoverAudioItem:
    """Vertont EINEN bestätigten Ordner. Ist der Text unverändert und ein
    aktives Audio bereits AUDIO_READY, wird NICHT automatisch neu vertont —
    der Aufrufer (UI-Klick) entscheidet über eine echte Neuvertonung, indem
    diese Funktion trotzdem aufgerufen wird (Re-TTS ist immer ein bewusster
    Klick, siehe audio_tab.py)."""
    confirmed_document = load_folder_voiceovers_confirmed(project)
    draft = next((item for item in confirmed_document.items if item.folder_name == folder_name), None)
    if draft is None:
        raise ValueError(f"Kein bestätigter Voice-over-Text für '{folder_name}' vorhanden.")

    order_index = _resolve_order_index(project, folder_name)
    text = draft.voiceover_text_full
    text_hash = _text_hash(text)
    settings = load_elevenlabs_settings(project)

    manifest = load_audio_manifest(project)
    existing = next(
        (item for item in manifest.items if item.scope == AUDIO_SCOPE_FOLDER and item.folder_name == folder_name),
        None,
    )
    if existing is not None and existing.voiceover_text_hash == text_hash and existing.status == AUDIO_STATUS_READY:
        return existing

    return _run_tts_and_update_manifest(
        project,
        scope=AUDIO_SCOPE_FOLDER,
        folder_name=folder_name,
        order_index=order_index,
        text=text,
        text_hash=text_hash,
        settings=settings,
    )


def synthesize_intro(project: Project) -> VoiceoverAudioItem:
    confirmed_hook = load_confirmed_intro_hook(project)
    if confirmed_hook is None:
        raise ValueError("Kein bestätigter Intro-Hook vorhanden.")

    text = confirmed_hook.hook_text
    text_hash = _text_hash(text)
    settings = load_elevenlabs_settings(project)

    manifest = load_audio_manifest(project)
    existing = next((item for item in manifest.items if item.scope == AUDIO_SCOPE_INTRO), None)
    if existing is not None and existing.voiceover_text_hash == text_hash and existing.status == AUDIO_STATUS_READY:
        return existing

    return _run_tts_and_update_manifest(
        project,
        scope=AUDIO_SCOPE_INTRO,
        folder_name="",
        order_index=0,
        text=text,
        text_hash=text_hash,
        settings=settings,
    )


def synthesize_all_confirmed_voiceovers(
    project: Project, *, progress_callback: ProgressCallback | None = None
) -> VoiceoverAudioManifest:
    """Sequenziell: Intro zuerst, danach alle bestätigten Ordner in
    Dramaturgie-Reihenfolge. Kein Parallel-TTS (§11)."""
    confirmed_document = load_folder_voiceovers_confirmed(project)
    confirmed_hook = load_confirmed_intro_hook(project)

    scopes: list[tuple[str, str]] = []
    if confirmed_hook is not None:
        scopes.append((AUDIO_SCOPE_INTRO, ""))
    for item in sorted(confirmed_document.items, key=lambda item: item.order_index):
        scopes.append((AUDIO_SCOPE_FOLDER, item.folder_name))

    total = len(scopes)
    for index, (scope, folder_name) in enumerate(scopes, start=1):
        label = "Intro" if scope == AUDIO_SCOPE_INTRO else folder_name
        if progress_callback is not None:
            progress_callback(label, index, total)
        try:
            if scope == AUDIO_SCOPE_INTRO:
                synthesize_intro(project)
            else:
                synthesize_folder_voiceover(project, folder_name)
        except (ElevenLabsTtsError, ValueError):
            # Fehler ist bereits im Manifest/tts_errors.json dokumentiert (TTS-Fehler)
            # bzw. es gibt schlicht keinen bestätigten Text (ValueError) — Batch läuft weiter.
            continue

    return load_audio_manifest(project)


def synthesize_test_voice(project: Project, text: str) -> Path:
    """Kurzer Testruf ohne Manifest-Eintrag — nur zum Anhören der aktuellen
    ElevenLabs-Einstellungen (§11, optional). Wirft ElevenLabsTtsError bei
    Problemen; die UI zeigt den Fehler direkt an."""
    settings = load_elevenlabs_settings(project)
    result = synthesize_speech_with_timestamps(text, settings)

    test_dir = get_audio_test_dir(project.work_dir_path)
    test_dir.mkdir(parents=True, exist_ok=True)
    extension, _ = audio_extension_for_output_format(settings.output_format)
    version = 1
    while (test_dir / f"test_voice_v{version:03d}{extension}").exists():
        version += 1
    path = test_dir / f"test_voice_v{version:03d}{extension}"
    path.write_bytes(result.audio_bytes)
    return path
