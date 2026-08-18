# OTIO Schnittplaner

Lokale Streamlit-App zur Erstellung validierter Schnittpläne und OTIO-Dateien aus Voice-over und Medien-Assets.

**Aktueller Stand:** Meilenstein 2 — Projekt bearbeiten, Asset-Analyse (Gemini), Voice-over (Whisper lokal oder Gemini).

## Projekt bearbeiten

In der Sidebar **„Projekt bearbeiten“**:

- Ordnerauswahl (alle / ausgewählte)
- **Voice-over analysieren** → `voice_over_analysis.json` (Standard: **Whisper lokal**, kostenlos)
- **Ausgewählte Ordner (Gemini)** → `_otio/inventory/<Ordner>.json` (pro Ordner eine Datei mit Asset-Beschreibungen)
- **Alles bearbeiten** → Voice-over + alle Asset-Ordner

Gemini-Aufrufe nur nach Checkbox-Bestätigung. API-Schlüssel in `.env`:

```env
GEMINI_API_KEY=dein_schlüssel
GEMINI_MODEL=gemini-3.1-flash-lite
VOICE_OVER_BACKEND=whisper
WHISPER_MODEL=large-v3
```

Unter **Projekt bearbeiten** kann das Gemini-Modell pro Sitzung gewählt werden. Voice-over standardmäßig mit **Whisper large-v3** (lokal, beste Qualität, langsamer).

## Projektstruktur (Benutzerordner)

Ein Projekt ist ein Hauptordner, z. B.:

```
USA/
├── Grand Canyon/        # Asset-Unterordner (Videos/Bilder)
├── Yellowstone/
├── ...
├── Voice over/
│   └── DE/              # Voice-over-Audios (Sprache de -> DE)
└── _otio/               # App-Arbeitsordner (Cache, Frames — wird angelegt)
```

Nach der Analyse entstehen im Projektordner bzw. unter `_otio/`:

- `_otio/inventory/<Ordner>.json` — Beschreibungen aller Mediendateien je Asset-Unterordner (z. B. `Florida_Keys.json`)
- `voice_over_analysis.json` — Audio-Analyse mit Timestamps pro Satz/Passage

Ältere Projekte mit zentraler `inventory.json` im Projektroot werden beim Laden automatisch aufgeteilt.

## Voraussetzungen

- Python 3.11+
- FFmpeg und ffprobe (im `PATH`)

## Installation

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt   # nur für Tests

cp .env.example .env
```

## App starten

Doppelklick im Finder auf **`OTIO starten.command`** (einmalig: Rechtsklick → Öffnen, falls macOS blockiert).

Im Launcher:

- **App starten** — räumt Port 8501 und hängende Streamlit-Prozesse, dann Start
- **Stoppen** — beendet die App auch mitten in einem LLM-Call (Prozessende, nicht nur UI-Cancel)
- **Neu starten** — Stop + Start; optional Häkchen **Vorher git pull**
- **Diesen Branch holen und neu starten** — `git fetch`, Checkout, `git pull --ff-only`, dann Start

Die App läuft danach unabhängig vom Terminal. Das Terminal-Fenster kann zu, ohne dass Port 8501 blockiert bleibt.

Alternativ im Terminal:

```bash
source .venv/bin/activate
python scripts/otio_app_ctl.py restart --pull
python scripts/otio_app_ctl.py restart --branch cursor/mein-branch
python scripts/otio_app_ctl.py stop
```

Klassisch (nicht empfohlen, wenn ein LLM-Call hängt):

```bash
source .venv/bin/activate
streamlit run app.py
```

## Tests

```bash
pytest -v
```

## Pfadregeln

- **Projektordner** (`project_root`): vom Benutzer gewählt, z. B. `~/Documents/YT/.../USA`
- **Asset-Unterordner**: werden automatisch erkannt (alle direkten Unterordner außer Voice-over und `_otio`)
- **Voice-over**: Standard-Unterordner `Voice over/<SPRACHE>` (z. B. `DE` bei Sprache `de`)
- **Arbeitsordner** (`work_dir`): Standard `<Projektordner>/_otio` — Cache, Frames, Zwischenergebnisse
- **Originalmedien** in Asset- und Voice-over-Ordnern werden **niemals verändert**
- `frames_per_shot` (Standard: 3) — später konfigurierbar für Gemini-Frame-Analyse

## Nächste Meilensteine

1. Voice-over ↔ Asset-Ordner zuordnen (Dateiname → Ordner, mit Bestätigung)
2. Voice-over-Abschnitte ↔ konkrete Assets zuordnen
3. `edit_plan.json` validieren und OTIO exportieren
