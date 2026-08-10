"""Enhanced TTS: ein ElevenLabs-Call pro Kapitel, nie pro Segment."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.elevenlabs_client import ElevenLabsTtsResult
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ElevenLabsSettings,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    CHAPTER_AUDIO_OPEN,
    CHAPTER_AUDIO_READY,
    list_chapter_audio_statuses,
    synthesize_folder_script_audio,
    synthesize_open_chapters_audio,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.paths import chapter_audio_path
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Ireland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Dublin").mkdir()
    return Project(
        id="enh-chapter-tts",
        name="Chapter TTS",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
    )


def test_folder_tts_uses_single_elevenlabs_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Dublin", order_index=1, enabled=True
                )
            ],
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="One. Two. Three.",
            segments=[
                ScriptSegment(
                    segment_id="Dublin_segment_001",
                    text="One.",
                    sequence_index=1,
                    folder_name="Dublin",
                    folder_order_index=1,
                    author_pause_after_seconds=3.0,
                ),
                ScriptSegment(
                    segment_id="Dublin_segment_002",
                    text="Two.",
                    sequence_index=2,
                    folder_name="Dublin",
                    folder_order_index=1,
                    author_pause_after_seconds=2.0,
                ),
                ScriptSegment(
                    segment_id="Dublin_segment_003",
                    text="Three.",
                    sequence_index=3,
                    folder_name="Dublin",
                    folder_order_index=1,
                ),
            ],
        ),
    )
    lock_script(project)
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(
            project_id=project.id,
            voice_id="voice-abc",
            model_id="eleven_v3",
            output_format="wav_48000",
        ),
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")

    calls: list[str] = []

    def _fake_tts(text: str, settings):  # noqa: ANN001
        calls.append(text)
        return ElevenLabsTtsResult(
            audio_bytes=b"FAKEWAV",
            alignment={},
            normalized_alignment={},
            response_metadata={"status_code": 200},
        )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.audio_timing_service."
        "synthesize_speech_with_timestamps",
        _fake_tts,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.audio_timing_service."
        "measure_audio_duration_seconds",
        lambda path: 3.0,
    )

    statuses_before = list_chapter_audio_statuses(project)
    assert len(statuses_before) == 1
    assert statuses_before[0].status == CHAPTER_AUDIO_OPEN
    assert statuses_before[0].is_open is True

    doc = synthesize_folder_script_audio(project, "Dublin")
    assert len(calls) == 1
    assert calls[0] == "One. [pause 3 seconds] Two. [pause 2 seconds] Three."
    assert len(doc.segments) == 3
    assert all(item.audio_status == "valid" for item in doc.segments)
    chapter_file = chapter_audio_path(project, "Dublin", ".wav")
    assert chapter_file.is_file()
    assert chapter_file.read_bytes() == b"FAKEWAV"
    assert all(item.audio_path == str(chapter_file) for item in doc.segments)
    assert not (chapter_file.parent.parent / "segments").exists()

    statuses_after = list_chapter_audio_statuses(project)
    assert statuses_after[0].status == CHAPTER_AUDIO_READY
    assert statuses_after[0].is_open is False

    # Offsets liegen in der Kapitel-WAV; OTIO nutzt source_start, kein Slice-File.
    assert doc.segments[0].source_start_seconds == 0.0
    assert doc.segments[-1].source_end_seconds == 3.0
    assert all(
        item.source_end_seconds > item.source_start_seconds for item in doc.segments
    )

    # Bereits vertont → Alle offenen macht keinen weiteren Call.
    synthesize_open_chapters_audio(project)
    assert len(calls) == 1
