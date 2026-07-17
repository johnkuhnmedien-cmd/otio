# Discovery V2 – Handoff

## Repository-Stand

- Branch: `cursor/discovery-v2-integration`
- PR: `#69`
- Ursprüngliche Basis: `7187d163a5959351a1ee79f5c931b0d651e21d49`
- Aktiver Auftrag: `DISCOVERY-V2-PHASE7B-COPY-INTAKE-001-R1`

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
- FFmpeg/ffprobe nur über Adapter
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
    Kurz-Commit: `da7dbbf`  
    Vollständigen Hash später mit `git rev-parse da7dbbf` ergänzen.

11. Copy-Intake  
    Kurz-Commit: `1b59023`  
    Noch nicht freigegeben.

## Implementierter Ablauf

Projekt anlegen  
→ Inventory  
→ Auswahl bestätigen  
→ Asset Registry  
→ technische Validierung  
→ SHA-256  
→ ffprobe  
→ Dublettenhinweise  
→ Intake-Plan  
→ Copy-Intake

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

SQLite:

`_otio_v2/registry/assets.sqlite3`

SQLite ist interne Wahrheit.

Historische Imports, Validierungen, Intake-Pläne und Working-Media-Versionen dürfen nicht automatisch gelöscht werden.

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

Fehlender Timecode ist kein Fehler.

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

## Verbindlicher Working-Media-Pfad

Kanonische Copy-Ausgabe:

`_otio_v2/media/working/<asset_id>/<source_sha256>/copy-v1/<asset_id>.<extension>`

Temporäre Datei:

`_otio_v2/media/temp/<run_id>/<asset_id>.tmp.<extension>`

`source_relative_path` ist nur Herkunftsmetadatum und darf nicht der kanonische Zielpfad sein.

Eine neue Quellversion mit neuem SHA-256 erhält eine neue historische Ausgabe.

## Testbaseline vor R1

- 2545 gesammelt
- 2527 bestanden
- 18 bekannte Fehler

Bekannte Bereiche:

- cut_plan
- voiceover_generation
- timing
- Gemini retry

Neue Discovery-, Routing-, Classic- oder Without-VO-Fehler sind nicht zulässig.

## Nächster erlaubter Schritt

Nur:

`DISCOVERY-V2-PHASE7B-COPY-INTAKE-001-R1`

Ziel:

- kanonischen Working-Media-Pfad korrigieren
- historische Versionierung sicherstellen
- Idempotenz und Konflikterkennung härten
- Crash-Fenster nach `os.replace` absichern

Noch nicht erlaubt:

- Remux
- Transkodierung
- Phase 7C
- redaktionelle Analyse
- Dramaturgie
- Kapitel
- Karten
- OTIO-Export

## Verbleibender Fahrplan

1. Phase 7B abschließen
2. Phase 7C: Remux/Transkodierung/Bildkonvertierung
3. Phase 7D: UI-Härtung und Nutzer-Smoke-Test
4. redaktionelle Assetanalyse
5. Projektbrief und Dramaturgie
6. Kapitel und Karten
7. Skript und Visual Beats
8. Coverage Audit
9. Stock-Supplementation
10. Script Lock und ElevenLabs
11. Pausen und Timing
12. Visual Edit Plan
13. Humanity Review
14. Feasibility und Repair
15. Freigabe
16. Discovery-OTIO-Export

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
