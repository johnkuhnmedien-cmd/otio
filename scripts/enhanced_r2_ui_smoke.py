#!/usr/bin/env python3
"""Echter Streamlit-UI-Smoke für WITHOUT-VO-ENHANCED R2.

1) Seedet ein Enhanced-Projekt in die echte App-DB
2) Startet `streamlit run app.py`
3) Steuert die UI per Playwright (Chrome)
4) Speichert echte Screenshots unter /opt/cursor/artifacts/enhanced-r2-ui-smoke/
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/opt/cursor/artifacts/enhanced-r2-ui-smoke")
PORT = int(os.environ.get("ENHANCED_R2_UI_PORT", "8511"))
BASE = f"http://127.0.0.1:{PORT}"


def _seed_project() -> dict:
    sys.path.insert(0, str(ROOT))
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import ProjectCreate, ProjectMode
    from otio_app.project_repository import create_project
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
        ResolvedShot,
        ResolvedTimelineDocument,
        ScriptSegment,
        StockCandidate,
        StockSearchResultsDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        resolved_timeline_path,
        stock_search_results_path,
    )
    from otio_app.services.without_voiceover_enhanced.script_lock_service import (
        lock_script,
        save_script_draft,
    )
    from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
        default_stock_providers_config,
        save_stock_providers_config,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    # Keep the projects DB free of leftover smoke rows so the selectbox is unambiguous.
    try:
        import sqlite3

        from otio_app.config import get_db_path

        conn = sqlite3.connect(get_db_path())
        conn.execute("DELETE FROM projects WHERE name LIKE 'R2 UI Smoke%'")
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    stamp = int(time.time())
    seed_root = OUT / f"project_root_{stamp}"
    seed_root.mkdir(parents=True)
    (seed_root / "Assets").mkdir()
    work = seed_root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()

    # Unique name so selectbox is easy to find.
    name = f"R2 UI Smoke Enhanced {stamp}"

    project = create_project(
        ProjectCreate(
            name=name,
            project_root=str(seed_root),
            work_dir=str(work),
            language="en",
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        ),
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
    )

    # Default: all providers enabled (UI will disable Pexels).
    save_stock_providers_config(
        project,
        {n: True for n in default_stock_providers_config().providers},
    )

    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Smoke narration for R2.",
            segments=[
                ScriptSegment(
                    segment_id="segment_001",
                    text="Smoke narration for R2.",
                    sequence_index=1,
                )
            ],
        ),
    )
    lock_script(project)

    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            provider_status={"wikimedia": "completed", "pexels": "disabled"},
            candidates=[
                StockCandidate(
                    candidate_id="wikimedia_r2_001",
                    provider="wikimedia",
                    title="R2 Smoke Candidate",
                    media_type="photo",
                    preview_url="https://example.com/preview.jpg",
                    source_page="https://example.com/page",
                    license="CC0",
                )
            ],
        ),
    )

    # Damaged + valid local media for UI assignment.
    damaged = work / "damaged.jpg"
    damaged.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
    valid = work / "valid.jpg"
    Image.new("RGB", (24, 24), color=(12, 90, 160)).save(valid, format="JPEG")

    # Resolved timeline ready for OTIO after export_ready assignment.
    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=1.0,
            audio_segments=[],
            shots=[
                ResolvedShot(
                    shot_id="shot_001",
                    asset_id="wikimedia_r2_001",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=1.0,
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                )
            ],
        ),
    )

    meta = {
        "project_id": project.id,
        "project_name": project.name,
        "work_dir": str(work),
        "damaged_path": str(damaged),
        "valid_path": str(valid),
    }
    (OUT / "00_seed.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def _wait_http(url: str, timeout: float = 60.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"Streamlit not ready at {url}: {last_err}")


def _start_streamlit() -> subprocess.Popen:
    env = os.environ.copy()
    env["BROWSER"] = "none"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.port",
        str(PORT),
        "--server.headless",
        "true",
        "--server.address",
        "127.0.0.1",
        "--browser.gatherUsageStats",
        "false",
    ]
    log_path = OUT / "streamlit.log"
    log_fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (OUT / "streamlit.pid").write_text(str(proc.pid), encoding="utf-8")
    return proc


def _dismiss_dialogs(page) -> None:
    """Close Streamlit exception/welcome dialogs that block pointer events."""
    for _ in range(3):
        dialogs = page.locator('[data-testid="stDialog"]')
        if dialogs.count() == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        close_btn = page.locator(
            '[data-testid="stDialog"] button[aria-label="Close"], '
            '[data-testid="stDialog"] button:has-text("Close")'
        )
        if close_btn.count():
            close_btn.first.click(force=True)
            page.wait_for_timeout(300)


def _enhanced_nav_active(page) -> bool:
    # Do not match the radio label on "Neues Projekt".
    return "Workflow (Enhanced MVP)" in page.locator("body").inner_text()


def _expand_sidebar_more(page) -> None:
    more = page.get_by_text("View more", exact=False)
    if more.count() == 0:
        more = page.get_by_text("View 4 more", exact=False)
    if more.count():
        more.first.click()
        page.wait_for_timeout(600)


def _open_sidebar_page(page, title: str) -> None:
    """Client-side nav only — page.goto would create a new Streamlit session."""
    _expand_sidebar_more(page)
    link = page.get_by_role("link", name=title, exact=True)
    if link.count() == 0:
        link = page.locator(f'a:has-text("{title}")')
    if link.count() == 0:
        raise RuntimeError(f"Sidebar-Link nicht gefunden: {title!r}")
    link.first.click()
    page.wait_for_timeout(2500)
    _dismiss_dialogs(page)


def _force_streamlit_rerun(page) -> None:
    """Any widget interaction after ACTIVE_PROJECT_KEY is set rebuilds Enhanced nav."""
    # Clean Media "Nur prüfen" reliably triggers a script rerun.
    btn = page.get_by_role("button", name="Nur prüfen")
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(2000)
        _dismiss_dialogs(page)
        return
    assets = page.locator('[data-testid="stCheckbox"]', has_text="Assets")
    if assets.count():
        assets.first.locator("label").click()
        page.wait_for_timeout(1000)
        _dismiss_dialogs(page)


def _select_project(page, project_name: str) -> None:
    """Select project and force a Streamlit rerun so Enhanced nav is built.

    ``st.navigation`` reads ``ACTIVE_PROJECT_KEY`` before the page body runs.
    The selectbox writes the key during render, but Enhanced pages only appear
    after a subsequent rerun — keyboard commit on the combobox triggers that.
    """
    _dismiss_dialogs(page)
    box = page.locator('div[data-testid="stSelectbox"]').first
    box.wait_for(state="visible", timeout=30_000)
    inp = box.locator('input[aria-label="Projekt"], input').first
    inp.click()
    page.wait_for_timeout(250)
    # Unique filter → ArrowDown/Enter reliably commits React-Aria ComboBox.
    inp.fill(project_name)
    page.wait_for_timeout(500)
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    _dismiss_dialogs(page)
    if not _enhanced_nav_active(page):
        # Selecting the already-default project may not rerun — force one.
        _force_streamlit_rerun(page)
    for _ in range(20):
        if _enhanced_nav_active(page):
            return
        page.wait_for_timeout(400)
    fail_shot = OUT / "FAIL_select_project.png"
    page.screenshot(path=str(fail_shot), full_page=True)
    raise RuntimeError(
        f"Enhanced-Navigation erschien nicht nach Auswahl von {project_name!r}; "
        f"input={inp.input_value()!r}; screenshot={fail_shot}"
    )


def _drive_ui(meta: dict) -> dict:
    from playwright.sync_api import sync_playwright

    evidence: dict = {"steps": [], "screenshots": [], "otio_path": None, "target_urls": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(45_000)

        # Open Clean Media (always present), select Enhanced project → nav switches.
        page.goto(f"{BASE}/clean-media", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _dismiss_dialogs(page)
        _select_project(page, meta["project_name"])
        assert _enhanced_nav_active(page)
        evidence["steps"].append("enhanced_project_selected")

        _open_sidebar_page(page, "⑦ Cut Plan")
        if "Cut Plan (Enhanced" not in page.locator("body").inner_text():
            page.screenshot(path=str(OUT / "FAIL_cut_plan.png"), full_page=True)
            raise RuntimeError("Enhanced Cut Plan Seite nicht sichtbar")
        page.get_by_text("Stockanbieter verwenden").wait_for(state="visible")

        def _wait_streamlit_idle(timeout_ms: int = 15_000) -> None:
            deadline = time.time() + timeout_ms / 1000.0
            while time.time() < deadline:
                if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                    page.wait_for_timeout(250)
                    if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                        return
                page.wait_for_timeout(150)

        def _set_provider(label: str, *, checked: bool) -> None:
            widget = page.locator('[data-testid="stCheckbox"]', has_text=label).first
            box = widget.locator('input[type="checkbox"]')
            if box.is_checked() == checked:
                return
            widget.locator("label").first.click()
            _wait_streamlit_idle()
            box = page.locator('[data-testid="stCheckbox"]', has_text=label).locator(
                'input[type="checkbox"]'
            )
            if box.is_checked() != checked:
                raise RuntimeError(
                    f"Provider {label} konnte nicht auf checked={checked} gesetzt werden"
                )

        _set_provider("Pexels", checked=False)
        _set_provider("Wikimedia", checked=True)
        _wait_streamlit_idle()
        pex_state = (
            page.locator('[data-testid="stCheckbox"]', has_text="Pexels")
            .locator('input[type="checkbox"]')
            .is_checked()
        )
        (OUT / "debug_pre_save_ui.txt").write_text(
            f"pexels_checked={pex_state}\n", encoding="utf-8"
        )

        shot1 = OUT / "01_cut_plan_provider_checkboxes.png"
        page.screenshot(path=str(shot1), full_page=True)
        evidence["screenshots"].append(str(shot1))
        evidence["steps"].append("cut_plan_providers_visible")

        config_path = Path(meta["work_dir"]) / "config" / "stock_providers.json"
        mtime_before = config_path.stat().st_mtime if config_path.is_file() else 0
        save_btn = page.get_by_role("button", name="Anbieterauswahl speichern")
        save_btn.wait_for(state="visible")
        save_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        # Prefer real mouse click so Streamlit's websocket registers the button.
        box = save_btn.bounding_box()
        if box is None:
            raise RuntimeError("Save-Button ohne Bounding-Box")
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        # Wait until a script run starts (or file mtime changes).
        saw_run = False
        for _ in range(40):
            if page.locator('[data-testid="stStatusWidget"]').count():
                saw_run = True
                break
            if config_path.is_file() and config_path.stat().st_mtime > mtime_before:
                saw_run = True
                break
            page.wait_for_timeout(100)
        _wait_streamlit_idle()
        page.wait_for_timeout(800)
        # Retry once if Streamlit ignored the first click.
        if config_path.is_file() and config_path.stat().st_mtime <= mtime_before:
            save_btn.click(force=True)
            page.wait_for_timeout(300)
            _wait_streamlit_idle()
            page.wait_for_timeout(800)
        mtime_after = config_path.stat().st_mtime if config_path.is_file() else 0
        (OUT / "debug_post_save.txt").write_text(
            f"saw_run={saw_run}\nmtime_before={mtime_before}\nmtime_after={mtime_after}\n"
            f"content={config_path.read_text(encoding='utf-8') if config_path.is_file() else None}\n",
            encoding="utf-8",
        )
        for _ in range(40):
            if config_path.is_file():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                providers = cfg.get("providers") or {}
                pexels_enabled = bool((providers.get("pexels") or {}).get("enabled", True))
                wiki_enabled = bool((providers.get("wikimedia") or {}).get("enabled", False))
                if (not pexels_enabled) and wiki_enabled:
                    break
            page.wait_for_timeout(250)
        else:
            raise RuntimeError(
                f"Provider-Config nicht persistiert: {config_path} "
                f"content={config_path.read_text(encoding='utf-8') if config_path.is_file() else None}"
            )
        evidence["steps"].append("providers_saved_to_disk")

        # Full browser reload (required) — session resets; re-select, then Cut Plan.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _dismiss_dialogs(page)
        # After reload we may land on default/with-VO nav; open Clean Media via URL once.
        page.goto(f"{BASE}/clean-media", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        _dismiss_dialogs(page)
        _select_project(page, meta["project_name"])
        _open_sidebar_page(page, "⑦ Cut Plan")

        if "Cut Plan (Enhanced" not in page.locator("body").inner_text():
            page.screenshot(path=str(OUT / "FAIL_cut_plan_reload.png"), full_page=True)
            raise RuntimeError("Enhanced Cut Plan nach Reload nicht sichtbar")
        provider_row2 = page.get_by_test_id("stHorizontalBlock").first
        pexels2 = provider_row2.get_by_role("checkbox", name="Pexels", exact=True)
        wiki2 = provider_row2.get_by_role("checkbox", name="Wikimedia", exact=True)
        pexels2.wait_for(state="visible")
        assert not pexels2.is_checked(), "Pexels sollte nach Reload deaktiviert sein"
        assert wiki2.is_checked(), "Wikimedia sollte nach Reload aktiviert sein"

        shot2 = OUT / "02_providers_after_reload.png"
        page.screenshot(path=str(shot2), full_page=True)
        evidence["screenshots"].append(str(shot2))
        evidence["steps"].append("providers_persisted_after_reload")

        # Accept stock candidate.
        cand_widget = page.locator(
            '[data-testid="stCheckbox"]', has_text="wikimedia_r2_001"
        ).first
        cand_widget.wait_for(state="visible")
        if not cand_widget.locator('input[type="checkbox"]').is_checked():
            cand_widget.locator("label").click()
            _wait_streamlit_idle()
        page.get_by_role("button", name="Auswahl akzeptieren").click()
        _wait_streamlit_idle()
        page.wait_for_timeout(800)

        # Assign damaged local file.
        path_input = page.get_by_role(
            "textbox", name="local_media_path für wikimedia_r2_001"
        )
        path_input.wait_for(state="visible")
        path_input.click()
        path_input.fill(meta["damaged_path"])
        page.get_by_role(
            "button",
            name="Lokale Datei zuordnen & validieren (wikimedia_r2_001)",
        ).click()
        _wait_streamlit_idle()
        page.get_by_text("local_media_invalid", exact=False).wait_for(state="visible")

        shot3 = OUT / "03_damaged_local_error.png"
        page.screenshot(path=str(shot3), full_page=True)
        evidence["screenshots"].append(str(shot3))
        evidence["steps"].append("damaged_local_rejected")

        # Assign valid local image.
        path_input = page.get_by_role(
            "textbox", name="local_media_path für wikimedia_r2_001"
        )
        path_input.click()
        path_input.fill(meta["valid_path"])
        page.get_by_role(
            "button",
            name="Lokale Datei zuordnen & validieren (wikimedia_r2_001)",
        ).click()
        _wait_streamlit_idle()
        page.get_by_text("export_ready", exact=False).wait_for(state="visible")

        shot4 = OUT / "04_valid_local_export_ready.png"
        page.screenshot(path=str(shot4), full_page=True)
        evidence["screenshots"].append(str(shot4))
        evidence["steps"].append("valid_local_export_ready")

        # OTIO export on Final Output (sidebar click keeps session).
        _open_sidebar_page(page, "⑧ Final Output")
        if "Final Output (Enhanced)" not in page.locator("body").inner_text():
            page.screenshot(path=str(OUT / "FAIL_final_output.png"), full_page=True)
            raise RuntimeError("Enhanced Final Output nicht sichtbar")
        page.get_by_role("button", name="OTIO erzeugen").click()
        page.wait_for_timeout(2500)
        success = page.get_by_text("OTIO geschrieben", exact=False)
        success.wait_for(state="visible", timeout=30_000)
        success_text = success.inner_text()

        shot5 = OUT / "05_otio_export_success.png"
        page.screenshot(path=str(shot5), full_page=True)
        evidence["screenshots"].append(str(shot5))
        evidence["steps"].append("otio_export_success")
        evidence["otio_success_text"] = success_text

        browser.close()

    # Parse OTIO file from filesystem under work dir.
    work = Path(meta["work_dir"])
    otio_files = sorted(work.rglob("*.otio"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not otio_files:
        # Also search language scope.
        otio_files = sorted(
            Path(meta["work_dir"]).parent.rglob("*enhanced*.otio"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if otio_files:
        otio_path = otio_files[0]
        evidence["otio_path"] = str(otio_path)
        payload = otio_path.read_text(encoding="utf-8")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {}
        urls: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                if "target_url" in node:
                    urls.append(str(node["target_url"]))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        evidence["target_urls"] = urls
        assert urls, "OTIO enthält keine target_url"
        assert all(not u.lower().startswith(("http://", "https://")) for u in urls)
        assert any(meta["valid_path"] in u or Path(meta["valid_path"]).name in u for u in urls)

    (OUT / "06_ui_smoke_evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = _seed_project()
    proc = _start_streamlit()
    try:
        _wait_http(BASE)
        evidence = _drive_ui(meta)
        print(json.dumps({"ok": True, "meta": meta, "evidence": evidence}, indent=2))
        return 0
    except Exception as exc:
        (OUT / "ERROR.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        print(f"UI smoke failed: {exc}", file=sys.stderr)
        # Keep streamlit log for diagnosis.
        return 1
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
