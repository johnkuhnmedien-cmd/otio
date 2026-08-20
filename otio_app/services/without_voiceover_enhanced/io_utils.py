"""Kleine JSON-I/O-Helfer für Enhanced-Artefakte."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def write_json(path: Path, payload: dict[str, Any] | BaseModel) -> Path:
    """Schreibt JSON atomar (tmp + replace), damit parallele Kapitel-Writes nicht concatenieren."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
        tmp.replace(path)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def read_json(path: Path) -> dict[str, Any]:
    """Liest ein JSON-Objekt. Bei ``Extra data`` (zwei Objekte hintereinander) das erste."""
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        if "Extra data" not in (exc.msg or ""):
            raise
        payload, _end = json.JSONDecoder().raw_decode(text.lstrip())
    if not isinstance(payload, dict):
        raise ValueError(f"JSON-Wurzel ist kein Objekt: {path}")
    return payload


def load_model(path: Path, model_type: type[T]) -> T | None:
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return model_type.model_validate(payload)
