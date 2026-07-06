"""Schriftarten für ffmpeg drawtext auflösen."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FOLDER_TITLE_FONT_OPTIONS: tuple[str, ...] = (
    "Phosphate",
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

_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc", ".ttf", ".otc"})


def resolve_font_path(font_name: str) -> Path | None:
    """Sucht eine Schriftdatei per fc-match oder in Standard-Ordnern."""
    clean_name = font_name.strip()
    if not clean_name:
        return None

    via_fc = _resolve_via_fontconfig(clean_name)
    if via_fc is not None:
        return via_fc

    tokens = [token for token in re.split(r"\s+", clean_name) if token]
    name_key = clean_name.casefold().replace(" ", "")
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
            stem_key = candidate.stem.casefold().replace(" ", "").replace("-", "")
            if name_key in stem_key or all(token.casefold() in candidate.name.casefold() for token in tokens):
                return candidate
    return None


def _resolve_via_fontconfig(font_name: str) -> Path | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", font_name],
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
    path = Path(raw)
    if not path.is_file():
        return None
    name_key = font_name.casefold().replace(" ", "").replace("-", "")
    stem_key = path.stem.casefold().replace(" ", "").replace("-", "")
    if name_key not in stem_key:
        return None
    return path


def resolve_font_with_fallback(
    requested_font: str,
    *,
    fallback_font: str = OPENING_TITLE_FALLBACK_FONT,
) -> tuple[Path | None, str, bool]:
    """Liefert (font_path, resolved_name, fallback_used)."""
    requested = requested_font.strip() or "Phosphate"
    path = resolve_font_path(requested)
    if path is not None:
        return path, requested, False
    fallback = fallback_font.strip() or OPENING_TITLE_FALLBACK_FONT
    fallback_path = resolve_font_path(fallback)
    if fallback_path is not None:
        return fallback_path, fallback, True
    return None, fallback, True
