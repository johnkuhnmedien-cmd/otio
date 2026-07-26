"""Enhanced YouTube Publish: Kontext aus Resolved Timeline + Locked Script."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ResolvedChapterEnvelope,
    ResolvedTimelineDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    save_confirmed_intro_hook,
)
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    ProjectBrief,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    save_project_brief,
)
from otio_app.services.youtube_publish_service import (
    build_youtube_publish_context_from_resolved,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="yt-enh",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="fr",
        asset_subdir_names=["Yellowstone", "Bisti"],
        selected_asset_subdirs=["Yellowstone", "Bisti"],
    )


def test_build_youtube_context_from_resolved_timeline(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_project_brief(
        project,
        ProjectBrief(
            project_id=project.id,
            video_title="USA Incredible",
            language="FR",
        ),
    )
    save_confirmed_intro_hook(
        project,
        ConfirmedIntroHook(
            project_id=project.id,
            language="fr",
            hook_id="hook_001",
            hook_text="Bienvenue dans l'Ouest sauvage.",
            word_count=4,
            hook_type="cinematic_promise",
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            script_version="script-v1",
            narration_full="Yellowstone text. Bisti text.",
            segments=[
                ScriptSegment(
                    segment_id="s1",
                    text="Geysers et bisons à Yellowstone.",
                    sequence_index=1,
                    folder_name="Yellowstone",
                ),
                ScriptSegment(
                    segment_id="s2",
                    text="Formations étranges à Bisti.",
                    sequence_index=2,
                    folder_name="Bisti",
                ),
            ],
        ),
    )
    lock_script(project)

    resolved = ResolvedTimelineDocument(
        script_version="script-v1",
        total_duration_seconds=200.0,
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="ch_intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=2.0,
                chapter_audio_end=10.0,
                chapter_video_end=12.0,
            ),
            ResolvedChapterEnvelope(
                chapter_id="ch_ys",
                folder_name="Yellowstone",
                chapter_video_start=12.0,
                chapter_audio_start=14.0,
                chapter_audio_end=100.0,
                chapter_video_end=110.0,
            ),
            ResolvedChapterEnvelope(
                chapter_id="ch_bisti",
                folder_name="Bisti",
                chapter_video_start=110.0,
                chapter_audio_start=112.0,
                chapter_audio_end=190.0,
                chapter_video_end=200.0,
            ),
        ],
    )

    context = build_youtube_publish_context_from_resolved(project, resolved)
    assert context.title == "USA Incredible"
    assert context.language == "FR"
    assert [c.folder_name for c in context.chapters] == [
        "Intro",
        "Yellowstone",
        "Bisti",
    ]
    assert context.chapters[0].timestamp == "00:00"
    assert context.chapters[1].timestamp == "00:12"
    assert context.chapters[2].timestamp == "01:50"
    assert "Ouest sauvage" in context.intro_text
    assert "Yellowstone" in context.folder_scripts[1]["voiceover_text"]
    assert "Bisti" in context.folder_scripts[2]["voiceover_text"]
    assert context.quiz_count == 1
