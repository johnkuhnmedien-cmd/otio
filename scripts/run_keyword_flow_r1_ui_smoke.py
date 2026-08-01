#!/usr/bin/env python3
"""Keyword Flow R1 Streamlit smoke — screenshots + build-label proof."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8520"
OUT = Path("/opt/cursor/artifacts/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path("/opt/cursor/artifacts/keyword-flow-r1-ui-smoke.json")


def main() -> int:
    findings: dict = {"base": BASE, "ok": False, "steps": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(BASE, wait_until="networkidle", timeout=120_000)
        time.sleep(2)
        home = OUT / "keyword-flow-r1-app-home.png"
        page.screenshot(path=str(home), full_page=True)
        findings["steps"].append({"home": str(home), "bytes": home.stat().st_size})

        # Systemstatus via path if available
        for path in ("/systemstatus", "/Systemstatus", "?page=systemstatus"):
            try:
                page.goto(BASE.rstrip("/") + path, wait_until="networkidle", timeout=60_000)
                time.sleep(1.5)
                body = page.inner_text("body")
                if "App-Build" in body or "Build:" in body or "cursor/" in body:
                    shot = OUT / "keyword-flow-r1-systemstatus.png"
                    page.screenshot(path=str(shot), full_page=True)
                    findings["build_text_snippet"] = body[:2000]
                    findings["steps"].append(
                        {"systemstatus": str(shot), "bytes": shot.stat().st_size}
                    )
                    break
            except Exception as exc:  # noqa: BLE001
                findings["steps"].append({"systemstatus_error": str(exc), "path": path})

        page.goto(BASE, wait_until="networkidle", timeout=120_000)
        time.sleep(1)
        body = page.inner_text("body")
        findings["home_has_keyword_flow_label"] = "Keyword Flow" in body
        findings["home_has_branch"] = "enhanced-keyword-flow-3982" in body
        findings["home_has_head"] = "1e9afac" in body or "cursor/enhanced-keyword-flow" in body

        # Try sidebar navigation to Systemstatus / Enhanced
        for label in ("Systemstatus", "System-Status", "🔍", "Enhanced", "Ohne Voice-over"):
            try:
                loc = page.get_by_text(label, exact=False).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    time.sleep(1.5)
                    body2 = page.inner_text("body")
                    if "enhanced-keyword-flow-3982" in body2 or "App-Build" in body2:
                        shot = OUT / "keyword-flow-r1-systemstatus.png"
                        page.screenshot(path=str(shot), full_page=True)
                        findings["nav_body_snippet"] = body2[:2500]
                        findings["steps"].append({"nav": label, "shot": str(shot)})
                        break
            except Exception as exc:  # noqa: BLE001
                findings["steps"].append({"nav_error": label, "err": str(exc)})

        browser.close()

    findings["ok"] = bool(
        findings.get("home_has_branch")
        or "enhanced-keyword-flow-3982" in str(findings.get("build_text_snippet") or "")
        or "enhanced-keyword-flow-3982" in str(findings.get("nav_body_snippet") or "")
    )
    REPORT.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(findings, indent=2, ensure_ascii=False))
    return 0 if findings["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
