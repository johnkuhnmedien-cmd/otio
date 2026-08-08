# AGENTS.md

## Cursor Cloud specific instructions

**What this is:** `OTIO Schnittplaner` — a local, German-language **Streamlit** app that builds validated
edit plans and OpenTimelineIO files from voice-over audio and media assets (asset analysis via Gemini,
voice-over transcription via Whisper/Gemini, cut-plan LLMs, stock supplements, OTIO export). Single service.

### Repository layout gotcha (important)
- The `main` branch is essentially empty (initial commit + a one-line `README.md`). **All real code lives in
  the `cursor/*` feature branches.** To run, test, or develop anything you must be on a feature branch (or a
  branch based off one), not on `main`. Pick the relevant feature branch for your task.

### Running / testing (see `README.md` for the canonical commands)
- A Python virtualenv is preinstalled at `.venv/`. Activate it first: `source .venv/bin/activate`.
  The startup update script recreates/refreshes it, so you normally don't need to reinstall deps.
- Run the app (dev mode): `streamlit run app.py` — serves on port `8501`. `.streamlit/config.toml` already
  sets `server.headless = true`, so it will not try to open a browser.
- Tests: `pytest` — the suite is large (~3000 tests, ~12 min). A handful of failures are usually present on
  in-progress feature branches due to test/code drift (assertion mismatches, e.g. page lists or file paths),
  **not** environment problems. Treat a broadly-green run (thousands passing) as a healthy environment.
- No linter/formatter is configured (no ruff/flake8/black/mypy, no `pyproject.toml`); there is no lint command.
- `ffmpeg`/`ffprobe` are required and are already available on the VM `PATH`.

### API keys / external services (non-obvious)
- The app **runs without any API keys**. Keys are optional and only needed to actually call cloud providers
  (Gemini, OpenAI, Anthropic, OpenRouter/xAI, Pexels/Adobe/Unsplash/Pixabay). Add them in-app under the
  "🔑 API-Schlüssel" page (persisted to `data/user_secrets.env`, git-ignored) or via a local `.env`
  (copy `.env.example`). Do not commit real keys.
- Voice-over defaults to **local Whisper** (`faster-whisper`, `WHISPER_MODEL=large-v3`). The model is
  **downloaded from the network on first use** (large) — expect a delay / network requirement the first time
  you actually run a voice-over analysis, not at startup.

### Data locations
- Projects are stored in a local SQLite DB at `data/projects.db`. Per-project working artifacts (cache, frames,
  edit plans, exports) are written under `<project_root>/_otio/` (and `_otio/<LANG>/`, e.g. `_otio/DE/`).
  Original media in asset/voice-over folders is never modified.
