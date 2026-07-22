"""ElevenLabs-Timestamps + Satzzeiten beim Enhanced-TTS."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.elevenlabs_client import ElevenLabsTtsResult
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    ElevenLabsSettings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    synthesize_locked_script_audio,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    segment_alignments_path,
    segment_sentence_alignment_path,
    segment_timestamps_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    build_segment_alignment,
    split_segment_into_sentences,
)


def test_split_segment_into_sentences() -> None:
    assert split_segment_into_sentences("One. Two? Three!") == [
        "One.",
        "Two?",
        "Three!",
    ]
    assert split_segment_into_sentences("  Alone  ") == ["Alone"]


def test_build_segment_alignment_maps_sentence_times() -> None:
    text = "Hello world. Next sentence."
    # One timestamp slot per character in the TTS text.
    starts = [i * 0.05 for i in range(len(text))]
    ends = [start + 0.05 for start in starts]
    alignment = build_segment_alignment(
        segment_id="Sedona_segment_001",
        script_version="script-v1",
        audio_path="/tmp/a.mp3",
        audio_duration_seconds=ends[-1],
        tts_text=text,
        timestamps_path="/tmp/ts.json",
        elevenlabs_alignment={
            "characters": list(text),
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
    )
    assert len(alignment.sentences) == 2
    assert alignment.sentences[0].sentence_id == "Sedona_segment_001__s001"
    assert alignment.sentences[0].text == "Hello world."
    assert alignment.sentences[1].text == "Next sentence."
    assert alignment.sentences[0].start_seconds < alignment.sentences[1].start_seconds
    assert alignment.sentences[1].end_seconds > alignment.sentences[0].end_seconds


def test_synthesize_persists_timestamps_and_sentence_alignment(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Sedona").mkdir()
    project = Project(
        id="enh-align",
        name="Enhanced Align",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Sedona"],
        selected_asset_subdirs=["Sedona"],
    )
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Sedona", order_index=1, enabled=True
                )
            ],
        ),
    )
    text = "Red rocks glow. Night falls."
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full=text,
            segments=[
                ScriptSegment(
                    segment_id="Sedona_segment_001",
                    text=text,
                    sequence_index=1,
                    folder_name="Sedona",
                    folder_order_index=1,
                )
            ],
            visual_intents=[
                VisualIntent(intent_id="intent_001", description="rocks")
            ],
        ),
    )
    lock_script(project)
    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc")
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")

    starts = [i * 0.04 for i in range(len(text))]
    ends = [start + 0.04 for start in starts]

    def _fake_tts(tts_text: str, settings):  # noqa: ANN001
        return ElevenLabsTtsResult(
            audio_bytes=b"FAKEAUDIO",
            alignment={
                "characters": list(tts_text),
                "character_start_times_seconds": starts[: len(tts_text)],
                "character_end_times_seconds": ends[: len(tts_text)],
            },
            normalized_alignment={
                "characters": list(tts_text),
                "character_start_times_seconds": starts[: len(tts_text)],
                "character_end_times_seconds": ends[: len(tts_text)],
            },
            response_metadata={"status_code": 200, "request_id": "req-1"},
        )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.audio_timing_service."
        "synthesize_speech_with_timestamps",
        _fake_tts,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.audio_timing_service."
        "measure_audio_duration_seconds",
        lambda path: ends[-1],
    )

    doc = synthesize_locked_script_audio(project)
    assert len(doc.segments) == 1
    item = doc.segments[0]
    assert item.timestamps_path
    assert item.alignment_path
    assert Path(item.timestamps_path).is_file()
    assert Path(item.alignment_path).is_file()

    raw = json.loads(Path(item.timestamps_path).read_text(encoding="utf-8"))
    assert "alignment" in raw
    assert "normalized_alignment" in raw
    assert raw["alignment"]["character_start_times_seconds"]

    assert segment_timestamps_path(project, "Sedona_segment_001").is_file()
    assert segment_sentence_alignment_path(project, "Sedona_segment_001").is_file()
    assert segment_alignments_path(project).is_file()

    index = json.loads(segment_alignments_path(project).read_text(encoding="utf-8"))
    assert index["schema_version"] == "enhanced-segment-alignments-v1"
    assert len(index["segments"]) == 1
    sentences = index["segments"][0]["sentences"]
    assert len(sentences) == 2
    assert sentences[0]["text"] == "Red rocks glow."
    assert sentences[1]["text"] == "Night falls."
    assert sentences[0]["start_seconds"] < sentences[1]["start_seconds"]
