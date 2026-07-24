"""Research-Excel-Parser + Dateinamen für Adobe-Stock-Import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.services.adobe_research_import import (
    STATUS_DOWNLOADED,
    STATUS_OPEN,
    build_research_import_board,
    download_research_import,
    format_asset_stem,
    parse_research_excel,
    sanitize_folder_name,
)

IRELAND_XLSX = (
    Path(__file__).resolve().parent / "fixtures" / "research_template_ireland.xlsx"
)


def test_sanitize_folder_name_keeps_readable_title() -> None:
    assert sanitize_folder_name("Dublin") == "Dublin"
    assert "Giant" in sanitize_folder_name("Giant’s Causeway & Causeway Coast")
    assert "/" not in sanitize_folder_name("A/B:C")


def test_format_asset_stem() -> None:
    assert format_asset_stem("Dublin", 1) == "Dublin_Asset_01"
    assert format_asset_stem("Dublin", 12) == "Dublin_Asset_12"


@pytest.mark.skipif(not IRELAND_XLSX.is_file(), reason="Ireland research Excel fehlt")
def test_parse_ireland_research_excel() -> None:
    plan = parse_research_excel(IRELAND_XLSX)
    assert plan.chapter_count >= 8
    assert plan.asset_count >= 100
    titles = [ch.title for ch in plan.chapters]
    assert "Dublin" in titles
    assert "Skellig Michael" in titles
    dublin = next(ch for ch in plan.chapters if ch.title == "Dublin")
    assert dublin.folder_name == "Dublin"
    assert dublin.asset_count >= 20
    assert dublin.assets[0].asset_id.isdigit()
    assert "stock.adobe.com" in dublin.assets[0].link
    # Non-numeric note in ID column must be skipped (Dingle row anomaly).
    dingle = next(ch for ch in plan.chapters if "Dingle" in ch.title)
    assert all(a.asset_id.isdigit() for a in dingle.assets)


def test_parse_minimal_workbook(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Alpha"
    ws["D1"] = "Beta"
    ws["A2"] = "Count"
    ws["B2"] = "Asset ID"
    ws["C2"] = "Link"
    ws["D2"] = "Count"
    ws["E2"] = "Asset ID"
    ws["F2"] = "Link"
    ws["A3"] = 1
    ws["B3"] = 123456
    ws["C3"] = "https://stock.adobe.com/ph/video/x/123456"
    ws["D3"] = 1
    ws["E3"] = 987654
    ws["F3"] = "https://stock.adobe.com/ph/images/y/987654"
    path = tmp_path / "mini.xlsx"
    wb.save(path)

    plan = parse_research_excel(path)
    assert plan.chapter_count == 2
    assert plan.chapters[0].title == "Alpha"
    assert plan.chapters[0].assets[0].asset_id == "123456"
    assert plan.chapters[0].assets[0].media_hint == "video"
    assert plan.chapters[1].assets[0].media_hint == "image"


def _minimal_plan(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Alpha"
    ws["A2"] = "Count"
    ws["B2"] = "Asset ID"
    ws["C2"] = "Link"
    ws["A3"] = 1
    ws["B3"] = 111
    ws["C3"] = "https://stock.adobe.com/ph/video/x/111"
    ws["A4"] = 2
    ws["B4"] = 222
    ws["C4"] = "https://stock.adobe.com/ph/video/x/222"
    path = tmp_path / "board.xlsx"
    wb.save(path)
    return parse_research_excel(path)


def test_build_research_import_board_marks_downloaded_from_sidecar(tmp_path: Path) -> None:
    plan = _minimal_plan(tmp_path)
    target = tmp_path / "out"
    chapter_dir = target / "Alpha"
    chapter_dir.mkdir(parents=True)
    sidecar = chapter_dir / "Alpha_Asset_01.adobe.json"
    sidecar.write_text(
        json.dumps(
            {
                "asset_id": "111",
                "license": "Video_HD",
                "local_path": str(chapter_dir / "Alpha_Asset_01.mp4"),
            }
        ),
        encoding="utf-8",
    )

    board = build_research_import_board(plan, target)
    assert board.total == 2
    assert board.downloaded == 1
    assert board.open_count == 1
    by_id = {a.asset_id: a for ch in board.chapters for a in ch.assets}
    assert by_id["111"].status == STATUS_DOWNLOADED
    assert by_id["222"].status == STATUS_OPEN


def test_download_respects_should_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _minimal_plan(tmp_path)
    target = tmp_path / "dl"
    calls = {"n": 0}

    def fake_license_and_download(adapter, *, content_id, media_type, destination):
        calls["n"] += 1
        path = destination.with_suffix(".mp4")
        path.write_bytes(b"x" * 200_000)
        return path, "Video_HD"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import._license_and_download_to_path",
        fake_license_and_download,
    )
    monkeypatch.setattr(
        "otio_app.services.adobe_research_import._infer_media_type",
        lambda *_a, **_k: "video",
    )

    class _Ready:
        acquire_enabled = True
        message = "ok"

    monkeypatch.setattr(
        "otio_app.services.adobe_research_import.AdobeStockAdapter.readiness",
        lambda self: _Ready(),
    )

    stop_after_first = {"stop": False}

    def should_stop() -> bool:
        return stop_after_first["stop"]

    progress_events = []

    def on_progress(event) -> None:
        progress_events.append(event)
        if event.done >= 1 and event.status == "downloaded":
            stop_after_first["stop"] = True

    result = download_research_import(
        plan,
        target,
        skip_existing_ids=False,
        progress_callback=on_progress,
        should_stop=should_stop,
    )
    assert result.cancelled is True
    assert result.downloaded == 1
    assert calls["n"] == 1
    assert any(e.status == "cancelled" for e in progress_events)
    board = build_research_import_board(plan, target)
    assert board.downloaded == 1
    assert board.open_count >= 1
