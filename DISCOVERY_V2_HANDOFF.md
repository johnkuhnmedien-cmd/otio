# Discovery V2 – Handoff

## Repository-Stand

- Branch: `cursor/discovery-v2-integration`
- PR: `#69`
- Ursprüngliche Basis: `7187d163a5959351a1ee79f5c931b0d651e21d49`
- Technischer Ausgang vor Phase 8D: `1b5f0d564c46050c7702734557357cce6dbcb26c`
- Phase 7: `APPROVED`
- Phase 8A: `APPROVED`
- Phase 8B: `APPROVED`
- Phase 8C: `APPROVED`
- Phase 8D / Phase 8 Closeout: `APPROVED`
- Phase 9 Editorial Core / Coverage: **implementiert (Fake Text E2E)**
- Phase 10 Supplementation / Script Lock: **implementiert (Fake Stock E2E)**
- Phase 11 Voice / Pause / Timing: **implementiert (Fake Voice E2E)**
- Phase-11 Contract Hardening R1: **angewendet** (Schema **18**)
- SoT Bootstrap: `RECONSTRUCTED_BOOTSTRAP` — für dieses Repository neu konsolidiert;
  Recovery-Suche → `NOT_FOUND`; andere Projekte und gelöschter GPT-Wissensstand
  sind keine Repositoryquelle; Authority-Restore der verbindlichen Regeln
  (D-DOC-008 … D-DOC-010; D-DOC-006/007 nur zusammen mit diesen)
- Registry-Schema: **18**
- Fake Vision: aktiv (`provider=fake`)
- Fake Text Editorial: aktiv (`provider=fake`, `fake-editorial-v1`)
- Fake Stock Search: aktiv (`provider=fake`)
- Fake Voice: aktiv (`provider=fake`, `fake-neutral-v1`, WAV PCM s16le 48 kHz mono)
- Echte Vision-/Text-/Stock-/Voice-Provider: **gesperrt**
- Kein ElevenLabs; keine echte Adobe-OAuth-/Lizenz-/Download-Integration
- Aktiver Produktauftrag: keiner (Phase 11 Fake-Pfad abgeschlossen; Hardening R1)
- Nächster erlaubter Schritt nach Freigabe: **Phase 12 planen**
- Phase 12 noch nicht begonnen
- Gesperrt ohne eigenen Auftrag: echte Provider, Visual Edit Plan, Humanity, OTIO
- Teststand nach Contract Hardening R1: **2883 collected / 2864 passed / 18 failed / 1 skipped**

## Source of Truth

Verbindliche Reihenfolge:

1. `.cursor/rules/00-core-architecture.mdc`
2. `.cursor/rules/01-step-discipline.mdc`
3. `docs/DECISIONS.md`
4. `docs/MASTER_PLAN.md`
5. `docs/ALPHA_SCOPE.md`
6. `docs/PIPELINE_SPEC.md`
7. `docs/MEDIA_LIFECYCLE.md`
8. `docs/EDITORIAL_QUALITY.md`
9. `docs/MODEL_ROUTING.md`
10. `docs/CLASSIC_MIGRATION_CONTRACT.md`
11. `docs/PROGRESS.md`
12. `docs/CHIEF_DEV_HANDOFF.md`
13. `docs/source_plans/*` (nachrangig; ggf. leer/nicht vorhanden; überschreibt nichts Höheres)

`docs/ALPHA_EXECUTION_MANIFEST.md` und dieses Handoff-Dokument sind untergeordnet.
Bei Widerspruch gilt die höhere Quelle. Verbindlich ist der für Discovery V2
geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.

## Projektmodi

- `with_voiceover`
- `without_voiceover`
- `discovery_v2`

Classic und Without-VO müssen unverändert bleiben.

## Pfadvertrag

Discovery V2 schreibt ausschließlich unter:

`<project_root>/_otio_v2/`

Discovery darf unter:

`<project_root>/_otio/`

nichts erzeugen, verändern oder löschen.

Der gespeicherte Classic-`work_dir` darf bei Discovery nicht als Schreibziel verwendet werden.

## Architektur

- Python 3.12
- Streamlit
- modularer Monolith
- UI → Application → Domain → Adapters → Persistence
- keine Fachlogik in Streamlit
- SQLite als interne Wahrheit
- versionierte JSON-Artefakte
- Pydantic
- FFmpeg/ffprobe nur über Adapter und nur im kontrollierten Worker
- OpenTimelineIO
- pytest
- schwere Jobs außerhalb normaler Streamlit-Reruns
- externe Dienste nur über Adapter
- keine hart codierten Modelle in Fachmodulen

## Abgeschlossene Schritte

1. Discovery-Shell
   `bc17ad277ce1456b7f063f69c6e5ed540fddefc9`

2. Shell-Härtung
   `98e35c17bed01dbcf890fe360065d77015355516`

3. Read-only Inventory
   `ca242f3bc11dd8cccc8c9f6fa3c0f1eb8518ec97`

4. Inventory-Auswahl und Bestätigung
   `62eeb2e6ff667bda060662f70c273a7922915c3d`

5. Asset Registry
   `41316bcc8b98555567eb052c2547c5f47c58c13e`

6. Technische Validierung
   `af5978bd8c944f0e6bd121540ef08a6a1b021eca`

7. `_otio`-Isolation
   `65aa0877a7e73c45830565e8cbd45124ba4c498d`

8. Mode-aware Arbeitswurzel und Job-Recovery
   `1bbf192e5ba1eba4597a92a0185cdca85eb822ce`

9. Media-Intake-Planer
   `feda546a4e796475c57cee10e6c549de5ff79614`

10. Konservative Video-Copy-Entscheidung
    `da7dbbf740cc06d9d2b457b2cc9594c6ad75099d`

11. Copy-Intake (bytegenaue Kopie)
    `1b590237498f4ef1fa9ecd6e3564c14f9ab8b338`
    Freigegeben als Teil von Phase 7.

12. Remux-Intake
    `fc60242fe719747334b85e277aef91117ac6de6d`

13. Video-Transcode-Intake
    `26e37b6dea3a60510f1017d7b994f40ae25933f5`

14. Transcode-Zähler und Stream-Policy
    `4248dc590843816d0c79298e996be5e838f5bf82`

15. Intake-UI ohne Live-Probing
    `aeb90bd3bc3e413ae97ff176e464dcb96354c0f3`

16. TIFF-zu-PNG-Image-Convert
    `e8ba32d567104390bf3f6898c6b5137c9eefc3ae`

17. Phase-7-Abschlusshärtung
    `3ba0716a6d2cc12a4e0fdf9f61b5c2dcdead5016`

18. Phase-7-Handoff-Abschluss
    `26030b024d959f77c7eead850053e5b16afade4b`

19. Analysis-Contracts (Phase 8A)
    `8168c8b84d693c5b29556759a755d2ded78677d7`

20. Assetanalyse-Navigationstest
    `39d42f81392bf9ec06a5aa87f9a15ac230480684`

21. Phase-8A-Härtung (Rohstatus-Eligibility)
    `11e5cf8ae96e38cd9686cd7d915ad85b66be98e9`

22. Phase-8A-Handoff
    `c104d785f0d32ce1df3c7dcafbb5787a7ba1f57a`

23. Shot-/Frame-Prepare (Phase 8B)
    `867267184e4afd368b5ae5404131ad090fa46ef3`

24. Phase-8B-Härtung / Evidenzmatrix
    `0d8ec2f060107a1720c750b11e4372c3529a2c61`

## Phase 7 – Media Intake (APPROVED)

Phase 7 ist abgeschlossen und freigegeben.

### Freigegebene Intake-Aktionen

- bytegenaue Kopie
- Container-Remux
- H.264-Video-Transkodierung
- TIFF-zu-PNG-Konvertierung

### Freigegebene Profile

- `copy-v1`
- `remux-mp4-v1`
- `video-h264-v1`
- `image-png-v1`

### Gemeinsame Laufzeitregeln

- gegenseitige Run-Sperre über alle vier Scopes
- terminale Runs blockieren nicht
- Orphan-Runs → `worker_interrupted`
- Idempotenz / Reuse ohne Binärdubletten
- Konflikte werden niemals überschrieben
- Crash-Fenster nach `os.replace` ist für alle vier Aktionen reparierbar
- Ausgaben bleiben historisch unter den jeweiligen Profilpfaden getrennt
- Discovery schreibt nur unter `_otio_v2`

## Phase 8A – Analysis Contracts (APPROVED)

Phase 8A ist abgeschlossen und freigegeben.

### Freigegebene Commits

- `8168c8b84d693c5b29556759a755d2ded78677d7` — `feat: add Discovery V2 analysis contracts`
- `39d42f81392bf9ec06a5aa87f9a15ac230480684` — `test: allow Assetanalyse in Discovery navigation`
- `11e5cf8ae96e38cd9686cd7d915ad85b66be98e9` — `fix: harden Discovery V2 analysis contracts`

### Implementierter Umfang

- Analysis-Domainverträge (`AnalysisInputIdentity`, Eligibility, Run/RunAsset, Shot-/Frame-Vertragsformen)
- Analysis-Identity-Profil: `analysis-contract-v1`
- Registry-Schema zunächst **11** (durch Phase 8B auf **12** erhöht)
- Tabellen:
  - `analysis_runs`
  - `analysis_run_assets`
  - `analysis_identities`
- exakte aktuelle Plan-zu-Working-Media-Bindung
- Eligibility nur bei Working-Media-Rohstatus `completed`
- Discovery-Seite **Assetanalyse**
- Analysis-Pfadvertrag unter `_otio_v2/analysis/`

### Verbindliche Eligibility-Regel

Nur zulässig:

`working_media` Rohstatus = `completed`

und exakte Übereinstimmung mit dem aktuellen Intake-Plan-Item:

- `project_id`
- `asset_id`
- `validation_id` / Source-SHA der aktuellen Validation
- erwartete `action`
- erwartete `processing_profile_version`

Nicht zulässig:

- Rohstatus `ready`
- `pending`
- `failed`
- unbekannter Status
- historische Ersatzprofile
- Auswahl nach `created_at`
- Auswahl nach Pfad oder Dateiname

Audio-only: `not_applicable` für visuelle Analyse.

## Phase 8B – Shot- und Frame-Vorbereitung (APPROVED)

Phase 8B ist abgeschlossen und freigegeben.

### Freigegebene Commits

- Technischer Commit:
  `867267184e4afd368b5ae5404131ad090fa46ef3`
  — `feat: add Discovery V2 shot and frame preparation`
- Hardening-Commit:
  `0d8ec2f060107a1720c750b11e4372c3529a2c61`
  — `test: harden Discovery V2 analysis prepare R1 evidence`

### Implementierter Umfang

- Registry-Schema **12**
- Tabellen:
  - `technical_shots`
  - `representative_frames`
- Profile:
  - `analysis-prepare-v1`
  - `shot-detect-v1`
  - `frame-sample-v1`
- Run-Scope: `analysis_prepare_only`
- FFmpeg-Scene-Detection über Discovery-Adapter (`select='gt(scene,0.35)'`)
- deterministische Shot-Grenzen (Dedup 0,04 s, Min 0,40 s, Max 30 s)
- repräsentative Frames (Mittelpunkt, Schwarz-Kandidaten, Cap 24)
- lokale technische Signale (`brightness_mean`, `black_fraction`, `is_black`, `sharpness_score`, Hashes)
- Standbild-Previews (JPEG/PNG) ohne künstliche Shots/Timestamps
- Audio → `not_applicable`
- Prepare-Worker, Launcher, Orphan-Recovery
- JSON-Runberichte unter `analysis/runs/`
- UI-Button „Lokale Analyse vorbereiten“ + persistierte Review-Ansicht
- keine Visual Observations, kein Gateway, keine APIs, keine Modellanalyse

### Artefaktpfade

Unter `_otio_v2/analysis/`:

- `analysis/frames/<working_media_id>/frame-sample-v1/<shot-or-still>/<frame_id>.<jpg|png>`
- `analysis/temp/<run_id>/`
- `analysis/runs/<run_id>.json`
- `analysis/latest_prepare_run.json`
- `analysis/manifests/<analysis_identity_id>/analysis-prepare-v1.json`
- `analysis/observations/` (Pfadvertrag vorhanden, noch ohne Produktinhalt)

### Domaintrennung (weiterhin verbindlich)

- Working Media
- Technical Shot Segment
- Representative Frame
- lokale technische Signale
- Visual Observation *(implementiert, Fake Vision)*
- Observation Review *(implementiert)*
- Visual Beat *(noch nicht implementiert)*

`prepared` bedeutet nicht modellanalysiert, nicht redaktionell akzeptiert und nicht für Visual Beats freigegeben.

## Phase 8C – Fake Vision (APPROVED)

- Technischer Commit: `21bd454f1cd667539ecc22e26f2d5a23ad3945d3`
- R1 Evidence: `1b5f0d564c46050c7702734557357cce6dbcb26c`
- Schema **13**: `analysis_consent_events`, `model_analysis_attempts`, `visual_observations`
- Aufrufpfad: UI → Model Analysis Service → Discovery Vision Gateway → FakeVisionAdapter
- Explizite Consent pro Model-Run; kein HTTP/SDK; keine echten Provider

## Phase 8D – Observation Review / Closeout (APPROVED)

- Schema **14**: ausschließlich neue Tabelle `visual_observation_reviews`
- Unveränderliche Reviewrevisionen: `accepted` | `reanalyze_requested` | `rejected`
- Editorial-Ready-Gate nur für aktuelle, gültige, akzeptierte Observations
- `accepted` ist keine Asset-/Fakten-/Geo-/Synthetic-/Beat-Freigabe
- `reanalyze_requested` startet keinen automatischen Model-Run
- UI: manuelles Review auf Assetanalyse ohne Medien-/Gateway-I/O
- Alpha Execution Manifest: `docs/ALPHA_EXECUTION_MANIFEST.md` (untergeordnet)
- Bekannter VFR-Skip bleibt; 18 Baseline-Failures bewusst unrepariert

## Implementierter Ablauf

Projekt anlegen
→ Inventory
→ Auswahl bestätigen
→ Asset Registry
→ technische Validierung
→ SHA-256
→ ffprobe (Worker)
→ Dublettenhinweise
→ Intake-Plan
→ Copy-Intake
→ Remux-Intake
→ Video-Transcode-Intake
→ TIFF-Image-Convert
→ Assetanalyse-Eligibility
→ lokale Analysevorbereitung (Shots/Frames)

## Inventory

Artefakte:

- `_otio_v2/inventory/snapshots/<scan_id>.json`
- `_otio_v2/inventory/latest.json`
- `_otio_v2/inventory/selections/<selection_id>.json`
- `_otio_v2/inventory/selection_latest.json`

Regeln:

- rekursiver read-only Scan
- relative Pfade
- oberster Ordner = `source_group`
- `source_group` ist kein Kapitel
- Selection ist an `scan_id` gebunden
- neuer Scan macht alte Selection stale

## Registry

Registry-Schema:

`12`

SQLite:

`_otio_v2/registry/assets.sqlite3`

SQLite ist interne Wahrheit.

Historische Imports, Validierungen, Intake-Pläne, Working-Media-Versionen, Analysis-Identities, Technical Shots und Representative Frames dürfen nicht automatisch gelöscht werden.

Noch keine Tabellen für:

- `visual_observations`
- `model_analysis_attempts`
- `analysis_consent_events`

## Technische Validierung

Gespeichert werden unter anderem:

- Größe und mtime
- SHA-256
- Container
- Video-/Audio-Codec
- Auflösung
- rationale Framerate
- Timecode oder `null`
- Pixel-Format
- Bit-Tiefe
- Dublettenhinweise
- Bildfelder für TIFF/PNG-Intake

Fehlender Timecode ist kein Fehler.

Aktuelle Source-Prüfungen erfolgen ausschließlich im kontrollierten Worker.

## Video-Copy-Policy

`copy` oder `remux` nur bei:

- kompatiblem Codec
- geeignetem Container
- `pixel_format` = `yuv420p` oder `yuvj420p`
- `bit_depth` = 8

Fehlende Angaben:

- `transcode`
- `insufficient_copy_metadata`

Inkompatible Angaben:

- `transcode`
- `incompatible_pixel_format`
- `incompatible_bit_depth`

## Verbindliche Working-Media-Pfade

Kanonische Ausgaben:

- Copy:
  `_otio_v2/media/working/<asset_id>/<source_sha256>/copy-v1/<asset_id>.<extension>`
- Remux:
  `_otio_v2/media/working/<asset_id>/<source_sha256>/remux-mp4-v1/<asset_id>.mp4`
- Video-Transcode:
  `_otio_v2/media/working/<asset_id>/<source_sha256>/video-h264-v1/<asset_id>.mp4`
- TIFF→PNG:
  `_otio_v2/media/working/<asset_id>/<source_sha256>/image-png-v1/<asset_id>.png`

Temporäre Dateien:

`_otio_v2/media/temp/<run_id>/...`

`source_relative_path` ist nur Herkunftsmetadatum und darf nicht der kanonische Zielpfad sein.

## UI-Regel

Beim normalen Streamlit-Rendering ist verboten:

- ffprobe
- FFmpeg
- Pillow `Image.open`
- Hashing
- `stat` oder mtime der Medien
- automatischer Jobstart durch Rerun
- Provider-/API-Aufrufe

Die UI liest ausschließlich persistierte Validation-/Registry-/Analysis-Daten.

Die Seite **Assetanalyse** startet lokale Vorbereitung nur über den expliziten Button
„Lokale Analyse vorbereiten“. Noch kein Modellstart, kein Consent, kein Provider.

## Verbindliche Einschränkungen (Phase 7)

Nicht unterstützt und kontrolliert blockiert (keine stille Konvertierung):

- HEIC/HEIF
- 16-Bit-TIFF
- BigTIFF
- Mehrseiten-TIFF
- TIFF mit ICC-Profil
- exotische TIFF-Modi

Keine neuen Medienformate oder Profile ohne eigenen freigegebenen Auftrag.

## Aktueller Teststand

Nach Phase-8B-Härtung (`0d8ec2f060107a1720c750b11e4372c3529a2c61`):

- 2761 gesammelt
- 2742 bestanden
- 18 fehlgeschlagen
- 1 übersprungen

Hinweise:

- VFR-End-to-End-Smoke: `NOT_EXECUTABLE` / skipped; Vertragsprüfung (Sekundenwerte, kein `-r`) PASS
- die 18 Baseline-Fehler liegen außerhalb Discovery-Analysis-Prepare
  (cut_plan, voiceover_generation, timing, Gemini retry)

Neue Discovery-, Routing-, Classic- oder Without-VO-Fehler sind nicht zulässig.

### Historische Baselines

Nach Phase-8A-Härtung (`11e5cf8ae96e38cd9686cd7d915ad85b66be98e9`):

- 2687 gesammelt
- 2669 bestanden
- 18 fehlgeschlagen
- 0 übersprungen

Nach Phase-7-Abschlusshärtung (`3ba0716a6d2cc12a4e0fdf9f61b5c2dcdead5016`):

- 2649 gesammelt
- 2631 bestanden
- 18 fehlgeschlagen

## Nächster erlaubter Schritt

Nur:

Phase 8C — zentraler multimodaler Vision-Gateway, Fake-Adapter, strukturierte
Visual Observations und explizite Nutzerfreigabe gemäß Plan unten.

Noch nicht erlaubt:

- Phase 8C Produktcode ohne eigenen Umsetzungsauftrag
- echte Provider-Smokes ohne separate Nutzerfreigabe
- Dramaturgie
- Visual Beats
- Kapitel
- Skript
- Karten
- OTIO-Export
- HEIC-/HEIF-Unterstützung
- neue Video-/Audio-/Bildprofile

---

## Phase 8C – Implementierungsplan (PLANNING ONLY)

Stand: dokumentiert im Handoff; **nicht implementiert**.

### Ziel

Assetanalyse erhält einen **zentralen multimodalen Discovery Vision Gateway**:

```text
UI / Application (Model-Analysis-Service)
  → Discovery Vision Gateway
      → FakeVisionAdapter (verpflichtend zuerst)
      → optional ein realer Provideradapter (nur wenn Codebasis ihn trägt)
```

Keine direkten Provideraufrufe aus UI, Domain oder Prepare-Worker.

### Untersuchte Infrastruktur

Gelesen / ausgewertet:

- `otio_app/services/plan_llm_client.py` — Text-LLM-Router (Gemini/OpenAI/Anthropic/xAI/OpenRouter), nur Text
- `otio_app/services/gemini_client.py` — klassische Frame→Gemini-Bildteile (`Part.from_bytes`, JPEG)
- `otio_app/services/api_keys.py`, `otio_app/api_providers.py`, `otio_app/config.py`, `otio_app/defaults.py`
- Classic `asset_analyzer.py` / `frame_extract.py` (nur Referenz)
- Discovery Prepare: Domain, Repository, Worker, UI, Frame-Pfade

### Wiederverwendbar

- API-Key-Laden / Runtime-Overrides / Settings-UI
- Provider-ID-Präfixe und Key-Mapping aus `plan_llm_client`
- Discovery Representative Frames + Hashes + Identity als alleiniger Upload-Input
- Analysis-Run-/Worker-/Recovery-Muster aus Phase 8B
- Gemini-Bildteil-Muster aus `gemini_client.py` als **Adapter-Referenz**, nicht als Gateway

### Nicht wiederverwendbar / Classic-only

- Classic `asset_analyzer.py`-Orchestrierung und Inventory-Cache
- Classic `frame_extract.py` als Discovery-Input
- direkter Gemini-Aufruf aus Fachmodulen
- Text-only `plan_llm_client` als Multimodal-Gateway erweitern *(Entscheidung: nein)*

### Gateway-Architektur (Entscheidung)

**Neuen Discovery-Vision-Gateway neben dem Text-Router etablieren**, nicht
`plan_llm_client` multimodal aufblasen.

Geplante Module (Umsetzungsauftrag):

- `otio_app/discovery_v2/application/model_analysis_service.py`
- `otio_app/discovery_v2/adapters/vision_gateway.py`
- `otio_app/discovery_v2/adapters/vision_fake.py`
- optional später: `otio_app/discovery_v2/adapters/vision_gemini.py`
- `otio_app/discovery_v2/jobs/model_analysis_worker.py`
- `otio_app/discovery_v2/domain/visual_observation.py`
- Persistenz-Erweiterungen in `asset_analysis_repository` / Schema 13

Gateway-Verantwortlichkeiten:

- Capability `vision`
- gemeinsame Konfiguration (Provider, Modell-ID, Limits, Prompt-/Schema-Version)
- Request-Objekt: Textprompt + geordnete Frame-Parts (MIME, bytes/path resolve, frame_id, hashes)
- Response: untrusted JSON → Pydantic `VisualObservation`
- kein Secret in Logs/Berichten
- Verhalten ohne konfigurierten Provider:
  - Fake-Adapter bleibt nutzbar für Tests und lokale Entwicklung
  - realer Providerlauf → `analysis_gateway_unconfigured` / `vision_model_unavailable`

### Providerumfang (erste Implementierung)

1. **FakeVisionAdapter** — verpflichtend, vollständig, deterministisch, offline
2. **Genau ein realer Adapter nur wenn ohne Spekulation möglich:**
   - **Gemini** ist der einzige Provider mit vorhandenem Bild-Upload-Code
     (`gemini_client.describe_media_from_frames`)
   - daher: optional `VisionGeminiAdapter` hinter manueller/deaktivierter Konfiguration
   - **kein** automatischer Produktiv-Upload in Phase 8C ohne separate Nutzerfreigabe

UNKNOWN (nicht als wired behaupten):

- OpenAI Vision Payload
- Anthropic Image Content Blocks
- OpenRouter Vision
- xAI Vision

Text-Routing dieser Provider bleibt unberührt.

### Visual-Observation-Schema

Pydantic-Modell `VisualObservation` (Schema-Version z. B. `visual-observation-v1`):

Pflichtfelder mindestens:

- `summary: str`
- `visible_subjects: list[str]`
- `actions: list[str]`
- `setting: str | None`
- `indoor_outdoor: Literal["indoor","outdoor","mixed","unknown"]`
- `day_night: Literal["day","night","mixed","unknown"]`
- `people_present: bool | None`
- `crowd_level: Literal["none","few","many","crowd","unknown"]`
- `camera_scale: Literal["extreme_closeup","closeup","medium","wide","aerial","unknown"]`
- `camera_motion_hint: Literal["static","pan","tilt","handheld","tracking","unknown"]`
- `visual_quality_notes: list[str]`
- `readable_text_present: bool | None`
- `readable_text_summary: str | None`
- `possible_location_clues: list[str]`
- `geographic_confidence: float`  # 0..1, Hinweis nur
- `landmark_candidates: list[str]`
- `weather_visible: str | None`
- `safety_or_sensitive_content: list[str]`
- `possible_synthetic_indicators: list[str]`
- `synthetic_confidence: float`  # 0..1, Hinweis nur
- `uncertainty_notes: list[str]`
- `evidence_frame_ids: list[str]`
- `editorial_signals: list[str]`

Regeln:

- unbekannt bleibt unbekannt (`unknown` / `null` / leere Listen)
- Geo- und Synthetic-Angaben nur Hinweise mit Konfidenz, keine redaktionelle Wahrheit
- jede Evidence-ID muss zu persistiertem Representative Frame derselben Analysis Identity gehören
- zusätzliche Felder / Pfade / Befehle → `model_response_schema_mismatch`
- Modelloutput ist untrusted und wird strikt validiert

### Persistenz und Schema 13

Voraussichtliche Schema-Erhöhung: **12 → 13**.

Nur diese neuen Tabellen:

1. `visual_observations`
2. `model_analysis_attempts`
3. `analysis_consent_events`

Keine vorsorglichen Zusatztabellen.

Historische Versionierung mindestens über:

- `analysis_identity_id`
- `gateway_version`
- `provider`
- `model_identifier`
- `prompt_version`
- `response_schema_version`

JSON-Artefakte unter:

`_otio_v2/analysis/observations/<analysis_identity_id>/...`

sowie Runberichte für Model-Runs (`scope = model` bzw. `analysis_model_only` —
finale Scope-Konstante im Umsetzungsauftrag festlegen; Phase 8B hat
`ANALYSIS_RUN_SCOPE_MODEL = "model"` vorbereitet).

Maximal ein aktiver Analysis-Run pro Projekt bleibt gültig (Prepare und Model
gegenseitig sperren).

### Nutzerfreigabe (MANUAL)

Standard:

1. lokale Vorbereitung muss für die gewählten Assets `prepared` sein
2. UI zeigt exakte Anzahl der zu übertragenden Frames (und Summe Bytes)
3. Nutzer bestätigt explizit externe Verarbeitung
4. Zustimmung gilt **nur für diesen Model-Run**
5. kein automatischer Upload, kein Streamlit-Rerun-Start
6. kein vollständiges Video, keine Working-Media-Videos, keine Originale
7. keine Übernahme alter Consent-Events
8. keine Secrets persistieren

Fehler ohne Consent: `analysis_consent_required`.

### Frame- und Run-Limits

- nur persistierte Representative Frames
- max. **24 Frames je Video-Asset** (bereits Prepare-Cap)
- globale Run-Obergrenze (Vorschlag zur Umsetzung): **96 Frames / Run**
- Dateigrößenlimit je Frame (Vorschlag): **8 MiB**
- Dateigrößenlimit je Run (Vorschlag): **64 MiB**
- bei Überschreitung: `analysis_frame_limit_exceeded`
- fehlende/veränderte Frames: `analysis_frame_missing` / `analysis_frame_hash_mismatch`

Exacte Zahlen im Umsetzungsauftrag als Konstanten festnageln; Werte hier sind
Planungsvorschläge, nicht Produktdefaults.

### Retry und Caching

- höchstens **zwei** kontrollierte Wiederholungen nach transienten Providerfehlern
- keine Endlosschleife
- identische Modellanalyse (gleiche Identity + Provider + Modell + Prompt- + Schema-Version + Frame-Hash-Satz) → Cache / `reused`
- neue Prompt-/Modell-/Schema-Version → neue Attempt-Identität
- Providerfehler und Schemafehler getrennt
- nach Retry-Erschöpfung: `analysis_retry_exhausted`
- keine Secrets in Logs/Berichten

### UI (Phase 8C)

Auf **Assetanalyse** ergänzen:

- Provider/Modell nur aus zentraler Konfiguration (keine hart codierten IDs in UI)
- Frameanzahl und Hinweis auf externe Verarbeitung vor Start
- expliziter Button z. B. „Modellanalyse starten“
- Consent-Checkbox nur für den aktuellen Run
- persistierter Fortschritt / Attempt-Status
- strukturierte Observations inkl. Konfidenz und Unsicherheit
- weiterhin: kein FFmpeg/ffprobe/Pillow/Hash/API beim Rendering

### Fehlercodes (mindestens)

- `analysis_consent_required`
- `analysis_gateway_unconfigured`
- `vision_model_unavailable`
- `analysis_frame_missing`
- `analysis_frame_hash_mismatch`
- `analysis_frame_limit_exceeded`
- `provider_auth_failed`
- `provider_timeout`
- `provider_rate_limited`
- `model_response_invalid`
- `model_response_schema_mismatch`
- `analysis_retry_exhausted`
- `analysis_artifact_write_failed`
- `analysis_registry_write_failed`
- `worker_interrupted`

### Testplan (konkrete Gruppen)

1. zentrale Gateway-Nutzung — keine direkten Providerimporte in UI/Domain/Worker
2. Fake-Adapter End-to-End — Observation persistiert ohne Netz
3. keine hart codierten Modelle in Fachmodulen
4. explizite Nutzerfreigabe erforderlich
5. Rerun startet keinen Model-Job
6. nur persistierte Frames; Originale/Working-Videos blockiert
7. Frame- und Run-Limits
8. Pydantic-Validierung gültiger/ungültiger Responses
9. ungültige Evidence-IDs abgelehnt
10. Retry-Limit (max. 2)
11. Caching und historische Versionen getrennt
12. Providerfehler ohne Secret-Leak
13. Orphan-Recovery (`worker_interrupted`)
14. UI-No-I/O beim Rendering
15. keine Dramaturgie-/Visual-Beat-Felder oder -Tabellen

### Implementierungsaufteilung (empfohlen)

1. **8C-A Contracts + Schema 13**
   Domain `VisualObservation`, Consent-/Attempt-Verträge, Tabellen, Pfade, Fake-Gateway-Interface

2. **8C-B Fake End-to-End**
   Model-Service, Worker, Consent-UI, Fake-Adapter, Persistenz, Reports, Tests 1–11/14–15

3. **8C-C Optional Gemini-Adapter (deaktiviert)**
   Nur hinter Konfiguration; kein automatischer Smoke; MIME korrekt (JPEG/PNG); Tests 3/12

4. **8C-D Hardening**
   Recovery, Limits, Cache-Kanten, Secret-Leak-Guards

Phase 8D bleibt separat: Review-UI-Feinschliff, Caching-UX, Nutzer-Smoke mit echter Freigabe.

### Risiken

- Gemini-Classic sendet Frames derzeit immer als `image/jpeg` — Discovery-PNG-Stills brauchen korrekte MIME
- Consent-/Kostenkommunikation muss klar von lokaler Vorbereitung getrennt bleiben
- Provider-Vision außerhalb Gemini ist UNKNOWN und darf nicht spekulativ verdrahtet werden
- Mutual exclusion Prepare↔Model-Run muss in Startgates beider Services erzwungen werden

### UNKNOWN-Punkte

- OpenAI / Anthropic / OpenRouter / xAI Vision-Payloads im bestehenden Code: **nicht vorhanden**
- produktive Default-Modell-ID für Discovery Vision: noch festzulegen (nicht aus Classic übernehmen ohne Auftrag)
- endgültige numerische Run-/Byte-Limits: Vorschläge oben, Freigabe im Umsetzungsauftrag
- exakter Model-Run-Scope-String (`model` vs. `analysis_model_only`): im Code derzeit Alias `model`
- ob Gemini-Adapter in 8C-C überhaupt aktiviert werden darf: nur mit separater Nutzerfreigabe

---

## Phase 9 – Editorial Core / Coverage (Fake, implementiert)

- Schema **15**: Editorial-Tabellen (`project_briefs`, `narrative_plans`,
  `hook_variants`, `script_drafts`, Sentences/Claims/Beats/Intents,
  `coverage_audits`, Runs/Attempts, `editorial_project_state`)
- Pfad: UI → Editorial Services → DiscoveryTextGateway → FakeTextAdapter
- Artefakte unter `_otio_v2/editorial/`
- MANUAL-Seite **Editorial**; Observation-Inputs nur editorial-ready
- Entscheidungen: D-9-001 … D-9-006
- Plan: `docs/source_plans/PHASE9_EDITORIAL_CORE_COVERAGE_PLAN.md`

## Phase 10 – Supplementation / Script Lock (Fake, implementiert)

- Schema **16**: `coverage_gaps`, `coverage_gap_events`, `supplementation_runs`,
  `supplementation_attempts`, `supplementation_requests`, `stock_search_attempts`,
  `stock_candidates`, `stock_candidate_decisions`, `claim_decisions`,
  `graphic_plans`, `script_locks`, `script_lock_risks`;
  `editorial_project_state.current_script_lock_id`
- Pfad Suche: UI → Supplementation Service → StockSearchGateway → FakeStockSearchAdapter
- Pfad Lock: UI → ScriptLock Service → Domainvalidierung → Persistence
- Candidate/Preview ≠ Working Media; Original nur über normalen Media Intake
- Eskalation append-only; Script Lock = unveränderlicher Fingerprint-Snapshot
- Entscheidungen: D-10-001 … D-10-008
- Plan: `docs/source_plans/PHASE10_SUPPLEMENTATION_SCRIPT_LOCK_PLAN.md`
- Keine echte Adobe-/Stockintegration

## Phase 11 – Voice / Pause / Timing (Fake, implementiert)

- Schema **17** (ursprünglich) → Contract Hardening R1 → Schema **18**
  (keine neuen Tabellen; ergänzte Spalten an `voice_profiles`, `voice_segments`,
  `pause_directions`; `current_timeline_id` unverändert)
- Tabellen: `narration_project_state`, `voice_profiles`,
  `voice_generation_runs`/`attempts`, `voice_segments`,
  `pause_direction_plans`/`pause_directions`,
  `narration_timelines`/`narration_timeline_entries`
- Voice: UI → Voice Service → VoiceGenerationGateway → FakeVoiceAdapter
- Pause: UI → Pause Service → DiscoveryTextGateway (`pause_direction`) → FakeTextAdapter
- Timing: UI → Narration Timing Service → deterministischer Python Resolver
- Artefakte unter `_otio_v2/narration/`; Narration-Audio ≠ Working Media
- Permanente Pause-Referenzfehler ohne Adapter-Retry (D-11-010)
- Entscheidungen: D-11-001 … D-11-011
- Plan: `docs/source_plans/PHASE11_VOICE_PAUSE_TIMING_PLAN.md`
- Kein ElevenLabs; keine Phase-12-Funktion; Phase 12 nicht begonnen

## Verbleibender Fahrplan

1. ~~Phase 7 Media Intake~~ — APPROVED
2. ~~Phase 8A Analysis Contracts~~ — APPROVED
3. ~~Phase 8B lokale Shot-/Frame-Vorbereitung~~ — APPROVED
4. ~~Phase 8C Fake Vision~~ — APPROVED
5. ~~Phase 8D Observation Review / Closeout~~ — APPROVED
6. ~~Phase 9 Editorial Core / Coverage (Fake)~~ — implementiert
7. ~~Phase 10: Supplementation und Script Lock (Fake)~~ — implementiert
8. ~~Phase 11: Voice, Pausen und Timing (Fake)~~ — implementiert
9. Phase 12: Visual Edit Plan und Quality (**planen** nach Freigabe)
10. Phase 13: Review und OTIO

Ausführungsreihenfolge und Gates: `docs/ALPHA_EXECUTION_MANIFEST.md`
(untergeordnet gegenüber Regeln, DECISIONS, MASTER_PLAN, ALPHA_SCOPE, …).

## Arbeitsdisziplin

Cursor muss vor jeder Arbeit die Source-of-Truth-Hierarchie und den aktuellen
Auftrag prüfen (siehe `.cursor/rules/01-step-discipline.mdc`).

Nur diesen Auftrag umsetzen.

Keine Folgephase beginnen.

Nach Abschluss berichten:

1. Auftrags-ID
2. Zusammenfassung
3. neue/geänderte/gelöschte Dateien
4. Git-Diff
5. Architektur- und Schemaänderungen
6. Befehle
7. Tests
8. fehlgeschlagene Tests
9. Smoke-Test
10. Risiken
11. Abweichungen
12. Classic unverändert
13. keine Secrets
14. keine Folgephase begonnen
