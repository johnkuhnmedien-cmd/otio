"""Phase 6: Audio-Alignment-Service — Algorithmus, Folder- und Intro-Alignment."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import (
    ALIGNMENT_WARNING_NON_MONOTONIC_TIMESTAMPS,
    ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND,
    ALIGNMENT_WARNING_USED_PROPORTIONAL_FALLBACK,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.audio_alignment_service import (
    _align_segments,
    build_folder_alignment,
    build_intro_alignment,
    load_alignment,
    save_alignment,
)
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    FolderVoiceoverDraft,
    FolderVoiceoversDocument,
    IntroHookVisualBeat,
    SentenceItem,
    VoiceoverAlignment,
    VoiceoverAudioItem,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    save_folder_voiceovers_confirmed,
)
from otio_app.services.voiceover_generation.intro_hook_service import save_confirmed_intro_hook


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="alignment-project",
        name="Alignment Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


# --- _align_segments (Kernalgorithmus) ---


def test_align_segments_finds_exact_matches() -> None:
    full_text = "Hello world. This is a test."
    segments = [("s1", "Hello world."), ("s2", "This is a test.")]
    alignment = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    results, warnings = _align_segments(full_text, segments, alignment)
    assert "s1" in results
    assert "s2" in results
    assert results["s1"][0] < results["s2"][0]
    assert not any(w.startswith(ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND) for w in warnings)


def test_align_segments_tolerates_whitespace_and_punctuation_differences() -> None:
    full_text = "Hello,   world!  This is a test."
    segments = [("s1", "Hello world"), ("s2", "This is a test")]
    alignment = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    results, warnings = _align_segments(full_text, segments, alignment)
    assert not any(w.startswith(ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND) for w in warnings)
    assert "s1" in results
    assert "s2" in results


def test_align_segments_uses_proportional_fallback_when_not_found() -> None:
    full_text = "Completely different text than the segments."
    segments = [("s1", "This text does not appear at all")]
    alignment = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    results, warnings = _align_segments(full_text, segments, alignment)
    assert "s1" in results
    assert any(w.startswith(ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND) for w in warnings)
    assert any(w.startswith(ALIGNMENT_WARNING_USED_PROPORTIONAL_FALLBACK) for w in warnings)


def test_align_segments_detects_non_monotonic_timestamps() -> None:
    full_text = "Hi"
    segments = [("s1", "Hi")]
    alignment = {
        "characters": ["H", "i"],
        "character_start_times_seconds": [0.5, 0.1],
        "character_end_times_seconds": [0.6, 0.2],
    }
    _, warnings = _align_segments(full_text, segments, alignment)
    assert ALIGNMENT_WARNING_NON_MONOTONIC_TIMESTAMPS in warnings


def test_align_segments_missing_timestamps_returns_empty_with_warning() -> None:
    results, warnings = _align_segments("Hi", [("s1", "Hi")], {})
    assert results == {}
    assert any("MISSING_CHARACTER_TIMESTAMPS" in w for w in warnings)


def test_align_segments_turkish_dotted_i_does_not_crash() -> None:
    full_text = (
        "Mount Ararat and İshak Pasha Palace stand above the plateau."
    )
    segments = [("s1", full_text)]
    alignment = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    results, warnings = _align_segments(full_text, segments, alignment)
    assert "s1" in results
    assert results["s1"][1] > results["s1"][0]
    assert not any(w.startswith(ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND) for w in warnings)


def test_align_segments_empty_segment_text_is_warned() -> None:
    full_text = "Hello"
    alignment = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    _, warnings = _align_segments(full_text, [("s1", "   ")], alignment)
    assert any("EMPTY_SEGMENT_TEXT" in w for w in warnings)


# --- build_folder_alignment ---


def test_folder_alignment_contains_sentence_and_beat_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Hello world. This is a test.",
        word_count=6,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", beat_id="beat_001", text="Hello world.", primary_asset_id="asset_1"),
            SentenceItem(sentence_id="sentence_002", beat_id="beat_002", text="This is a test.", primary_asset_id="asset_2"),
        ],
    )
    save_folder_voiceovers_confirmed(project, FolderVoiceoversDocument(project_id=project.id, items=[draft]))

    full_text = draft.voiceover_text_full
    alignment_data = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    audio_item = VoiceoverAudioItem(scope="folder", folder_name="Grand Canyon", audio_duration_sec=len(full_text) * 0.1)

    alignment = build_folder_alignment(project, "Grand Canyon", audio_item, alignment_data)
    assert [item.sentence_id for item in alignment.items] == ["sentence_001", "sentence_002"]
    assert [item.beat_id for item in alignment.items] == ["beat_001", "beat_002"]


def test_folder_alignment_contains_audio_start_and_end(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Hello world.",
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Hello world.")],
    )
    save_folder_voiceovers_confirmed(project, FolderVoiceoversDocument(project_id=project.id, items=[draft]))
    full_text = draft.voiceover_text_full
    alignment_data = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    audio_item = VoiceoverAudioItem(scope="folder", folder_name="Grand Canyon")
    alignment = build_folder_alignment(project, "Grand Canyon", audio_item, alignment_data)
    assert alignment.items[0].audio_start_sec == 0.0
    assert alignment.items[0].audio_end_sec > 0.0


def test_folder_alignment_preserves_primary_asset_id_and_supplement_flag(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Hello world.",
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001", text="Hello world.", primary_asset_id="asset_1",
                needs_supplement_asset=True, supplement_reason="Kein Asset gefunden.",
            )
        ],
    )
    save_folder_voiceovers_confirmed(project, FolderVoiceoversDocument(project_id=project.id, items=[draft]))
    full_text = draft.voiceover_text_full
    alignment_data = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    audio_item = VoiceoverAudioItem(scope="folder", folder_name="Grand Canyon")
    alignment = build_folder_alignment(project, "Grand Canyon", audio_item, alignment_data)
    assert alignment.items[0].primary_asset_id == "asset_1"
    assert alignment.items[0].needs_supplement_asset is True
    assert alignment.items[0].supplement_reason == "Kein Asset gefunden."


def test_build_folder_alignment_uses_tts_text_when_provided(tmp_path: Path) -> None:
    """Nutzerfeedback (Pausen): bei eleven_v3 kann der tatsächlich an
    ElevenLabs gesendete Text (mit Pause-Tag) von voiceover_text_full
    abweichen — build_folder_alignment muss dann gegen DIESEN Text aligned
    werden, nicht gegen den ungetaggten Fließtext."""
    project = _make_project(tmp_path)
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Erster Satz. Zweiter Satz.",
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", pause_after="long"),
            SentenceItem(sentence_id="sentence_002", text="Zweiter Satz."),
        ],
    )
    save_folder_voiceovers_confirmed(project, FolderVoiceoversDocument(project_id=project.id, items=[draft]))

    # Das ist der Text, der TATSÄCHLICH an ElevenLabs gesendet wurde (inkl.
    # Pause-Tag) — länger als voiceover_text_full.
    tts_text = "Erster Satz. [long pause] Zweiter Satz."
    alignment_data = {
        "characters": list(tts_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(tts_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(tts_text))],
    }
    audio_item = VoiceoverAudioItem(scope="folder", folder_name="Grand Canyon")

    alignment = build_folder_alignment(
        project, "Grand Canyon", audio_item, alignment_data, tts_text=tts_text
    )

    # Ohne tts_text würde "Zweiter Satz." NICHT an der erwarteten (späteren)
    # Position im kürzeren voiceover_text_full gefunden -> hier muss es aber
    # korrekt und ohne Fallback-Warnung ausgerichtet sein.
    assert ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND not in "".join(alignment.alignment_warnings)
    assert alignment.items[1].audio_start_sec > alignment.items[0].audio_end_sec


def test_build_folder_alignment_falls_back_to_voiceover_text_full_without_tts_text(
    tmp_path: Path,
) -> None:
    """Rückwärtskompatibilität: ohne tts_text-Parameter verhält sich
    build_folder_alignment exakt wie vorher."""
    project = _make_project(tmp_path)
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Hello world.",
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Hello world.")],
    )
    save_folder_voiceovers_confirmed(project, FolderVoiceoversDocument(project_id=project.id, items=[draft]))
    full_text = draft.voiceover_text_full
    alignment_data = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    audio_item = VoiceoverAudioItem(scope="folder", folder_name="Grand Canyon")
    alignment = build_folder_alignment(project, "Grand Canyon", audio_item, alignment_data)
    assert alignment.items[0].audio_start_sec == 0.0
    assert alignment.items[0].audio_end_sec > 0.0


# --- build_intro_alignment ---


def test_intro_alignment_based_on_visual_beats(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    hook = ConfirmedIntroHook(
        project_id=project.id,
        hook_id="hook_001",
        hook_text="A place of mystery awaits every traveler.",
        visual_beats=[
            IntroHookVisualBeat(
                hook_beat_id="hook_beat_001", text="A place of mystery awaits every traveler.",
                primary_asset_id="asset_1",
            )
        ],
    )
    save_confirmed_intro_hook(project, hook)
    full_text = hook.hook_text
    alignment_data = {
        "characters": list(full_text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(full_text))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(full_text))],
    }
    audio_item = VoiceoverAudioItem(scope="intro")
    alignment = build_intro_alignment(project, audio_item, alignment_data)
    assert alignment.scope == "intro"
    assert alignment.folder_name == ""
    assert alignment.items[0].sentence_id == "hook_beat_001"
    assert alignment.items[0].primary_asset_id == "asset_1"


# --- save/load ---


def test_save_and_load_folder_alignment_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
    from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan

    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1)],
        ),
    )
    alignment = VoiceoverAlignment(project_id=project.id, scope="folder", folder_name="Grand Canyon")
    save_alignment(project, "folder", "Grand Canyon", alignment)

    loaded = load_alignment(project, "folder", "Grand Canyon")
    assert loaded is not None
    assert loaded.folder_name == "Grand Canyon"


def test_load_alignment_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_alignment(project, "folder", "Grand Canyon") is None
