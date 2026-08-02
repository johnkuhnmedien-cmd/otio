#!/usr/bin/env python3
"""R2 Streamlit smoke — fail-closed on Traceback / DuplicateElementKey."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8520"
OUT = Path("/opt/cursor/artifacts/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path("/opt/cursor/artifacts/keyword-flow-r2-ui-smoke.json")

FORBIDDEN = (
    "Traceback",
    "streamlit.errors.StreamlitDuplicateElementKey",
    "StreamlitDuplicateElementKey",
    "st.exception",
)


def _assert_clean(body: str, *, where: str) -> None:
    for token in FORBIDDEN:
        if token in body:
            raise RuntimeError(f"{where}: forbidden UI error token {token!r}")


def main() -> int:
    findings: dict = {"base": BASE, "ok": False, "steps": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1600})

        page.goto(BASE + "/systemstatus", wait_until="domcontentloaded", timeout=120_000)
        time.sleep(2)
        body = page.inner_text("body")
        _assert_clean(body, where="systemstatus")
        if "cursor/enhanced-keyword-flow-3982" not in body:
            raise RuntimeError("systemstatus missing branch label")
        page.screenshot(
            path=str(OUT / "keyword-flow-r2-systemstatus.png"), full_page=True
        )
        findings["build_snippet"] = [
            line for line in body.splitlines() if "Build" in line
        ][:6]
        findings["steps"].append("systemstatus_ok")

        page.goto(BASE + "/projekte", wait_until="networkidle", timeout=120_000)
        time.sleep(1)
        page.get_by_text("KeywordFlowR1", exact=False).first.click()
        time.sleep(0.5)
        page.get_by_role("button", name="Projekt bearbeiten").click()
        time.sleep(2.5)
        try:
            page.get_by_text("View 5 more", exact=False).click(timeout=4000)
            time.sleep(0.5)
        except Exception:
            pass
        page.get_by_text("⑦ Cut Plan", exact=True).first.click()
        time.sleep(3)
        body = page.inner_text("body")
        _assert_clean(body, where="cut-plan")
        page.locator("summary", has_text="Cut Plan Settings").click(timeout=8000)
        time.sleep(1)
        body = page.inner_text("body")
        _assert_clean(body, where="cut-plan-settings")
        for label in ("Keyword Flow", "Rhythmus", "Keyword-Sync", "Wort-Onsets"):
            if label not in body:
                raise RuntimeError(f"missing UI label: {label}")
        page.screenshot(
            path=str(OUT / "keyword-flow-r2-cut-plan-styles.png"), full_page=True
        )
        findings["steps"].append("cut_plan_styles_ok")

        # Final Output path
        try:
            page.get_by_text("⑧ Final Output", exact=False).first.click(timeout=8000)
            time.sleep(2)
            body = page.inner_text("body")
            _assert_clean(body, where="final-output")
            page.screenshot(
                path=str(OUT / "keyword-flow-r2-final-output.png"), full_page=True
            )
            findings["steps"].append("final_output_ok")
        except Exception as exc:  # noqa: BLE001
            findings["final_output_note"] = str(exc)

        browser.close()

    findings["ok"] = True
    REPORT.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(findings, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        REPORT.write_text(
            json.dumps({"ok": False, "error": str(exc)}, indent=2),
            encoding="utf-8",
        )
        print(exc, file=sys.stderr)
        raise SystemExit(1)
