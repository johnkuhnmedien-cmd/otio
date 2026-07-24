"""Research-Excel-Parser + Dateinamen für Adobe-Stock-Import."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.services.adobe_research_import import (
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
