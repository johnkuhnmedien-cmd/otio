#!/usr/bin/env python3
"""Minimal Streamlit UI smoke for Keyword Flow Free style selector."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path("/opt/cursor/artifacts/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path("/opt/cursor/artifacts/reports/keyword_flow_free_ui_smoke.json")
APP = Path("/tmp/kff_style_selector_app.py")
PORT = 8531


def write_app() -> None:
    APP.write_text(
        '''\
import streamlit as st

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_UNIFIED,
    UNIFIED_CUT_STYLE_CHOICES,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_RHYTHM,
)

st.set_page_config(page_title="Keyword Flow Free Style Selector", layout="wide")
st.title("Enhanced Cut Plan Settings")
st.caption("WITHOUT-VO-ENHANCED-KEYWORD-FLOW-FREE-001")

style_labels = {
    UNIFIED_CUT_STYLE_RHYTHM: "Rhythmus (shot_min/max)",
    UNIFIED_CUT_STYLE_KEYWORD_SYNC: "Keyword-Sync (Wort↔Bild)",
    UNIFIED_CUT_STYLE_KEYWORD_FLOW: "Keyword Flow",
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE: "Keyword Flow Free",
}
style = st.radio(
    "Unified-Stil",
    options=list(UNIFIED_CUT_STYLE_CHOICES),
    format_func=lambda s: style_labels.get(s, s),
    index=list(UNIFIED_CUT_STYLE_CHOICES).index(UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE),
    horizontal=True,
    key="kff_style",
)
if style == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE:
    st.caption(
        "Freier kontextbasierter Schnitt auf kontinuierlichem Wortfluss. "
        "Mehrere Shots pro Satz und Shots über Satzgrenzen sind ausdrücklich erlaubt."
    )
st.write("Selected:", style_labels.get(style, style))
st.write("Mode:", CUT_PLAN_MODE_UNIFIED)
''',
        encoding="utf-8",
    )


def main() -> int:
    write_app()
    findings: dict = {"ok": False, "port": PORT}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP),
            "--server.port",
            str(PORT),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(4)
        # Prefer playwright if available; else use a simple HTTP check + computer use later.
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", "playwright"]
            )
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"]
            )
            from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="networkidle", timeout=120_000)
            time.sleep(2)
            body = page.inner_text("body")
            findings["has_keyword_flow_free"] = "Keyword Flow Free" in body
            findings["has_keyword_flow"] = "Keyword Flow" in body
            findings["has_caption"] = "kontinuierlichem Wortfluss" in body
            shot = OUT / "keyword-flow-free-style-selector.png"
            page.screenshot(path=str(shot), full_page=True)
            findings["screenshot"] = str(shot)
            findings["screenshot_bytes"] = shot.stat().st_size
            browser.close()
        findings["ok"] = bool(
            findings.get("has_keyword_flow_free") and findings.get("has_caption")
        )
    except Exception as exc:  # noqa: BLE001
        findings["error"] = str(exc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    REPORT.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(findings, indent=2, ensure_ascii=False))
    return 0 if findings.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
