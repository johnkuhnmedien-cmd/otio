"""Cross-language accepted supplements for Unified LLM Cut."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cross_language_supplements import (
    folder_matches_supplement,
    gap_folder_hint,
    iter_sibling_language_dirs,
    load_sibling_export_ready_supplements,
    sibling_supplement_rows_for_cut_plan,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    _local_assets_payload,
)
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import STOCK_SUBDIR


def _project(tmp_path: Path, *, language: str = "en") -> Project:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        asset_subdir_names=["Yellowstone", "Bisti"],
        selected_asset_subdirs=["Yellowstone", "Bisti"],
    )


def _write_accepted(
    lang_dir: Path,
    *,
    candidates: list[StockCandidate],
) -> Path:
    stock = lang_dir / VOICEOVER_GENERATION_SUBDIR / STOCK_SUBDIR
    stock.mkdir(parents=True, exist_ok=True)
    path = stock / "accepted_supplements.json"
    doc = AcceptedSupplementsDocument(
        schema_version="enhanced-accepted-supplements-v1",
        script_version="v1",
        supplements=candidates,
    )
    path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_gap_folder_hint_and_match() -> None:
    assert gap_folder_hint("Yellowstone_gap_001") == "Yellowstone"
    assert gap_folder_hint("Grand_Canyon_gap_002") == "Grand_Canyon"
    assert folder_matches_supplement(
        "Yellowstone",
        gap_id="Yellowstone_gap_001",
        media_path="/x/clean/Yellowstone/clip.mp4",
    )
    assert not folder_matches_supplement(
        "Bisti",
        gap_id="Yellowstone_gap_001",
        media_path="/x/clean/Yellowstone/clip.mp4",
    )


def test_sibling_dirs_and_rows(tmp_path: Path) -> None:
    project = _project(tmp_path, language="en")
    work = Path(project.work_dir)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake-video-bytes-long-enough")

    de_dir = work / "DE"
    _write_accepted(
        de_dir,
        candidates=[
            StockCandidate(
                candidate_id="pexels_video_111",
                provider="pexels",
                title="Bison herd",
                media_type="video",
                gap_id="Yellowstone_gap_003",
                local_media_path=str(media),
                media_validation_status=STATUS_EXPORT_READY,
                duration_seconds=8.5,
                selected=True,
            ),
            StockCandidate(
                candidate_id="missing_file",
                provider="pexels",
                title="Gone",
                media_type="video",
                gap_id="Bisti_gap_001",
                local_media_path=str(tmp_path / "nope.mp4"),
                media_validation_status=STATUS_EXPORT_READY,
                selected=True,
            ),
        ],
    )
    # Current language also has a file — must not appear as sibling.
    _write_accepted(
        work / "EN",
        candidates=[
            StockCandidate(
                candidate_id="en_only",
                provider="pexels",
                title="EN only",
                media_type="video",
                gap_id="Yellowstone_gap_001",
                local_media_path=str(media),
                media_validation_status=STATUS_EXPORT_READY,
                selected=True,
            )
        ],
    )

    siblings = iter_sibling_language_dirs(project)
    assert [("DE", de_dir)] == [(n, p) for n, p in siblings]

    with patch(
        "otio_app.services.without_voiceover_enhanced.cross_language_supplements.refresh_supplement_validation",
        side_effect=lambda c: c,
    ):
        ready = load_sibling_export_ready_supplements(project)
        assert [c.candidate_id for _, c in ready] == ["pexels_video_111"]

        rows = sibling_supplement_rows_for_cut_plan(
            project, folder_name="Yellowstone"
        )
    assert len(rows) == 1
    assert rows[0]["local_asset_id"] == "pexels_video_111"
    assert rows[0]["source"] == "cross_language_accepted_supplement"
    assert rows[0]["source_language"] == "DE"
    assert "accepted DE" in rows[0]["description"]

    # Other folder: no match
    with patch(
        "otio_app.services.without_voiceover_enhanced.cross_language_supplements.refresh_supplement_validation",
        side_effect=lambda c: c,
    ):
        assert sibling_supplement_rows_for_cut_plan(project, folder_name="Bisti") == []


def test_local_assets_payload_merges_sibling_and_dedupes(tmp_path: Path) -> None:
    project = _project(tmp_path, language="en")
    work = Path(project.work_dir)
    media = tmp_path / "herd.mp4"
    media.write_bytes(b"fake-video-bytes-long-enough")
    _write_accepted(
        work / "DE",
        candidates=[
            StockCandidate(
                candidate_id="pexels_video_222",
                provider="pexels",
                title="Herd",
                media_type="video",
                gap_id="Yellowstone_gap_009",
                local_media_path=str(media),
                media_validation_status=STATUS_EXPORT_READY,
                selected=True,
            )
        ],
    )
    inv_dir = work / "inventory"
    inv_dir.mkdir(parents=True)
    slim = {
        "assets": [
            {
                "id": "asset__yellowstone__local01__abcd1234",
                "file": "local01.mp4",
                "type": "video",
                "dauer_s": 10.0,
                "beschreibung": "Local YS clip",
            },
            {
                "id": "pexels_video_222",
                "file": "herd.mp4",
                "type": "video",
                "dauer_s": 8.0,
                "beschreibung": "Already in inventory",
            },
        ]
    }
    (inv_dir / "Yellowstone.slim.json").write_text(
        json.dumps(slim), encoding="utf-8"
    )

    with patch(
        "otio_app.services.without_voiceover_enhanced.cross_language_supplements.refresh_supplement_validation",
        side_effect=lambda c: c,
    ):
        assets = _local_assets_payload(project, folder_name="Yellowstone")
    ids = [a["local_asset_id"] for a in assets]
    assert "asset__yellowstone__local01__abcd1234" in ids
    # Deduped — already in slim inventory
    assert ids.count("pexels_video_222") == 1
