"""Phase 11.3 (Nachbesserung): kombinierter Gemini-Aufruf
describe_and_validate_supplement_asset — beschreibt Frames UND validiert
sie gegen einen Bedarf in EINEM Request statt zwei getrennten Aufrufen."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from otio_app.services.gemini_client import describe_and_validate_supplement_asset

_MODULE = "otio_app.services.gemini_client"


def _fake_client(response_text: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.text = response_text
    client.models.generate_content.return_value = response
    return client


def _write_fake_frame(tmp_path: Path, name: str = "frame_001.jpg") -> Path:
    path = tmp_path / name
    path.write_bytes(b"FAKE_JPEG_BYTES")
    return path


def test_returns_fail_when_no_frames_without_calling_gemini() -> None:
    with patch(f"{_MODULE}._get_client") as mock_get_client:
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[],
            passage_text="Satz",
            visual_requirement="Anforderung",
        )
    assert result["status"] == "FAIL"
    assert result["description"] == ""
    mock_get_client.assert_not_called()


def test_parses_valid_json_response(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    payload = json.dumps(
        {"description": "Ein Wasserfall mit einer Person.", "status": "PASS", "score": 0.92, "reason": "Passt."}
    )
    with patch(f"{_MODULE}._get_client", return_value=_fake_client(payload)) as mock_get_client:
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
            visual_requirement="Wasserfall, Person spuert die Kuehle",
            model="gemini-3-flash-preview",
        )

    assert result == {
        "description": "Ein Wasserfall mit einer Person.",
        "status": "PASS",
        "score": 0.92,
        "reason": "Passt.",
    }
    mock_get_client.assert_called_once()


def test_normalizes_invalid_status_to_needs_user_review(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    payload = json.dumps({"description": "x", "status": "MAYBE", "score": 0.5, "reason": "unklar"})
    with patch(f"{_MODULE}._get_client", return_value=_fake_client(payload)):
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Satz",
            visual_requirement="Anforderung",
        )
    assert result["status"] == "NEEDS_USER_REVIEW"


def test_unparseable_response_returns_needs_user_review(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    with patch(f"{_MODULE}._get_client", return_value=_fake_client("not json at all")):
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Satz",
            visual_requirement="Anforderung",
        )
    assert result["status"] == "NEEDS_USER_REVIEW"
    assert result["description"] == ""


def test_score_out_of_range_is_clamped(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    payload = json.dumps({"description": "x", "status": "PASS", "score": 1.9, "reason": "y"})
    with patch(f"{_MODULE}._get_client", return_value=_fake_client(payload)):
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Satz",
            visual_requirement="Anforderung",
        )
    assert result["score"] == 1.0


def test_invalid_score_defaults_to_half(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    payload = json.dumps({"description": "x", "status": "PASS", "score": "not-a-number", "reason": "y"})
    with patch(f"{_MODULE}._get_client", return_value=_fake_client(payload)):
        result = describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Satz",
            visual_requirement="Anforderung",
        )
    assert result["score"] == 0.5


def test_passes_must_show_and_avoid_showing_into_prompt(tmp_path: Path) -> None:
    frame = _write_fake_frame(tmp_path)
    client = _fake_client(json.dumps({"description": "x", "status": "PASS", "score": 0.9, "reason": "y"}))
    with patch(f"{_MODULE}._get_client", return_value=client):
        describe_and_validate_supplement_asset(
            media_name="clip.mp4",
            folder_name="Havasu Falls",
            frame_paths=[frame],
            passage_text="Satz",
            visual_requirement="Anforderung",
            must_show=["waterfall", "person"],
            avoid_showing=["snow"],
        )

    call_kwargs = client.models.generate_content.call_args.kwargs
    prompt_text = call_kwargs["contents"][0].parts[0].text
    assert "waterfall, person" in prompt_text
    assert "snow" in prompt_text
