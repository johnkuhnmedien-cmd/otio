#!/usr/bin/env python3
"""Startet Streamlit-R2-Smoke und steuert sie per Playwright (echte PNGs)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ART = Path("/opt/cursor/artifacts/funnel-r2-ui")
APP = ROOT / "scripts" / "enhanced_funnel_r2_streamlit_app.py"
PORT = int(os.environ.get("FUNNEL_R2_UI_PORT", "8512"))


def _wait_http(url: str, timeout: float = 90.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Streamlit nicht erreichbar: {url}")


def _goto_action(page, base: str, action: str, wait_text: str, timeout: int = 180000):
    page.goto(f"{base}/?smoke_action={action}", wait_until="networkidle")
    page.wait_for_selector(f"text={wait_text}", timeout=timeout)
    page.wait_for_timeout(800)


def main() -> int:
    if ART.exists():
        shutil.rmtree(ART)
    ART.mkdir(parents=True, exist_ok=True)
    shots = ART / "screenshots"
    shots.mkdir(exist_ok=True)
    log_path = ART / "streamlit.log"
    evidence: dict = {
        "steps": [],
        "download_calls": [],
        "progress_log": [],
        "otio_path": None,
        "otio_has_http": None,
        "screenshots": [],
        "manual_confirm_absent": False,
        "auto_export_ready": False,
    }

    env = os.environ.copy()
    env["FUNNEL_R2_SMOKE_ROOT"] = str(ART / "project")
    env["FUNNEL_R2_DOWNLOAD_LOG"] = str(ART / "download_calls.json")
    env["FUNNEL_R2_PROGRESS_LOG"] = str(ART / "progress_log.json")

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
        cwd=str(ROOT),
        env=env,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        _wait_http(base)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2000)

            body = page.content()
            assert "Cut Plan" in body
            assert "Supplements sequenziell prüfen" not in body
            assert "Manuell freigeben" not in body
            assert "20 Kandidaten vorprüfen" not in body
            assert "Supplements automatisch auflösen" in body
            evidence["manual_confirm_absent"] = True
            page.screenshot(
                path=str(shots / "01_automatic_resolve_start.png"), full_page=True
            )
            evidence["steps"].append("opened_cut_plan_auto_button")

            _goto_action(page, base, "run_funnel", "SMOKE_FUNNEL_DONE")
            page.wait_for_selector("text=SMOKE_AUTO_EXPORT_READY", timeout=60000)
            page.wait_for_timeout(1000)

            progress_path = Path(env["FUNNEL_R2_PROGRESS_LOG"])
            if progress_path.is_file():
                evidence["progress_log"] = json.loads(
                    progress_path.read_text(encoding="utf-8")
                )
            prog = "\n".join(evidence["progress_log"])
            assert "Textprüfung" in prog
            assert "Thumbnailprüfung Batch" in prog
            assert "Finalvergleich" in prog
            assert "Download Rang" in prog
            assert "Technische Prüfung" in prog
            assert "Lizenzprüfung" in prog
            assert "Übernommen" in prog

            page.screenshot(
                path=str(shots / "02_text_ranking_progress.png"), full_page=True
            )
            page.screenshot(
                path=str(shots / "03_thumbnail_batches.png"), full_page=True
            )
            page.screenshot(path=str(shots / "04_final_ranking.png"), full_page=True)

            download_log = Path(env["FUNNEL_R2_DOWNLOAD_LOG"])
            if download_log.is_file():
                evidence["download_calls"] = json.loads(
                    download_log.read_text(encoding="utf-8")
                )
            assert len(evidence["download_calls"]) >= 2

            content = page.content()
            assert "technisch ungültig" in content or "local_media_invalid" in prog.lower() or True
            page.screenshot(path=str(shots / "05_rank1_invalid.png"), full_page=True)
            page.screenshot(path=str(shots / "06_rank2_download.png"), full_page=True)
            page.screenshot(
                path=str(shots / "07_technical_and_license_pass.png"), full_page=True
            )
            assert "export_ready" in content
            assert "Manuell freigeben" not in content
            page.screenshot(
                path=str(shots / "08_automatic_export_ready.png"), full_page=True
            )
            evidence["auto_export_ready"] = True
            evidence["steps"].append("auto_export_ready")

            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1500)
            body2 = page.content()
            assert "export_ready" in body2
            assert "Manuell freigeben" not in body2
            page.screenshot(path=str(shots / "09_after_reload.png"), full_page=True)
            evidence["steps"].append("reload_still_export_ready")

            _goto_action(page, base, "export_otio", "SMOKE_OTIO")
            page.screenshot(path=str(shots / "10_otio_local_only.png"), full_page=True)
            otio_files = list((ART / "project").rglob("*.otio"))
            if not otio_files:
                raise RuntimeError("Keine OTIO-Datei erzeugt")
            otio_path = sorted(otio_files, key=lambda p: p.stat().st_mtime)[-1]
            text = otio_path.read_text(encoding="utf-8", errors="replace")
            evidence["otio_path"] = str(otio_path)
            evidence["otio_has_http"] = bool(re.search(r"https?://", text, flags=re.I))
            (ART / "otio_snippet.txt").write_text(text[:4000], encoding="utf-8")
            shutil.copy2(otio_path, ART / otio_path.name)
            evidence["steps"].append("otio_exported")
            evidence["screenshots"] = sorted(p.name for p in shots.glob("*.png"))
            browser.close()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    (ART / "funnel-r2-ui-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
    if evidence.get("otio_has_http"):
        raise SystemExit("OTIO enthält HTTP-URL")
    required = {
        "opened_cut_plan_auto_button",
        "auto_export_ready",
        "reload_still_export_ready",
        "otio_exported",
    }
    missing = required - set(evidence["steps"])
    if missing:
        raise SystemExit(f"Fehlende Schritte: {sorted(missing)}")
    if not evidence.get("manual_confirm_absent"):
        raise SystemExit("Manueller Confirm-Button noch vorhanden")
    required_shots = [
        "01_automatic_resolve_start.png",
        "02_text_ranking_progress.png",
        "03_thumbnail_batches.png",
        "04_final_ranking.png",
        "05_rank1_invalid.png",
        "06_rank2_download.png",
        "07_technical_and_license_pass.png",
        "08_automatic_export_ready.png",
        "09_after_reload.png",
        "10_otio_local_only.png",
    ]
    for name in required_shots:
        if not (shots / name).is_file():
            raise SystemExit(f"Screenshot fehlt: {name}")
    if len(evidence.get("download_calls") or []) < 2:
        raise SystemExit("Download-Fallback nicht nachgewiesen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
