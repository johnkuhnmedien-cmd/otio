"""UI-only flash messages that survive exactly one controlled st.rerun()."""

from __future__ import annotations

from typing import Literal

import streamlit as st

FlashLevel = Literal["success", "warning", "error", "info"]

FLASH_KEY = "discovery_v2_ui_flash"


def consume_discovery_flash() -> None:
    """Show and clear a pending flash message (one render only)."""
    payload = st.session_state.pop(FLASH_KEY, None)
    if not payload:
        return
    if isinstance(payload, dict):
        level = str(payload.get("level") or "success")
        message = str(payload.get("message") or "")
    else:
        level = "success"
        message = str(payload)
    if not message:
        return
    if level == "warning":
        st.warning(message)
    elif level == "error":
        st.error(message)
    elif level == "info":
        st.info(message)
    else:
        st.success(message)


def discovery_ui_flash_and_rerun(
    message: str,
    *,
    level: FlashLevel = "success",
) -> None:
    """Store a flash for the next render and rerun exactly once.

    No gateway, job, or domain mutation — UI orchestration only.
    """
    st.session_state[FLASH_KEY] = {"level": level, "message": str(message)}
    st.rerun()


__all__ = [
    "FLASH_KEY",
    "FlashLevel",
    "consume_discovery_flash",
    "discovery_ui_flash_and_rerun",
]
