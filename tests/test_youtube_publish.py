"""Tests für YouTube Publish (Kapitel, Quiz-Anzahl, Persistenz, Prompt)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from otio_app.analysis_models import EditPlanSettings, TimelineItem, VoiceoverPlan
from otio_app.models import Project
from otio_app.services.otio_exporter import MergedEditPlanResult
from otio_app.services.voiceover_generation.models import (
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
)
from otio_app.services.voiceover_generation.prompts import (
    build_youtube_publish_prompt,
    build_youtube_quiz_prompt,
)
from otio_app.services.youtube_publish_models import YouTubeChapter
from otio_app.services.youtube_publish_service import (
    _append_chapters_to_description,
    _normalize_hashtags,
    _parse_quizzes,
    _parse_wonders_title,
    _prompt_from_context,
    build_youtube_publish_context,
    format_youtube_chapter_lines,
    format_youtube_timestamp,
    generate_youtube_publish_metadata,
    generate_youtube_quizzes,
    youtube_chapter_display_title,
    load_youtube_metadata,
    quiz_count_for_duration,
    save_youtube_metadata,
    youtube_chapter_display_title,
    youtube_country_folder_text_path,
    youtube_project_metadata_path,
)
from otio_app.services.youtube_publish_models import (
    YouTubeMetadataDocument,
    YouTubeQuizItem,
    YouTubeQuizOption,
)


def _project(tmp_path: Path, *, language: str = "de") -> Project:
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
        language=language,
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

    long_body = "x" * 3500
    combined = _append_chapters_to_description(long_body, chapters)
    assert len(combined) <= 5000
    assert "Antelope Canyon - 00:51" in combined

    tags = _normalize_hashtags("travel, #usa\n#canyon, travel #Natur")
    assert tags == "travel, usa, canyon, Natur"
    long_tags = _normalize_hashtags(", ".join(f"tag{i}" for i in range(200)))
    assert len(long_tags) <= 500
    assert "#" not in tags


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

    # Prompt muss Kapitelüberschriften enthalten, aber keine Folder-Skripte.
    prompt = _prompt_from_context(context)
    assert "Antelope Canyon" in prompt
    assert "Slot Canyons und Lichtstrahlen" not in prompt
    assert "Der Südwesten wartet" not in prompt


def test_generate_metadata_preserves_existing_quizzes(tmp_path: Path) -> None:
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

    save_youtube_metadata(
        project,
        YouTubeMetadataDocument(
            project_id=project.id,
            title="Alt",
            quizzes=[
                YouTubeQuizItem(
                    order_index=1,
                    question="Alte Frage?",
                    options=[
                        YouTubeQuizOption(label="A", text="1", is_correct=True),
                        YouTubeQuizOption(label="B", text="2"),
                        YouTubeQuizOption(label="C", text="3"),
                    ],
                    correct_option_label="A",
                    insert_at_sec=100,
                    insert_timestamp="01:40",
                )
            ],
        ),
    )

    class _Resp:
        raw_text = """{
          "title": "Antelope Canyon Guide",
          "wonders_title_formula": "Die Wunder von",
          "wonders_title_place": "den USA",
          "description_body": "Ein Film über enge Schluchten und Licht.",
          "hashtags": "#AntelopeCanyon, #USA, #Travel",
          "thumbnail_prompts": [
            "Photorealistic slot canyon walls in warm light, no text",
            "Photorealistic desert landscape at dusk, no text",
            "Photorealistic wide canyon floor, no logos"
          ],
          "quizzes": [{"question": "soll ignoriert werden"}]
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
    assert result.document.wonders_title_formula == "Die Wunder von"
    assert result.document.wonders_title_place == "den USA"
    assert result.document.formatted_wonders_title() == "Die Wunder von\nden USA"
    assert "Antelope Canyon - 00:00" in result.document.description
    assert "AntelopeCanyon" in result.document.hashtags
    assert "#" not in result.document.hashtags
    assert len(result.document.thumbnail_prompts) == 3
    assert "no text" in result.document.thumbnail_prompts[0]
    txt = youtube_country_folder_text_path(project).read_text(encoding="utf-8")
    assert txt.startswith("Titel\n")
    assert "Thumbnail-Prompts\n1. " in txt
    assert "Sprache\n" not in txt
    assert "Videotitel\n" not in txt
    assert "Kapitel\n" not in txt
    assert len(result.document.quizzes) == 1
    assert result.document.quizzes[0].question == "Alte Frage?"
    loaded = load_youtube_metadata(project)
    assert loaded is not None
    assert loaded.llm_run_id == result.llm_run_id


def test_generate_metadata_accepts_literal_newlines_in_description(
    tmp_path: Path,
) -> None:
    """Gemini schreibt oft echte Zeilenumbrüche in description_body — kein FAIL."""
    project = _project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        project_title="Grecia",
        language="IT",
        folders=[
            ConfirmedFolderPlanItem(
                folder_name="Athens",
                voiceover_text_full="Atene.",
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
            folder_name="Athens",
            voice_file="/vo/a.mp3",
            resolved_media_path="/a.mp4",
            timeline_in_sec=0.0,
            timeline_out_sec=120.0,
            duration_sec=120.0,
        )
    ]
    merged = MergedEditPlanResult(
        timeline_items=items,
        shots=[],
        settings=EditPlanSettings(),
        voiceovers=[VoiceoverPlan(path="/vo/a.mp3", duration_sec=100.0)],
        included_folders=["Athens"],
    )

    class _Resp:
        raw_text = (
            "{\n"
            '  "title": "Le meraviglie della Grecia",\n'
            '  "wonders_title_formula": "Le meraviglie della",\n'
            '  "wonders_title_place": "Grecia",\n'
            '  "description_body": "' + ("Un viaggio. " * 80) + "\n"
            'Secondo paragrafo della descrizione.",\n'
            '  "hashtags": "Grecia, Viaggi, Natura"\n'
            "}"
        )
        provider = "gemini"
        model = "gemini-test"
        latency_ms = 9
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

    assert result.status == "PASS", result.error
    assert result.document is not None
    assert "Secondo paragrafo" in result.document.description_body
    assert result.error in (None, "")


def test_generate_quizzes_separately(tmp_path: Path) -> None:
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
    save_youtube_metadata(
        project,
        YouTubeMetadataDocument(
            project_id=project.id,
            title="Bestehender Titel",
            wonders_title_formula="Die Wunder von",
            wonders_title_place="den USA",
            description_body="Bestehende Beschreibung",
            description="Bestehende Beschreibung\n\nAntelope Canyon - 00:00",
            hashtags="usa, canyon",
        ),
    )

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
        result = generate_youtube_quizzes(
            project,
            merged,
            provider="gemini",
            model="gemini-test",
        )

    assert result.status == "PASS"
    assert result.document is not None
    assert result.document.title == "Bestehender Titel"
    assert result.document.wonders_title_formula == "Die Wunder von"
    assert result.document.wonders_title_place == "den USA"
    assert result.document.hashtags == "usa, canyon"
    assert len(result.document.quizzes) == 2
    assert result.document.quizzes[0].insert_timestamp == "05:20"
    assert result.document.quizzes[0].correct_option_label == "B"


def test_youtube_publish_prompt_chapters_only_no_scripts() -> None:
    prompt = build_youtube_publish_prompt(
        language="DE",
        title="USA",
        total_duration_sec=1200,
        quiz_count=2,
        chapters_block="- Antelope Canyon — 00:00",
        intro_text="Hook that must be ignored",
        folder_scripts_block="Full script that must be ignored",
        description_max_chars=3500,
        hashtags_max_chars=500,
    )
    assert "Target language code: DE" in prompt
    assert "Antelope Canyon" in prompt
    assert "Hook that must be ignored" not in prompt
    assert "Full script that must be ignored" not in prompt
    assert "Do NOT invent quizzes" in prompt
    assert "wonders_title_formula" in prompt
    assert "Die Wunder von" in prompt
    assert "thumbnail_prompts" in prompt
    assert "Photorealistic" in prompt
    assert "never as raw line breaks" in prompt
    assert "EXACTLY 2 quiz" not in prompt


def test_youtube_quiz_prompt_chapters_only() -> None:
    prompt = build_youtube_quiz_prompt(
        language="DE",
        title="USA",
        total_duration_sec=1200,
        quiz_count=2,
        chapters_block="- Antelope Canyon — 00:00",
        option_count=3,
    )
    assert "EXACTLY 2 quiz" in prompt
    assert "Antelope Canyon" in prompt
    assert "Target language code: DE" in prompt
    assert "description_body" not in prompt


def test_save_load_roundtrip(tmp_path: Path) -> None:
    project = _project(tmp_path)
    doc = YouTubeMetadataDocument(
        project_id=project.id,
        title="T",
        description="D\n\nIntro - 00:00",
        hashtags="a, b",
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

    export_json = youtube_project_metadata_path(project)
    assert export_json == Path(project.project_root) / "Voice over" / "DE" / "youtube_metadata.json"
    assert export_json.is_file()
    exported = YouTubeMetadataDocument.model_validate_json(
        export_json.read_text(encoding="utf-8")
    )
    assert exported.title == "T"
    export_txt = youtube_country_folder_text_path(project)
    assert export_txt == Path(project.project_root) / "youtube_metadata_DE.txt"
    assert export_txt.is_file()
    text = export_txt.read_text(encoding="utf-8")
    assert "Titel\nT" in text
    assert "Beschreibung\nD\n\nIntro - 00:00" in text
    assert "Hashtags\na, b" in text
    assert "Intro - 00:00" in text
    assert "Thumbnail-Prompts\n1. " in text
    assert "Sprache\n" not in text
    assert "Videotitel\n" not in text
    assert loaded is not None
    assert len(loaded.thumbnail_prompts) == 3


def test_save_youtube_metadata_uses_language_folder(tmp_path: Path) -> None:
    project = _project(tmp_path, language="pt")
    save_youtube_metadata(
        project,
        YouTubeMetadataDocument(
            project_id=project.id,
            language="PT",
            title="As maravilhas dos EUA",
            wonders_title_formula="As maravilhas de",
            wonders_title_place="EUA",
            description="Um filme sobre canyons.",
            hashtags="EUA, Natureza",
        ),
    )
    export_json = youtube_project_metadata_path(project)
    assert export_json == Path(project.project_root) / "Voice over" / "PT" / "youtube_metadata.json"
    assert export_json.is_file()
    text = youtube_country_folder_text_path(project).read_text(encoding="utf-8")
    assert youtube_country_folder_text_path(project) == Path(project.project_root) / "youtube_metadata_PT.txt"
    assert "As maravilhas dos EUA" in text
    assert "Titel\nAs maravilhas dos EUA" in text
    assert "Hashtags\nEUA, Natureza" in text
    assert "Sprache\nPT" not in text
    assert "As maravilhas de\nEUA" not in text
    de_copy = Path(project.project_root) / "Voice over" / "DE" / "youtube_metadata.json"
    assert not de_copy.exists()


def test_parse_wonders_title_two_fields_and_newline_fallback() -> None:
    formula, place = _parse_wonders_title(
        {
            "wonders_title_formula": "Les merveilles de",
            "wonders_title_place": "la Grèce",
        }
    )
    assert formula == "Les merveilles de"
    assert place == "la Grèce"
    formula, place = _parse_wonders_title(
        {"on_screen_title": "The Wonders of\nGreece"}
    )
    assert formula == "The Wonders of"
    assert place == "Greece"
    doc = YouTubeMetadataDocument(
        project_id="x",
        wonders_title_formula="Die Wunder von",
        wonders_title_place="Griechenland",
    )
    assert doc.formatted_wonders_title() == "Die Wunder von\nGriechenland"


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


def test_youtube_chapter_titles_use_map_overlay_language() -> None:
    assert youtube_chapter_display_title("Vintgar-Klamm", language="IT") == "Gola di Vintgar"
    assert (
        youtube_chapter_display_title("Triglav-Nationalpark", language="IT")
        == "Parco nazionale del Triglav"
    )
    assert youtube_chapter_display_title("Intro", language="IT") == "Introduzione"
    assert youtube_chapter_display_title("Smartno", language="IT") == "Smartno"
    assert youtube_chapter_display_title("Vintgar-Klamm", language="DE") == "Vintgar-Klamm"

    chapters = [
        YouTubeChapter(folder_name="Intro", display_title="Intro", timestamp="00:00"),
        YouTubeChapter(
            folder_name="Vintgar-Klamm",
            display_title="Vintgar-Klamm",
            timestamp="13:53",
        ),
    ]
    lines = format_youtube_chapter_lines(chapters, "IT")
    assert "Introduzione - 00:00" in lines
    assert "Gola di Vintgar - 13:53" in lines
    assert "Vintgar-Klamm - 13:53" not in lines

    description = _append_chapters_to_description("Un viaggio in Slovenia.", chapters, "IT")
    assert "Gola di Vintgar - 13:53" in description
    assert "Vintgar-Klamm" not in description


def test_youtube_chapters_prefer_saved_map_labels(
    monkeypatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, language="it")
    item = MagicMock()
    item.chapter_id = "Bleder See"
    item.original_chapter_label = "Bleder See"
    item.localized_display_label = "Lago di Bled"
    item.language = "IT"
    plan = MagicMock(language="IT", maps=[item])
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.plan_service.load_map_plan",
        lambda _project: plan,
    )
    chapters = [
        YouTubeChapter(
            folder_name="Bleder See",
            display_title="Bleder See",
            timestamp="00:12",
        )
    ]
    lines = format_youtube_chapter_lines(chapters, "IT", project)
    assert "Lago di Bled - 00:12" in lines
    assert "Bleder See" not in lines


def test_youtube_ui_copies_localized_chapter_lines() -> None:
    src = Path("otio_app/ui/youtube_publish.py").read_text(encoding="utf-8")
    assert "format_youtube_chapter_lines" in src
    assert "youtube_description_for_copy" in src
    assert "Kapitelnamen wie auf der Karte" in src
    assert "youtube_country_folder_text_path" in src
    assert "Thumbnail-Prompts (ohne Text, realistisch)" in src
    assert "TXT im Länderordner" in src

