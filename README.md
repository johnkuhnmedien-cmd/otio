# OTIO Schnittplaner

Lokale Streamlit-App zur Erstellung validierter Schnittpläne und OTIO-Dateien aus Voice-over und Medien-Assets.

**Aktueller Stand:** Meilenstein 2 — Projekt bearbeiten, Asset-Analyse (Gemini), Voice-over (Whisper lokal oder Gemini).

## Projekt bearbeiten

In der Sidebar **„Projekt bearbeiten“**:

- Ordnerauswahl (alle / ausgewählte)
- **Voice-over analysieren** → `voice_over_analysis.json` (Standard: **Whisper lokal**, kostenlos)
- **Ausgewählte Ordner (Gemini)** → `inventory.json` (pro Mediendatei eine Beschreibung)
- **Alles bearbeiten** → Voice-over + alle Asset-Ordner

Gemini-Aufrufe nur nach Checkbox-Bestätigung. API-Schlüssel in `.env`:

```env
GEMINI_API_KEY=dein_schlüssel
GEMINI_MODEL=gemini-2.0-flash
VOICE_OVER_BACKEND=whisper
WHISPER_MODEL=small
```

Unter **Projekt bearbeiten** kann das Gemini-Modell pro Sitzung gewählt werden. Voice-over standardmäßig mit **Whisper** (lokal, keine API-Kosten) — auf Apple Silicon (M4) empfohlen: `small` oder `medium`.

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

Nach der Analyse (spätere Meilensteine) entstehen im Projektordner:

- `inventory.json` — Beschreibungen aller Mediendateien je Asset-Unterordner
- `voice_over_analysis.json` — Audio-Analyse mit Timestamps pro Satz/Passage

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
