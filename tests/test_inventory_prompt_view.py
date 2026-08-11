"""Slim-Inventory-Projektion v2 für LLM / externe Nutzung."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import (
    AssetDefect,
    AssetFolderAnalysis,
    AssetFramingProfile,
    AssetLookProfile,
    AssetMediaAnalysis,
    AssetMotionProfile,
    AssetQualityProfile,
)
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.inventory_prompt_view import (
    SLIM_INVENTORY_SCHEMA_VERSION,
    build_slim_folder_inventory,
    load_slim_folder_inventory_file,
    slim_assets_for_cut_plan_prompt,
    slim_assets_from_slim_document,
    slim_inventory_path_for,
)
from otio_app.services.media_utils import NO_ANALYZABLE_MEDIA_DESCRIPTION


def _v3_asset(**overrides: object) -> AssetMediaAnalysis:
    base = AssetMediaAnalysis(
        path="/media/AdobeStock_544058849.mov",
        description=(
            "Luftaufnahme einer weitläufigen, hügeligen Landschaft mit einer "
            "markanten historischen Stadtmauer auf einem Bergrücken. Die Kamera "
            "schwenkt und zeigt das historische Dorf im Tal unter klarem Himmel."
        ),
        caption="Luftaufnahme einer historischen Bergstadt mit Wehrmauer.",
        content_tags=[
            "Bergstadt",
            "Wehrmauer",
            "Kirchturm",
            "Luftaufnahme",
            "Festung",
            "Tal",
            "Dorf",
            "Mauer",
        ],
        asset_id="asset_adobestock_544058849",
        media_type="video",
        duration_seconds=24.8334,
        usable_in_s=0.12,
        motion="drone",
        framing="aerial",
        people=False,
        analysis_schema_version="asset-analysis-v3",
        analysis_parse_ok=True,
        analysis_confidence=0.95,
        description_model="gemini-3.5-flash",
        description_model_resolved="gemini-3.5-flash",
        analysis_raw_response="should-not-appear",
        motion_profile=AssetMotionProfile(
            type="drone",
            intensity=35,
            direction="forward",
            confidence=0.9,
        ),
        framing_profile=AssetFramingProfile(type="aerial", shot_scale="wide"),
        quality_profile=AssetQualityProfile(
            technical_quality=82,
            composition_quality=85,
            visual_appeal=86,
            subject_clarity=88,
            hero_potential=85,
            defect_severity=0,
        ),
        look_profile=AssetLookProfile(
            brightness=65,
            contrast=70,
            saturation=60,
            color_temperature="warm",
            dominant_colors=[
                "Ziegelrot",
                "Beige",
                "Rotbraun",
                "Ocker",
                "beige",
            ],
        ),
        defect_items=[],
    )
    return base.model_copy(update=overrides)


def _folder_v3() -> AssetFolderAnalysis:
    return AssetFolderAnalysis(
        folder="Albarracín",
        description="Folder overview must not appear in slim v2.",
        media_files=["/media/AdobeStock_544058849.mov"],
        assets=[_v3_asset()],
    )


def _folder_legacy() -> AssetFolderAnalysis:
    return AssetFolderAnalysis(
        folder="Antelope Canyon",
        description="Slot canyon",
        media_files=[],
        assets=[
            AssetMediaAnalysis(
                path="/media/Antelope_Canyon_Asset01.mp4",
                description="Wellenförmige Sandsteinwände.",
                asset_id="asset_antelope_canyon_asset01",
                media_type="video",
                motion="static",
                framing="wide",
                people=False,
            ),
            AssetMediaAnalysis(
                path="/media/Antelope_Canyon_Asset01_3840x2160.mp4",
                description="Wellenförmige Sandsteinwände in höherer Auflösung.",
                asset_id="asset_antelope_canyon_asset01_3840x2160",
                media_type="video",
                motion="tilt",
                framing="wide",
                people=False,
            ),
            AssetMediaAnalysis(
                path="/media/no_desc.mp4",
                description="   ",
                asset_id="asset_no_desc",
                media_type="video",
            ),
            AssetMediaAnalysis(
                path="/media/manual_stock.jpeg",
                description="Close-up sandstone.",
                asset_id="asset_manual_stock",
                media_type="image",
            ),
        ],
    )


def test_slim_v2_schema_and_compact_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: (_ for _ in ()).throw(AssertionError("ffprobe not needed")),
    )
    slim = build_slim_folder_inventory(_folder_v3(), probe_duration=True)
    assert slim["schema_version"] == SLIM_INVENTORY_SCHEMA_VERSION == "asset-slim-v2"
    assert slim["chapter"] == "Albarracín"
    assert "hinweis" not in slim
    assert "kapitel" not in slim
    assert "description" not in slim

    asset = slim["assets"][0]
    assert asset["id"] == "asset_adobestock_544058849"
    assert asset["file"] == "AdobeStock_544058849.mov"
    assert asset["type"] == "video"
    assert asset["duration_s"] == 24.833
    assert asset["usable_in_s"] == 0.12
    assert asset["caption"].startswith("Luftaufnahme einer historischen Bergstadt")
    assert "weitläufigen" not in json.dumps(slim, ensure_ascii=False)
    assert len(asset["tags"]) == 6
    assert asset["motion"] == {
        "type": "drone",
        "intensity": 35,
        "direction": "forward",
    }
    assert "confidence" not in asset["motion"]
    assert asset["framing"] == {"type": "aerial", "scale": "wide"}
    assert asset["quality"] == {
        "technical": 82,
        "composition": 85,
        "appeal": 86,
        "clarity": 88,
        "hero": 85,
        "defect": 0,
    }
    assert asset["look"]["temperature"] == "warm"
    assert asset["look"]["colors"] == ["Ziegelrot", "Beige", "Rotbraun"]
    assert asset["people"] is False
    assert "analysis_confidence" not in asset
    assert "analysis_signature" not in asset
    assert "analysis_raw_response" not in asset
    assert "description_model" not in asset
    assert "path" not in asset
    dumped = json.dumps(slim, ensure_ascii=False)
    assert "/media/" not in dumped
    assert "should-not-appear" not in dumped
    assert "0.95" not in dumped


def test_slim_v2_omits_unknown_and_empty_profiles() -> None:
    asset = _v3_asset(
        motion_profile=AssetMotionProfile(
            type="unknown", intensity=None, direction="unknown"
        ),
        framing_profile=AssetFramingProfile(type="wide", shot_scale="unknown"),
        look_profile=AssetLookProfile(
            brightness=None,
            contrast=None,
            saturation=None,
            color_temperature="unknown",
            dominant_colors=[],
        ),
        quality_profile=None,
        content_tags=[],
        people=None,
        people_action=None,
        defect_items=[],
        defects=None,
        usable_in_s=None,
    )
    slim = build_slim_folder_inventory(
        AssetFolderAnalysis(folder="X", assets=[asset]),
        probe_duration=False,
    )
    entry = slim["assets"][0]
    assert "motion" not in entry
    assert entry["framing"] == {"type": "wide"}
    assert "look" not in entry
    assert "quality" not in entry
    assert "tags" not in entry
    assert "people" not in entry
    assert "defects" not in entry
    assert "usable_in_s" not in entry


def test_slim_v2_defect_note_clamped_and_structured() -> None:
    note = "x" * 200
    asset = _v3_asset(
        defect_items=[
            AssetDefect(type="blur", severity=40, note=note),
        ],
        quality_profile=AssetQualityProfile(
            technical_quality=70,
            composition_quality=70,
            visual_appeal=70,
            subject_clarity=70,
            hero_potential=60,
            defect_severity=40,
        ),
    )
    slim = build_slim_folder_inventory(
        AssetFolderAnalysis(folder="X", assets=[asset]),
        probe_duration=False,
    )
    defects = slim["assets"][0]["defects"]
    assert defects == [{"type": "blur", "severity": 40, "note": "x" * 120}]


def test_slim_v2_caption_fallback_truncates_legacy_description() -> None:
    long_desc = "A" * 250
    asset = AssetMediaAnalysis(
        path="/media/legacy.mp4",
        description=long_desc,
        caption="",
        asset_id="",
        media_type="video",
        duration_seconds=5.0,
        motion="pan",
        framing="medium",
    )
    slim = build_slim_folder_inventory(
        AssetFolderAnalysis(folder="Legacy", assets=[asset]),
        probe_duration=False,
    )
    entry = slim["assets"][0]
    assert entry["caption"] == "A" * 180
    assert entry["id"] == "asset_legacy"
    assert entry["motion"] == {"type": "pan"}
    assert entry["framing"] == {"type": "medium"}


def test_slim_v2_skips_unusable_and_no_analyzable() -> None:
    folder = AssetFolderAnalysis(
        folder="Skip",
        assets=[
            AssetMediaAnalysis(
                path="/media/empty.mp4",
                description="",
                caption="",
                media_type="video",
            ),
            AssetMediaAnalysis(
                path="/media/parse_fail.mp4",
                description="ok text",
                caption="ok caption",
                analysis_parse_ok=False,
                media_type="video",
            ),
            AssetMediaAnalysis(
                path="/media/error.mp4",
                description="ok",
                caption="ok",
                error="boom",
                media_type="video",
            ),
            AssetMediaAnalysis(
                path="/media/no_media.mp4",
                description=NO_ANALYZABLE_MEDIA_DESCRIPTION,
                caption="",
                media_type="video",
            ),
            AssetMediaAnalysis(
                path="/media/caption_only.mp4",
                description="",
                caption="Nur Caption reicht.",
                media_type="video",
                duration_seconds=1.5,
            ),
        ],
    )
    slim = build_slim_folder_inventory(folder, probe_duration=False)
    assert [a["file"] for a in slim["assets"]] == ["caption_only.mp4"]


def test_slim_v1_document_still_loads_and_maps_prompt_rows() -> None:
    slim = {
        "kapitel": "Achill Island",
        "hinweis": "legacy",
        "assets": [
            {
                "id": "photo_a",
                "file": "a.jpg",
                "type": "photo",
                "dauer_s": None,
                "beschreibung": "Deserted Village still",
                "people": False,
            },
            {
                "id": "video_b",
                "file": "b.mp4",
                "type": "video",
                "dauer_s": 12.0,
                "beschreibung": "Ruinen eines verlassenen Dorfes",
                "motion": "pan",
                "framing": "wide",
            },
        ],
    }
    assert load_slim_folder_inventory_file  # import sanity
    rows = slim_assets_from_slim_document(slim, folder_name="Achill Island")
    assert [r["local_asset_id"] for r in rows] == ["video_b", "photo_a"]
    assert rows[0]["description"] == "Ruinen eines verlassenen Dorfes"
    assert rows[0]["duration_seconds"] == 12.0
    assert rows[0]["motion"] == "pan"
    assert rows[0]["framing"] == "wide"
    assert "quality" not in rows[0]
    assert "tags" not in rows[0]


def test_unknown_slim_schema_version_rejected(tmp_path: Path) -> None:
    path = tmp_path / "X.slim.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "asset-slim-v99",
                "chapter": "X",
                "assets": [
                    {
                        "id": "a",
                        "file": "a.mp4",
                        "type": "video",
                        "duration_s": 1.0,
                        "caption": "x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_slim_folder_inventory_file(path) is None


def test_slim_v2_prompt_row_keeps_phase2a_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: (_ for _ in ()).throw(AssertionError("no probe")),
    )
    rows = slim_assets_for_cut_plan_prompt(
        _folder_v3(), folder_name="Albarracín", probe_duration=False
    )
    row = rows[0]
    assert row["local_asset_id"] == "asset_adobestock_544058849"
    assert row["asset_id"] == "asset_adobestock_544058849"
    assert row["folder"] == "Albarracín"
    assert row["file"] == "AdobeStock_544058849.mov"
    assert row["duration_seconds"] == 24.833
    assert row["media_type"] == "video"
    assert row["description"] == "Luftaufnahme einer historischen Bergstadt mit Wehrmauer."
    assert row["motion"] == "drone"
    assert row["framing"] == "aerial"
    assert row["people"] is False
    assert row["usable_in_s"] == 0.12
    assert "tags" not in row
    assert "quality" not in row
    assert "look" not in row
    assert "scale" not in row
    assert "path" not in row


def test_slim_v2_prompt_row_summarizes_structured_defects() -> None:
    asset = _v3_asset(
        defect_items=[AssetDefect(type="blur", severity=40, note="soft focus")]
    )
    slim = build_slim_folder_inventory(
        AssetFolderAnalysis(folder="X", assets=[asset]),
        probe_duration=False,
    )
    rows = slim_assets_from_slim_document(slim, folder_name="X")
    assert rows[0]["defects"] == "blur: soft focus"


def test_slim_dedupe_prefers_video_over_longer_photo_description(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 10.0 if path.suffix == ".mp4" else None,
    )
    folder = AssetFolderAnalysis(
        folder="Achill Island",
        description="Achill",
        media_files=[],
        assets=[
            AssetMediaAnalysis(
                path="/media/sheep_cliff.jpg",
                description=(
                    "A verified Achill Island landscape where sheep are visibly "
                    "grazing across wet or heath-like blanket bog, with the habitat "
                    "readable as an extensive open terrain."
                ),
                asset_id="photo_sheep",
                media_type="image",
            ),
            AssetMediaAnalysis(
                path="/media/sheep_cliff.mp4",
                description="Markierte Schafe auf einer Klippe an der Küste.",
                asset_id="video_sheep",
                media_type="video",
            ),
        ],
    )
    slim = build_slim_folder_inventory(folder, probe_duration=True)
    assert [a["id"] for a in slim["assets"]] == ["video_sheep"]
    assert slim["assets"][0]["type"] == "video"


def test_build_slim_filters_and_dedupes_legacy(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 12.5 if path.suffix == ".mp4" else None,
    )
    slim = build_slim_folder_inventory(_folder_legacy(), probe_duration=True)
    assert slim["schema_version"] == "asset-slim-v2"
    assert slim["chapter"] == "Antelope Canyon"
    ids = [a["id"] for a in slim["assets"]]
    assert ids == [
        "asset_antelope_canyon_asset01_3840x2160",
        "asset_manual_stock",
    ]
    assert slim["assets"][0]["file"] == "Antelope_Canyon_Asset01_3840x2160.mp4"
    assert slim["assets"][0]["duration_s"] == 12.5
    assert slim["assets"][0]["caption"].startswith("Wellenförmige")
    assert slim["assets"][0]["motion"] == {"type": "tilt"}
    assert slim["assets"][0]["framing"] == {"type": "wide"}
    assert slim["assets"][0]["people"] is False
    assert slim["assets"][1]["type"] == "photo"
    assert slim["assets"][1]["duration_s"] is None


def test_empty_canonical_asset_id_gets_stable_derived_id() -> None:
    asset = AssetMediaAnalysis(
        path="/media/Castle Combe/Asset00003.mov",
        description="Fassade mit Blauregen.",
        caption="",
        asset_id="",
        media_type="video",
        duration_seconds=9.0,
    )
    slim = build_slim_folder_inventory(
        AssetFolderAnalysis(folder="Castle Combe", assets=[asset]),
        probe_duration=False,
    )
    assert slim["assets"][0]["id"] == "asset_asset00003"


def test_save_folder_inventory_writes_slim_v2_sibling(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 3.0,
    )
    path = tmp_path / "inventory" / "Antelope_Canyon.json"
    save_folder_inventory(path, _folder_legacy())
    assert path.is_file()
    slim_path = slim_inventory_path_for(path)
    assert slim_path.is_file()
    payload = json.loads(slim_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "asset-slim-v2"
    assert payload["chapter"] == "Antelope Canyon"
    assert len(payload["assets"]) == 2


def test_slim_cut_plan_prefers_existing_v1_slim_file(
    tmp_path: Path, monkeypatch
) -> None:
    called = {"probe": 0}

    def _probe(_path):
        called["probe"] += 1
        return 99.0

    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        _probe,
    )
    slim_path = tmp_path / "Antelope_Canyon.slim.json"
    slim_path.write_text(
        json.dumps(
            {
                "kapitel": "Antelope Canyon",
                "assets": [
                    {
                        "id": "asset_from_disk",
                        "file": "from_disk.mp4",
                        "type": "video",
                        "dauer_s": 42.0,
                        "beschreibung": "Aus vorhandener Slim-Datei.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_slim_folder_inventory_file(slim_path)
    assert loaded is not None
    rows = slim_assets_for_cut_plan_prompt(
        _folder_legacy(),
        folder_name="Antelope Canyon",
        probe_duration=True,
        existing_slim_path=slim_path,
    )
    assert called["probe"] == 0
    assert len(rows) == 1
    assert rows[0]["local_asset_id"] == "asset_from_disk"
    assert rows[0]["duration_seconds"] == 42.0
    assert rows[0]["file"] == "from_disk.mp4"


def test_slim_cut_plan_prefers_existing_v2_slim_file(
    tmp_path: Path, monkeypatch
) -> None:
    called = {"probe": 0}

    def _probe(_path):
        called["probe"] += 1
        return 99.0

    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        _probe,
    )
    slim_path = tmp_path / "Albarracin.slim.json"
    slim_path.write_text(
        json.dumps(
            {
                "schema_version": "asset-slim-v2",
                "chapter": "Albarracín",
                "assets": [
                    {
                        "id": "asset_disk_v2",
                        "file": "disk_v2.mov",
                        "type": "video",
                        "duration_s": 11.5,
                        "caption": "Caption von Disk.",
                        "tags": ["Mauer", "Dorf"],
                        "motion": {"type": "drone", "intensity": 20},
                        "framing": {"type": "aerial", "scale": "extreme_wide"},
                        "quality": {"hero": 90},
                        "look": {"temperature": "warm"},
                        "people": False,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = slim_assets_for_cut_plan_prompt(
        _folder_v3(),
        folder_name="Albarracín",
        probe_duration=True,
        existing_slim_path=slim_path,
    )
    assert called["probe"] == 0
    assert rows[0]["local_asset_id"] == "asset_disk_v2"
    assert rows[0]["description"] == "Caption von Disk."
    assert rows[0]["motion"] == "drone"
    assert rows[0]["framing"] == "aerial"
    assert "tags" not in rows[0]
    assert "quality" not in rows[0]
    assert "look" not in rows[0]


def test_canonical_inventory_not_mutated_by_slim_build() -> None:
    folder = _folder_v3()
    before = folder.model_dump(mode="json")
    build_slim_folder_inventory(folder, probe_duration=False)
    assert folder.model_dump(mode="json") == before


def test_slim_v2_is_much_smaller_than_canonical() -> None:
    asset = _v3_asset(
        frames_used=[
            "/work/frames/a/frame_001.jpg",
            "/work/frames/a/frame_002.jpg",
            "/work/frames/a/frame_003.jpg",
        ],
        analysis_raw_response="{" + ("x" * 400) + "}",
    )
    folder = AssetFolderAnalysis(
        folder="Albarracín",
        description="Folder overview " + ("y" * 200),
        media_files=[asset.path],
        frames_used=list(asset.frames_used),
        assets=[asset],
    )
    canonical = folder.model_dump(mode="json")
    slim = build_slim_folder_inventory(folder, probe_duration=False)
    canonical_bytes = len(json.dumps(canonical, ensure_ascii=False))
    slim_bytes = len(json.dumps(slim, ensure_ascii=False))
    assert slim_bytes < canonical_bytes * 0.55
    assert asset.description not in json.dumps(slim, ensure_ascii=False)
    assert asset.caption in slim["assets"][0]["caption"]


def test_roundtrip_write_load_v2(tmp_path: Path) -> None:
    path = tmp_path / "inventory" / "Albarracin.json"
    from otio_app.services.inventory_prompt_view import write_slim_folder_inventory

    slim_path = write_slim_folder_inventory(
        path, _folder_v3(), probe_duration=False
    )
    loaded = load_slim_folder_inventory_file(slim_path)
    assert loaded is not None
    assert loaded["schema_version"] == "asset-slim-v2"
    rows = slim_assets_from_slim_document(loaded, folder_name="Albarracín")
    assert rows[0]["asset_id"] == "asset_adobestock_544058849"
    assert rows[0]["description"].startswith("Luftaufnahme")
