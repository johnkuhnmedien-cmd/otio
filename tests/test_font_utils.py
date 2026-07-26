"""Font-Auflösung für Ordner-Titel (Phosphate Solid / TTC-Faces)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.services.font_utils import (
    FOLDER_TITLE_FONT_OPTIONS,
    _is_plausible_match,
    resolve_font_face_index,
    resolve_font_path,
)


def test_folder_title_font_options_include_phosphate_solid() -> None:
    assert "Phosphate" in FOLDER_TITLE_FONT_OPTIONS
    assert "Phosphate Solid" in FOLDER_TITLE_FONT_OPTIONS
    assert FOLDER_TITLE_FONT_OPTIONS.index("Phosphate Solid") == (
        FOLDER_TITLE_FONT_OPTIONS.index("Phosphate") + 1
    )


def test_plausible_match_accepts_phosphate_ttc_for_solid() -> None:
    path = Path("/System/Library/Fonts/Supplemental/Phosphate.ttc")
    assert _is_plausible_match(
        "Phosphate Solid",
        path=path,
        family="Phosphate",
        style="Solid",
        fullname="Phosphate Solid",
    )
    assert not _is_plausible_match(
        "Phosphate Solid",
        path=Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        family="Noto Sans",
        style="Regular",
        fullname="Noto Sans Regular",
    )


def test_resolve_font_path_uses_fontconfig_index_for_solid(tmp_path: Path) -> None:
    fake_ttc = tmp_path / "Phosphate.ttc"
    fake_ttc.write_bytes(b"ttc")
    fc_line = f"{fake_ttc}\t1\tPhosphate\tSolid\tPhosphate Solid"
    with patch("otio_app.services.font_utils.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = fc_line
        path = resolve_font_path("Phosphate Solid")
        assert path == fake_ttc
        assert resolve_font_face_index("Phosphate Solid", fake_ttc) == 1


def test_resolve_font_path_directory_fallback_matches_family_stem(
    tmp_path: Path, monkeypatch
) -> None:
    fonts_dir = tmp_path / "Fonts"
    fonts_dir.mkdir()
    ttc = fonts_dir / "Phosphate.ttc"
    ttc.write_bytes(b"ttc")
    monkeypatch.setattr(
        "otio_app.services.font_utils._FONT_SEARCH_DIRS",
        (fonts_dir,),
    )
    with patch(
        "otio_app.services.font_utils._resolve_via_fontconfig",
        return_value=None,
    ), patch(
        "otio_app.services.font_utils.resolve_font_face_index",
        return_value=1,
    ):
        assert resolve_font_path("Phosphate Solid") == ttc
