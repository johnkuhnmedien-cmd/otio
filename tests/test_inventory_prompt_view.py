"""Slim-Inventory-Projektion für LLM / externe Nutzung."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.inventory_prompt_view import (
    build_slim_folder_inventory,
    load_slim_folder_inventory_file,
    slim_assets_for_cut_plan_prompt,
    slim_inventory_path_for,
)


def _folder() -> AssetFolderAnalysis:
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


def test_build_slim_filters_and_dedupes(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 12.5 if path.suffix == ".mp4" else None,
    )
    slim = build_slim_folder_inventory(_folder(), probe_duration=True)
    assert slim["kapitel"] == "Antelope Canyon"
    ids = [a["id"] for a in slim["assets"]]
    # Duplikat Asset01 / Asset01_3840x2160 → ein Eintrag (längere Beschreibung).
    assert ids == [
        "asset_antelope_canyon_asset01_3840x2160",
        "asset_manual_stock",
    ]
    assert slim["assets"][0]["file"] == "Antelope_Canyon_Asset01_3840x2160.mp4"
    assert slim["assets"][0]["type"] == "video"
    assert slim["assets"][0]["dauer_s"] == 12.5
    assert slim["assets"][0]["beschreibung"].startswith("Wellenförmige")
    assert slim["assets"][0]["motion"] == "tilt"
    assert slim["assets"][0]["framing"] == "wide"
    assert slim["assets"][0]["people"] is False
    assert slim["assets"][1]["type"] == "photo"
    assert slim["assets"][1]["dauer_s"] is None


def test_slim_cut_plan_payload_keeps_prompt_keys(monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 8.0,
    )
    rows = slim_assets_for_cut_plan_prompt(
        _folder(), folder_name="Antelope Canyon", probe_duration=True
    )
    assert rows[0]["local_asset_id"] == rows[0]["asset_id"]
    assert "path" not in rows[0]
    assert rows[0]["file"].endswith(".mp4")
    assert rows[0]["description"]
    assert rows[0]["duration_seconds"] == 8.0


def test_save_folder_inventory_writes_slim_sibling(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 3.0,
    )
    path = tmp_path / "inventory" / "Antelope_Canyon.json"
    save_folder_inventory(path, _folder())
    assert path.is_file()
    slim_path = slim_inventory_path_for(path)
    assert slim_path.is_file()
    payload = json.loads(slim_path.read_text(encoding="utf-8"))
    assert payload["kapitel"] == "Antelope Canyon"
    assert len(payload["assets"]) == 2


def test_slim_cut_plan_prefers_existing_slim_file(
    tmp_path: Path, monkeypatch
) -> None:
    """Vorhandene Slim-Datei gewinnt — kein Neubau / kein ffprobe."""
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
        _folder(),
        folder_name="Antelope Canyon",
        probe_duration=True,
        existing_slim_path=slim_path,
    )
    assert called["probe"] == 0
    assert len(rows) == 1
    assert rows[0]["local_asset_id"] == "asset_from_disk"
    assert rows[0]["duration_seconds"] == 42.0
    assert rows[0]["file"] == "from_disk.mp4"
