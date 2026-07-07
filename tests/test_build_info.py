"""Tests für Build-/Git-Anzeige."""

from otio_app.build_info import expected_feature_markers, format_build_label


def test_format_build_label_includes_version() -> None:
    label = format_build_label()
    assert label.startswith("v0.1.0")


def test_expected_feature_markers_not_empty() -> None:
    markers = expected_feature_markers()
    assert len(markers) >= 3
    assert any("Hintergrund" in item for item in markers)
