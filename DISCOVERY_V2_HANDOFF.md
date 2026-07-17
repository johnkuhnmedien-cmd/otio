# Discovery V2 – Handoff

## Repository-Stand

- Branch: `cursor/discovery-v2-integration`
- PR: `#69`
- Ursprüngliche Basis: `7187d163a5959351a1ee79f5c931b0d651e21d49`
- Letzter technisch freigegebener Commit: `11e5cf8ae96e38cd9686cd7d915ad85b66be98e9`
- Phase 7: `APPROVED`
- Phase 8A: `APPROVED`
- Aktiver Produktauftrag: keiner (Analysis-Contracts abgeschlossen)

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

- Analysis-Domainverträge (`AnalysisInputIdentity`, Eligibility, Run/RunAsset, Shot-/Frame-Vertragsformen ohne Produktinstanzen)
- Analysis-Identity-Profil: `analysis-contract-v1`
- Registry-Schema **11**
- Tabellen:
  - `analysis_runs`
  - `analysis_run_assets`
  - `analysis_identities`
- exakte aktuelle Plan-zu-Working-Media-Bindung (keine Profilpriorität, kein `created_at`, kein Pfad)
- Eligibility nur bei Working-Media-Rohstatus `completed`
- read-only Discovery-Seite **Assetanalyse**
- Analysis-Pfadvertrag unter `_otio_v2/analysis/`
- versionierte Analysis-Run-JSON-Verträge (noch ohne produktive Run-Erzeugung aus der UI)

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

Audio-only: `not_applicable` für visuelle Analyse (kein Dummyframe, kein Analysis-Run aus der UI).

### Noch nicht implementiert (Phase 8A)

- keine Shot-Erkennung
- keine Frame-Extraktion
- keine Analyseframes
- keine lokalen Bildsignale
- keine Visual Observations
- kein Vision-Gateway
- keine Providerintegration
- kein Nutzer-Consent
- keine Modellanalyse
- keine Dramaturgie
- keine Visual Beats

### Analysis-Pfade

Ausschließlich unter:

`_otio_v2/analysis/`

Unter anderem:

- `analysis/runs/<run_id>.json`
- `analysis/manifests/<analysis_identity_id>.json`
- `analysis/temp/<run_id>/`
- `analysis/frames/`
- `analysis/observations/`

Analysis-Artefakte sind niemals Working Media und dürfen niemals an OTIO übergeben werden.

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
→ Assetanalyse-Eligibility (read-only)

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

`11`

SQLite:

`_otio_v2/registry/assets.sqlite3`

SQLite ist interne Wahrheit.

Historische Imports, Validierungen, Intake-Pläne, Working-Media-Versionen und Analysis-Identities dürfen nicht automatisch gelöscht werden.

Noch keine Tabellen für:

- `technical_shots`
- `representative_frames`
- `visual_observations`
- `model_analysis_attempts`
- Consent-Events

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
- Bildfelder für TIFF/PNG-Intake (u. a. Modus, Orientierung, ICC-/BigTIFF-/Mehrseiten-Hinweise)

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

Eine neue Quellversion mit neuem SHA-256 erhält eine neue historische Ausgabe.

## UI-Regel

Beim normalen Streamlit-Rendering ist verboten:

- ffprobe
- FFmpeg
- Pillow `Image.open`
- Hashing
- `stat` oder mtime der Medien
- automatischer Jobstart durch Rerun

Die UI liest ausschließlich persistierte Validation-/Registry-/Analysis-Vertragsdaten.

Die Seite **Assetanalyse** ist in Phase 8A read-only: kein Startbutton, kein Provider, kein Modell, kein Consent.

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

Nach Phase-8A-Härtung (`11e5cf8ae96e38cd9686cd7d915ad85b66be98e9`):

- 2687 gesammelt
- 2669 bestanden
- 18 fehlgeschlagen
- 0 übersprungen

Bekannte Baseline-Bereiche (nicht durch Discovery-Analysis-Contracts verursacht):

- cut_plan
- voiceover_generation
- timing
- Gemini retry

Neue Discovery-, Routing-, Classic- oder Without-VO-Fehler sind nicht zulässig.

### Historische Baselines

Nach Phase-7-Abschlusshärtung (`3ba0716a6d2cc12a4e0fdf9f61b5c2dcdead5016`):

- 2649 gesammelt
- 2631 bestanden
- 18 fehlgeschlagen

Vor Copy-Intake-R1:

- 2545 gesammelt
- 2527 bestanden
- 18 bekannte Fehler

## Nächster erlaubter Schritt

Nur:

Phase 8B — lokale technische Shot-Erkennung und repräsentative Frame-Vorbereitung planen beziehungsweise gemäß separatem Auftrag umsetzen.

Noch nicht erlaubt:

- Vision-Gateway
- LLM-Aufrufe
- API-/Provideraufrufe
- externe Bildübertragung
- Visual Observations als Produktlauf
- Visual Beats
- Dramaturgie
- Kapitel
- Skript
- Karten
- OTIO-Export
- HEIC-/HEIF-Unterstützung
- neue Video-/Audio-/Bildprofile

## Verbleibender Fahrplan

1. ~~Phase 7 Media Intake~~ — APPROVED
2. ~~Phase 8A Analysis Contracts~~ — APPROVED
3. Phase 8B: lokale Shot-Erkennung und repräsentative Frames
4. Phase 8C: Vision-Gateway und strukturierte Beobachtungen (nach Freigabe)
5. Phase 8D: Review-UI, Caching, Recovery, Nutzer-Smoke
6. Projektbrief und Dramaturgie
7. Kapitel und Karten
8. Skript und Visual Beats
9. Coverage Audit
10. Stock-Supplementation
11. Script Lock und ElevenLabs
12. Pausen und Timing
13. Visual Edit Plan
14. Humanity Review
15. Feasibility und Repair
16. Freigabe
17. Discovery-OTIO-Export

## Arbeitsdisziplin

Cursor muss vor jeder Arbeit `DISCOVERY_V2_CURRENT_TASK.md` vollständig lesen.

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
