"""Schriftarten für ffmpeg drawtext auflösen."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FOLDER_TITLE_FONT_OPTIONS: tuple[str, ...] = (
    "Phosphate",
    "Phosphate Solid",
    "Arial Bold",
    "Helvetica Neue",
    "Helvetica Neue Condensed Bold",
    "Impact",
    "Futura",
)

OPENING_TITLE_FALLBACK_FONT = "Helvetica Neue Condensed Bold"

_FONT_SEARCH_DIRS: tuple[Path, ...] = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
)

_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc", ".otc"})


def _compact(value: str) -> str:
    return (value or "").casefold().replace(" ", "").replace("-", "").replace("_", "")


def _name_tokens(font_name: str) -> list[str]:
    return [token for token in re.split(r"[\s_-]+", font_name.strip()) if token]


def _is_plausible_match(
    font_name: str,
    *,
    path: Path,
    family: str = "",
    style: str = "",
    fullname: str = "",
) -> bool:
    """Prüft, ob ein Treffer wirklich zur angefragten Schrift passt (nicht nur fc-Fallback)."""
    tokens = _name_tokens(font_name)
    if not tokens:
        return False
    haystack = " ".join(
        part
        for part in (family, style, fullname, path.stem, path.name)
        if part
    ).casefold()
    if all(token.casefold() in haystack for token in tokens):
        return True
    name_key = _compact(font_name)
    stem_key = _compact(path.stem)
    # TTC-Familien: "Phosphate Solid" → Phosphate.ttc
    if stem_key and (stem_key in name_key or name_key in stem_key):
        return True
    return False


def resolve_font_path(font_name: str) -> Path | None:
    """Sucht eine Schriftdatei per fc-match oder in Standard-Ordnern."""
    hit = _resolve_font_hit(font_name)
    return hit[0] if hit is not None else None


def resolve_font_face_index(font_name: str, font_path: Path | None) -> int:
    """Face-Index in TTC/OTC-Collections (z. B. Phosphate Solid ≠ Inline)."""
    if font_path is None:
        return 0
    if font_path.suffix.lower() not in {".ttc", ".otc"}:
        return 0
    via_fc = _face_index_via_fontconfig(font_name)
    if via_fc is not None:
        return via_fc
    return 0


def _resolve_font_hit(font_name: str) -> tuple[Path, int] | None:
    clean_name = font_name.strip()
    if not clean_name:
        return None

    via_fc = _resolve_via_fontconfig(clean_name)
    if via_fc is not None:
        return via_fc

    tokens = _name_tokens(clean_name)
    name_key = _compact(clean_name)
    for directory in _FONT_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        try:
            candidates = [
                path
                for path in directory.rglob("*")
                if path.suffix.lower() in _FONT_EXTENSIONS and path.is_file()
            ]
        except OSError:
            continue
        for candidate in candidates:
            stem_key = _compact(candidate.stem)
            token_hit = all(
                token.casefold() in candidate.name.casefold() for token in tokens
            )
            family_hit = bool(stem_key) and (
                name_key in stem_key or stem_key in name_key
            )
            if token_hit or family_hit:
                index = resolve_font_face_index(clean_name, candidate)
                return candidate, index
    return None


def _resolve_via_fontconfig(font_name: str) -> tuple[Path, int] | None:
    try:
        result = subprocess.run(
            [
                "fc-match",
                "-f",
                "%{file}\t%{index}\t%{family}\t%{style}\t%{fullname}",
                font_name,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    parts = raw.split("\t")
    while len(parts) < 5:
        parts.append("")
    file_raw, index_raw, family, style, fullname = parts[:5]
    path = Path(file_raw.strip())
    if not path.is_file():
        return None
    if not _is_plausible_match(
        font_name,
        path=path,
        family=family,
        style=style,
        fullname=fullname,
    ):
        return None
    try:
        face_index = max(0, int(str(index_raw).strip() or "0"))
    except ValueError:
        face_index = 0
    return path, face_index


def _face_index_via_fontconfig(font_name: str) -> int | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}\t%{index}\t%{family}\t%{style}\t%{fullname}", font_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    parts = raw.split("\t")
    while len(parts) < 5:
        parts.append("")
    file_raw, index_raw, family, style, fullname = parts[:5]
    path = Path(file_raw.strip())
    if not path.is_file():
        return None
    if not _is_plausible_match(
        font_name,
        path=path,
        family=family,
        style=style,
        fullname=fullname,
    ):
        return None
    try:
        return max(0, int(str(index_raw).strip() or "0"))
    except ValueError:
        return 0


def resolve_font_with_fallback(
    requested_font: str,
    *,
    fallback_font: str = OPENING_TITLE_FALLBACK_FONT,
) -> tuple[Path | None, str, bool]:
    """Liefert (font_path, resolved_name, fallback_used)."""
    path, resolved_name, fallback_used, _face_index = resolve_font_with_fallback_details(
        requested_font,
        fallback_font=fallback_font,
    )
    return path, resolved_name, fallback_used


def resolve_font_with_fallback_details(
    requested_font: str,
    *,
    fallback_font: str = OPENING_TITLE_FALLBACK_FONT,
) -> tuple[Path | None, str, bool, int]:
    """Liefert (font_path, resolved_name, fallback_used, face_index)."""
    requested = requested_font.strip() or "Phosphate"
    hit = _resolve_font_hit(requested)
    if hit is not None:
        return hit[0], requested, False, int(hit[1])
    fallback = fallback_font.strip() or OPENING_TITLE_FALLBACK_FONT
    fallback_hit = _resolve_font_hit(fallback)
    if fallback_hit is not None:
        return fallback_hit[0], fallback, True, int(fallback_hit[1])
    return None, fallback, True, 0
