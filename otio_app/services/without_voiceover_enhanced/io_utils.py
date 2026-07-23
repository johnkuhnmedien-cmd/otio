"""Kleine JSON-I/O-Helfer für Enhanced-Artefakte."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def write_json(path: Path, payload: dict[str, Any] | BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_model(path: Path, model_type: type[T]) -> T | None:
    if not path.is_file():
        return None
    return model_type.model_validate(read_json(path))
