"""Tests für Shot-Timing und Outro-Aufteilung."""

from __future__ import annotations

from otio_app.services.duration_rules import split_total_duration


def test_split_total_duration_14_seconds() -> None:
    assert split_total_duration(14.0) == [7.0, 7.0]


def test_split_total_duration_5_seconds() -> None:
    assert split_total_duration(5.0) == [5.0]
