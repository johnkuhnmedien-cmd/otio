"""Enhanced: bestätigtes Intro → Locked-Script + Audio-Reihenfolge."""

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
from otio_app.services.voiceover_generation.intro_hook_service import (
    save_confirmed_intro_hook,
)
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ElevenLabsSettings,
    IntroHookVisualBeat,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    synthesize_intro_script_audio,
    synthesize_locked_script_audio,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    list_cut_plan_chapter_names,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
    ENHANCED_INTRO_SEGMENT_ID,
    ensure_confirmed_intro_in_document,
    ensure_confirmed_intro_in_locked_script,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    group_segments_by_folder,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    lock_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Spain"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Rocamadour").mkdir()
    return Project(
        id="enh-intro",
        name="Enhanced Intro",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Rocamadour"],
        selected_asset_subdirs=["Rocamadour"],
    )


def _save_intro(project: Project, text: str = "Willkommen in Spanien.") -> None:
    save_confirmed_intro_hook(
        project,
        ConfirmedIntroHook(
            project_id=project.id,
            language="DE",
            hook_id="hook_1",
            hook_text=text,
            word_count=3,
            visual_beats=[
                IntroHookVisualBeat(
                    hook_beat_id="beat_1",
                    text=text,
                    visual_intent="wide establishing shot of cliffs",
                    source_folder_name="Rocamadour",
                )
            ],
        ),
    )


def test_ensure_intro_inserts_segment_and_intents(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _save_intro(project)
    doc = EnhancedScriptDocument(
        narration_full="Rocamadour thront über dem Tal.",
        segments=[
            ScriptSegment(
                segment_id="Rocamadour_segment_001",
                text="Rocamadour thront über dem Tal.",
                sequence_index=1,
                folder_name="Rocamadour",
                folder_order_index=1,
            )
        ],
    )
    assert ensure_confirmed_intro_in_document(project, doc) is True
    assert doc.segments[0].segment_id == ENHANCED_INTRO_SEGMENT_ID
    assert doc.segments[0].folder_name == ENHANCED_INTRO_FOLDER_NAME
    assert doc.segments[0].text == "Willkommen in Spanien."
    assert doc.segments[0].sequence_index == 1
    assert doc.segments[1].sequence_index == 2
    assert doc.narration_full.startswith("Willkommen in Spanien.")
    assert any(intent.intent_id == "intro_beat_1" for intent in doc.visual_intents)
    # Idempotent
    assert ensure_confirmed_intro_in_document(project, doc) is False


def test_lock_script_merges_confirmed_intro(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _save_intro(project, "Hook text here.")
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Kapitel eins.",
            segments=[
                ScriptSegment(
                    segment_id="Rocamadour_segment_001",
                    text="Kapitel eins.",
                    sequence_index=1,
                    folder_name="Rocamadour",
                    folder_order_index=1,
                )
            ],
            visual_intents=[VisualIntent(intent_id="i1", description="village")],
        ),
    )
    locked = lock_script(project)
    assert locked.segments[0].folder_name == ENHANCED_INTRO_FOLDER_NAME
    assert locked.segments[0].text == "Hook text here."
    reloaded = load_locked_script(project)
    assert reloaded is not None
    assert reloaded.segments[0].segment_id == ENHANCED_INTRO_SEGMENT_ID


def test_lock_script_accepts_intro_with_pause_markers(tmp_path: Path) -> None:
    """Intro-Hooks mit [pause N seconds] dürfen Script Lock nicht blockieren.

    Marker werden strukturiert; TTS injiziert daraus eleven_v3-Tags.
    """
    project = _project(tmp_path)
    _save_intro(
        project,
        "Willkommen in Irland.\n\n[pause 3 seconds]\n\nDie Küste wartet.",
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Kapitel eins.",
            segments=[
                ScriptSegment(
                    segment_id="Rocamadour_segment_001",
                    text="Kapitel eins.",
                    sequence_index=1,
                    folder_name="Rocamadour",
                    folder_order_index=1,
                )
            ],
        ),
    )
    locked = lock_script(project)
    intro_segs = [
        seg for seg in locked.segments if seg.folder_name == ENHANCED_INTRO_FOLDER_NAME
    ]
    assert len(intro_segs) == 2
    assert intro_segs[0].segment_id == ENHANCED_INTRO_SEGMENT_ID
    assert intro_segs[0].text == "Willkommen in Irland."
    assert intro_segs[0].author_pause_after_seconds == 3.0
    assert "[pause" not in intro_segs[0].text
    assert "[pause" not in (locked.narration_full or "")
    assert intro_segs[1].text == "Die Küste wartet."


def test_group_segments_puts_intro_first_even_if_not_in_folder_order(
    tmp_path: Path,
) -> None:
    doc = EnhancedScriptDocument(
        segments=[
            ScriptSegment(
                segment_id="a",
                text="A",
                sequence_index=1,
                folder_name="Rocamadour",
                folder_order_index=1,
            ),
            ScriptSegment(
                segment_id=ENHANCED_INTRO_SEGMENT_ID,
                text="Intro",
                sequence_index=2,
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
                folder_order_index=0,
            ),
        ]
    )
    groups = group_segments_by_folder(doc, folder_order=["Rocamadour"])
    assert [name for name, _ in groups] == [ENHANCED_INTRO_FOLDER_NAME, "Rocamadour"]


def test_ensure_locked_script_and_cut_chapter_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _save_intro(project)
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Rocamadour", order_index=1, enabled=True
                )
            ],
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Kapitel.",
            segments=[
                ScriptSegment(
                    segment_id="Rocamadour_segment_001",
                    text="Kapitel.",
                    sequence_index=1,
                    folder_name="Rocamadour",
                    folder_order_index=1,
                )
            ],
        ),
    )
    lock_script(project)
    # Simulate older locked script without Intro (strip then re-ensure).
    locked = load_locked_script(project)
    assert locked is not None
    locked.segments = [
        seg for seg in locked.segments if seg.folder_name != ENHANCED_INTRO_FOLDER_NAME
    ]
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.paths import script_locked_path

    write_json(script_locked_path(project), locked)

    synced = ensure_confirmed_intro_in_locked_script(project)
    assert synced is not None
    assert synced.segments[0].folder_name == ENHANCED_INTRO_FOLDER_NAME

    names = list_cut_plan_chapter_names(project)
    assert names[0] == ENHANCED_INTRO_FOLDER_NAME
    assert "Rocamadour" in names


def test_synthesize_all_includes_intro_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _save_intro(project, "Intro line.")
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Rocamadour", order_index=1, enabled=True
                )
            ],
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Kapitel.",
            segments=[
                ScriptSegment(
                    segment_id="Rocamadour_segment_001",
                    text="Kapitel.",
                    sequence_index=1,
                    folder_name="Rocamadour",
                    folder_order_index=1,
                )
            ],
        ),
    )
    lock_script(project)
    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc")
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")

    def _fake_tts(text: str, settings):  # noqa: ANN001
        return ElevenLabsTtsResult(
            audio_bytes=b"FAKE",
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
        lambda path: 1.0,
    )

    events: list[str] = []

    def _progress(
        folder_name: str,
        chapter_index: int,
        chapter_total: int,
        segment_index: int,
        segment_total: int,
    ) -> None:
        events.append(folder_name)

    doc = synthesize_locked_script_audio(project, progress_callback=_progress)
    assert events[0] == ENHANCED_INTRO_FOLDER_NAME
    assert ENHANCED_INTRO_SEGMENT_ID in {item.segment_id for item in doc.segments}
    assert len(doc.segments) == 2

    intro_only = synthesize_intro_script_audio(project)
    assert any(item.segment_id == ENHANCED_INTRO_SEGMENT_ID for item in intro_only.segments)
