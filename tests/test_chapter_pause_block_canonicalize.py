"""Kapitel speichern als Text + Autorenpausen (keine Satz-Segmente)."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    FactCheckHint,
    ScriptSegment,
    VisualBeat,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    parse_enhanced_script_response,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    canonicalize_script_document_to_pause_blocks,
    chapter_display_text,
    flatten_folder_segments_to_pause_blocks,
    parse_chapter_display_text,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Milos").mkdir(parents=True, exist_ok=True)
    return Project(
        name="Enhanced Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Milos"],
        selected_asset_subdirs=["Milos"],
        fps=25.0,
    )


def test_flatten_merges_segments_without_author_pause() -> None:
    segments = [
        ScriptSegment(
            segment_id="m_001",
            text="Milos ist vulkanisch.",
            sequence_index=1,
            folder_name="Milos",
            author_pause_after_seconds=0.0,
            visual_intent_ids=["intent_a"],
        ),
        ScriptSegment(
            segment_id="m_002",
            text="Sarakiniko glänzt weiß.",
            sequence_index=2,
            folder_name="Milos",
            author_pause_after_seconds=0.0,
            visual_intent_ids=["intent_b"],
        ),
        ScriptSegment(
            segment_id="m_003",
            text="Die Venus wurde gefunden.",
            sequence_index=3,
            folder_name="Milos",
            author_pause_after_seconds=5.0,
        ),
        ScriptSegment(
            segment_id="m_004",
            text="Obsidian prägte den Handel.",
            sequence_index=4,
            folder_name="Milos",
            author_pause_after_seconds=0.0,
        ),
    ]
    flat, id_map = flatten_folder_segments_to_pause_blocks(
        segments,
        folder_name="Milos",
        segment_id_prefix="milos_segment",
    )
    assert len(flat) == 2
    assert flat[0].author_pause_after_seconds == 5.0
    assert "Milos ist vulkanisch." in flat[0].text
    assert "Sarakiniko glänzt weiß." in flat[0].text
    assert "Die Venus wurde gefunden." in flat[0].text
    assert flat[0].visual_intent_ids == ["intent_a", "intent_b"]
    assert flat[1].text == "Obsidian prägte den Handel."
    assert id_map["m_001"] == flat[0].segment_id
    assert id_map["m_002"] == flat[0].segment_id
    assert id_map["m_003"] == flat[0].segment_id
    assert id_map["m_004"] == flat[1].segment_id

    rendered = chapter_display_text(flat)
    parsed = parse_chapter_display_text(
        rendered,
        folder_name="Milos",
        segment_id_prefix="roundtrip",
    )
    assert [seg.text.strip() for seg in parsed] == [seg.text.strip() for seg in flat]
    assert [seg.author_pause_after_seconds for seg in parsed] == [
        seg.author_pause_after_seconds for seg in flat
    ]


def test_lock_script_flattens_milos_style_segments(tmp_path: Path) -> None:
    project = _project(tmp_path)
    draft = EnhancedScriptDocument(
        narration_full="A. B. C. D.",
        segments=[
            ScriptSegment(
                segment_id="m_001",
                text="A.",
                sequence_index=1,
                folder_name="Milos",
                folder_order_index=2,
                author_pause_after_seconds=0.0,
            ),
            ScriptSegment(
                segment_id="m_002",
                text="B.",
                sequence_index=2,
                folder_name="Milos",
                folder_order_index=2,
                author_pause_after_seconds=4.0,
            ),
            ScriptSegment(
                segment_id="m_003",
                text="C.",
                sequence_index=3,
                folder_name="Milos",
                folder_order_index=2,
                author_pause_after_seconds=0.0,
            ),
            ScriptSegment(
                segment_id="m_004",
                text="D.",
                sequence_index=4,
                folder_name="Milos",
                folder_order_index=2,
                author_pause_after_seconds=3.0,
            ),
        ],
        visual_beats=[
            VisualBeat(
                beat_id="beat_1",
                description="coast",
                related_segment_ids=["m_001", "m_002"],
            )
        ],
        fact_check_hints=[
            FactCheckHint(
                hint_id="fact_1",
                related_segment_id="m_003",
                claim="C.",
            )
        ],
    )
    save_script_draft(project, draft)
    locked = lock_script(project)
    milos = [seg for seg in locked.segments if seg.folder_name == "Milos"]
    assert len(milos) == 2
    assert milos[0].author_pause_after_seconds == 4.0
    assert milos[1].author_pause_after_seconds == 3.0
    assert locked.visual_beats[0].related_segment_ids == [milos[0].segment_id]
    assert locked.fact_check_hints[0].related_segment_id == milos[1].segment_id


def test_parse_enhanced_script_response_flattens_folder_segments() -> None:
    payload = {
        "narration_full": "One. Two. Three.",
        "segments": [
            {
                "segment_id": "a_001",
                "text": "One.",
                "sequence_index": 1,
                "author_pause_after_seconds": 0,
            },
            {
                "segment_id": "a_002",
                "text": "Two.",
                "sequence_index": 2,
                "author_pause_after_seconds": 0,
            },
            {
                "segment_id": "a_003",
                "text": "Three.",
                "sequence_index": 3,
                "author_pause_after_seconds": 2,
            },
        ],
        "visual_beats": [
            {
                "beat_id": "b1",
                "description": "x",
                "related_segment_ids": ["a_001", "a_002"],
            }
        ],
        "fact_check_hints": [],
    }
    doc = parse_enhanced_script_response(
        payload, folder_name="Milos", folder_order_index=1
    )
    assert len(doc.segments) == 1
    assert doc.segments[0].author_pause_after_seconds == 2.0
    assert "One." in doc.segments[0].text and "Three." in doc.segments[0].text
    assert doc.visual_beats[0].related_segment_ids == [doc.segments[0].segment_id]


def test_canonicalize_is_idempotent_for_pause_blocks() -> None:
    doc = EnhancedScriptDocument(
        segments=[
            ScriptSegment(
                segment_id="s1",
                text="Alpha.",
                sequence_index=1,
                folder_name="Dublin",
                author_pause_after_seconds=3.0,
            ),
            ScriptSegment(
                segment_id="s2",
                text="Beta.",
                sequence_index=2,
                folder_name="Dublin",
                author_pause_after_seconds=0.0,
            ),
        ]
    )
    canonicalize_script_document_to_pause_blocks(doc)
    first = [(s.segment_id, s.text, s.author_pause_after_seconds) for s in doc.segments]
    canonicalize_script_document_to_pause_blocks(doc)
    second = [(s.segment_id, s.text, s.author_pause_after_seconds) for s in doc.segments]
    assert first == second
