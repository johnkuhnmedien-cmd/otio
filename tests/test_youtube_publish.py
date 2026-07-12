"""Tests für YouTube Publish (Kapitel, Quiz-Anzahl, Persistenz, Prompt)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import EditPlanSettings, TimelineItem, VoiceoverPlan
from otio_app.models import Project
from otio_app.services.otio_exporter import MergedEditPlanResult
from otio_app.services.voiceover_generation.models import (
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
)
from otio_app.services.voiceover_generation.prompts import build_youtube_publish_prompt
from otio_app.services.youtube_publish_models import YouTubeChapter
from otio_app.services.youtube_publish_service import (
    _append_chapters_to_description,
    _normalize_hashtags,
    _parse_quizzes,
    build_youtube_publish_context,
    format_youtube_timestamp,
    generate_youtube_publish_metadata,
    load_youtube_metadata,
    quiz_count_for_duration,
    save_youtube_metadata,
)
from otio_app.services.youtube_publish_models import YouTubeMetadataDocument


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / "_otio"
    (root / "Antelope Canyon").mkdir(parents=True)
    (root / "Grand Canyon").mkdir(parents=True)
    work.mkdir(parents=True)
    return Project(
        id="yt-test",
        name="USA Roadtrip",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        asset_subdir_names=["Antelope Canyon", "Grand Canyon"],
        selected_asset_subdirs=["Antelope Canyon", "Grand Canyon"],
    )


def test_format_youtube_timestamp() -> None:
    assert format_youtube_timestamp(0) == "00:00"
    assert format_youtube_timestamp(51) == "00:51"
    assert format_youtube_timestamp(143) == "02:23"
    assert format_youtube_timestamp(3723) == "1:02:03"


def test_quiz_count_for_duration() -> None:
    assert quiz_count_for_duration(0) == 1
    assert quiz_count_for_duration(300) == 1
    assert quiz_count_for_duration(600) == 1
    assert quiz_count_for_duration(601) == 2
    assert quiz_count_for_duration(1800) == 3


def test_append_chapters_and_hashtags_limits() -> None:
    chapters = [
        YouTubeChapter(
            folder_name="Antelope Canyon",
            display_title="Antelope Canyon",
            video_start_sec=51,
            timestamp="00:51",
        ),
        YouTubeChapter(
            folder_name="Grand Canyon",
            display_title="Grand Canyon",
            video_start_sec=143,
            timestamp="02:23",
        ),
    ]
    description = _append_chapters_to_description("Ein Roadtrip durch den Südwesten.", chapters)
    assert "Antelope Canyon - 00:51" in description
    assert "Grand Canyon - 02:23" in description
    assert description.startswith("Ein Roadtrip")

    tags = _normalize_hashtags("travel, #usa\n#canyon, travel")
    assert tags == "#travel, #usa, #canyon"
    long_tags = _normalize_hashtags(", ".join(f"tag{i}" for i in range(200)))
    assert len(long_tags) <= 500


def test_build_context_from_merged_timeline(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        project_title="USA Abenteuer",
        language="DE",
        intro=ConfirmedIntroPlanItem(hook_text="Der Südwesten wartet."),
        folders=[
            ConfirmedFolderPlanItem(
                folder_name="Antelope Canyon",
                order_index=1,
                voiceover_text_full="Slot Canyons und Lichtstrahlen.",
            ),
            ConfirmedFolderPlanItem(
                folder_name="Grand Canyon",
                order_index=2,
                voiceover_text_full="Der Canyon fällt steil ab.",
            ),
        ],
    )
    from otio_app.services.voiceover_generation.final_plan_service import (
        save_confirmed_voiceover_project_plan,
    )

    save_confirmed_voiceover_project_plan(project, plan)

    items = [
        TimelineItem(
            timeline_item_id="t1",
            type="video_shot",
            section_id="s1",
            folder_name="Antelope Canyon",
            voice_file="/vo/a.mp3",
            resolved_media_path="/a.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=51.0,
            duration_sec=51.0,
        ),
        TimelineItem(
            timeline_item_id="t2",
            type="video_shot",
            section_id="s2",
            folder_name="Grand Canyon",
            voice_file="/vo/b.mp3",
            resolved_media_path="/b.mp4",
            timeline_in_sec=51.0,
            timeline_out_sec=143.0,
            duration_sec=92.0,
        ),
    ]
    merged = MergedEditPlanResult(
        timeline_items=items,
        shots=[],
        settings=EditPlanSettings(),
        voiceovers=[
            VoiceoverPlan(path="/vo/a.mp3", duration_sec=40.0, timeline_start_sec=2.0),
            VoiceoverPlan(path="/vo/b.mp3", duration_sec=70.0, timeline_start_sec=1.0),
        ],
        included_folders=["Antelope Canyon", "Grand Canyon"],
    )
    context = build_youtube_publish_context(project, merged)
    assert context.title == "USA Abenteuer"
    assert context.language == "DE"
    assert context.chapters[0].timestamp == "00:00"
    assert context.chapters[1].timestamp == "00:51"
    assert context.chapters[1].display_title == "Grand Canyon"
    assert "Slot Canyons" in context.folder_scripts[0]["voiceover_text"]
    assert context.quiz_count == 1


def test_parse_quizzes_and_generate_with_mock_llm(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        project_title="USA",
        language="DE",
        folders=[
            ConfirmedFolderPlanItem(
                folder_name="Antelope Canyon",
                voiceover_text_full="Rote Felsen und enge Schluchten.",
            )
        ],
    )
    from otio_app.services.voiceover_generation.final_plan_service import (
        save_confirmed_voiceover_project_plan,
    )

    save_confirmed_voiceover_project_plan(project, plan)

    items = [
        TimelineItem(
            timeline_item_id="t1",
            type="video_shot",
            section_id="s1",
            folder_name="Antelope Canyon",
            voice_file="/vo/a.mp3",
            resolved_media_path="/a.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=700.0,
            duration_sec=700.0,
        )
    ]
    merged = MergedEditPlanResult(
        timeline_items=items,
        shots=[],
        settings=EditPlanSettings(),
        voiceovers=[VoiceoverPlan(path="/vo/a.mp3", duration_sec=600.0)],
        included_folders=["Antelope Canyon"],
    )

    class _Resp:
        raw_text = """{
          "title": "Antelope Canyon Guide",
          "description_body": "Ein Film über enge Schluchten und Licht.",
          "hashtags": "#AntelopeCanyon, #USA, #Travel",
          "quizzes": [
            {
              "order_index": 1,
              "question": "Wofür ist Antelope Canyon bekannt?",
              "options": [
                {"label": "A", "text": "Vulkane", "is_correct": false},
                {"label": "B", "text": "Slot Canyons und Licht", "is_correct": true},
                {"label": "C", "text": "Korallenriffe", "is_correct": false}
              ],
              "correct_option_label": "B",
              "insert_at_sec": 320,
              "reason": "Nach der Beschreibung der Schluchten"
            },
            {
              "order_index": 2,
              "question": "Welche Farbe prägt die Felsen?",
              "options": [
                {"label": "A", "text": "Blau", "is_correct": false},
                {"label": "B", "text": "Grün", "is_correct": false},
                {"label": "C", "text": "Rot", "is_correct": true}
              ],
              "correct_option_label": "C",
              "insert_at_sec": 600,
              "reason": "Gegen Ende des Kapitels"
            }
          ]
        }"""
        provider = "gemini"
        model = "gemini-test"
        latency_ms = 12
        token_usage = {"input": 1, "output": 2}

    with patch(
        "otio_app.services.youtube_publish_service.generate_plan_text_with_metadata",
        return_value=_Resp(),
    ):
        result = generate_youtube_publish_metadata(
            project,
            merged,
            provider="gemini",
            model="gemini-test",
        )

    assert result.status == "PASS"
    assert result.document is not None
    assert result.document.title == "Antelope Canyon Guide"
    assert "Antelope Canyon - 00:00" in result.document.description
    assert "#AntelopeCanyon" in result.document.hashtags
    assert len(result.document.quizzes) == 2
    assert result.document.quizzes[0].insert_timestamp == "05:20"
    assert result.document.quizzes[0].correct_option_label == "B"
    loaded = load_youtube_metadata(project)
    assert loaded is not None
    assert loaded.llm_run_id == result.llm_run_id


def test_youtube_publish_prompt_includes_language_and_quiz_count() -> None:
    prompt = build_youtube_publish_prompt(
        language="DE",
        title="USA",
        total_duration_sec=1200,
        quiz_count=2,
        chapters_block="- Antelope Canyon — 00:00",
        intro_text="Hook",
        folder_scripts_block="Script text",
        description_max_chars=5000,
        hashtags_max_chars=500,
    )
    assert "Target language code: DE" in prompt
    assert "EXACTLY 2 quiz" in prompt
    assert "Script text" in prompt


def test_save_load_roundtrip(tmp_path: Path) -> None:
    project = _project(tmp_path)
    doc = YouTubeMetadataDocument(
        project_id=project.id,
        title="T",
        description="D\n\nIntro - 00:00",
        hashtags="#a, #b",
        chapters=[
            YouTubeChapter(
                folder_name="Intro",
                display_title="Intro",
                timestamp="00:00",
            )
        ],
    )
    save_youtube_metadata(project, doc)
    loaded = load_youtube_metadata(project)
    assert loaded is not None
    assert loaded.title == "T"
    assert loaded.chapters[0].display_title == "Intro"


def test_parse_quizzes_fills_missing_correct_flag() -> None:
    quizzes = _parse_quizzes(
        {
            "quizzes": [
                {
                    "question": "Q?",
                    "options": [
                        {"label": "A", "text": "1"},
                        {"label": "B", "text": "2"},
                        {"label": "C", "text": "3"},
                    ],
                    "correct_option_label": "B",
                    "insert_at_sec": 10,
                }
            ]
        },
        quiz_count=1,
        total_duration_sec=100,
    )
    assert len(quizzes) == 1
    assert quizzes[0].correct_option_label == "B"
    assert quizzes[0].options[1].is_correct is True
