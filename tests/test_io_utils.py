"""JSON-Artefakte: Extra data nach parallelen Writes, atomares Speichern."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import BaseModel

from otio_app.services.without_voiceover_enhanced.io_utils import (
    load_model,
    read_json,
    write_json,
)
from otio_app.services.without_voiceover_enhanced.models import GapMergeReport


class _Tiny(BaseModel):
    schema_version: str = "v1"
    name: str = ""


def test_read_json_takes_first_object_on_extra_data(tmp_path: Path) -> None:
    path = tmp_path / "gap_merge_report.json"
    first = {"schema_version": "enhanced-gap-merge-v1", "message": "ok", "slots": []}
    second = {"schema_version": "enhanced-gap-merge-v1", "message": "dup"}
    path.write_text(
        json.dumps(first, indent=2) + "\n" + json.dumps(second, indent=2) + "\n",
        encoding="utf-8",
    )
    # Reproduces: Extra data: line N column 2
    try:
        json.loads(path.read_text(encoding="utf-8"))
        raise AssertionError("expected Extra data")
    except json.JSONDecodeError as exc:
        assert "Extra data" in exc.msg

    payload = read_json(path)
    assert payload["message"] == "ok"
    loaded = load_model(path, GapMergeReport)
    assert loaded is not None
    assert loaded.message == "ok"


def test_load_model_returns_none_on_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_model(path, _Tiny) is None


def test_write_json_concurrent_stays_single_object(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    errors: list[str] = []

    def worker(index: int) -> None:
        try:
            write_json(path, {"schema_version": "v1", "name": f"w{index}", "pad": "x" * 400})
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload["schema_version"] == "v1"
