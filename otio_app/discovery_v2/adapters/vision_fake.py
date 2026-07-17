"""Deterministic offline fake vision adapter for Discovery V2 Phase 8C."""

from __future__ import annotations

from collections.abc import Callable

from otio_app.discovery_v2.domain.visual_observation import VisionGatewayRequest


class FakeVisionTransientError(RuntimeError):
    """Synthetic transient failure used by gateway retry tests."""


FakeVisionHook = Callable[[VisionGatewayRequest], dict | Exception | None]

_TEST_HOOK: FakeVisionHook | None = None


def set_fake_vision_test_hook(hook: FakeVisionHook | None) -> None:
    global _TEST_HOOK
    _TEST_HOOK = hook


def reset_fake_vision_test_hook() -> None:
    set_fake_vision_test_hook(None)


class FakeVisionAdapter:
    """Offline adapter that returns untrusted dicts for gateway validation."""

    def analyze(self, request: VisionGatewayRequest) -> dict:
        if _TEST_HOOK is not None:
            hooked = _TEST_HOOK(request)
            if isinstance(hooked, Exception):
                raise hooked
            if hooked is not None:
                return hooked

        frame_ids = [frame.frame_id for frame in sorted(request.frames, key=lambda f: f.ordinal)]
        frame_count = len(frame_ids)
        hash_hint = ""
        if request.frames:
            hash_hint = sorted(frame.frame_sha256 for frame in request.frames)[0][:12]
        media_hint = (request.media_kind or "unknown").strip().lower() or "unknown"
        return {
            "summary": (
                f"Lokale Fake-Vision-Beobachtung fuer {frame_count} "
                f"Analysis-Frame(s) ({media_hint})."
            ),
            "visible_subjects": ["analysis frames"] if frame_count else [],
            "actions": [],
            "setting": None,
            "indoor_outdoor": "unknown",
            "day_night": "unknown",
            "people_present": None,
            "crowd_level": "unknown",
            "camera_scale": "unknown",
            "camera_motion_hint": "unknown",
            "visual_quality_notes": [
                f"fake_adapter_frame_count={frame_count}",
                f"fake_adapter_hash_hint={hash_hint}" if hash_hint else "fake_adapter_hash_hint=none",
            ],
            "readable_text_present": None,
            "readable_text_summary": None,
            "possible_location_clues": [],
            "geographic_confidence": 0.0,
            "landmark_candidates": [],
            "weather_visible": None,
            "safety_or_sensitive_content": [],
            "possible_synthetic_indicators": [],
            "synthetic_confidence": 0.0,
            "uncertainty_notes": [
                "Fake adapter: deterministic local simulation, not a semantic model."
            ],
            "evidence_frame_ids": frame_ids,
            "editorial_signals": [],
        }


__all__ = [
    "FakeVisionAdapter",
    "FakeVisionTransientError",
    "reset_fake_vision_test_hook",
    "set_fake_vision_test_hook",
]
