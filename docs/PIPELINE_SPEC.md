> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Pipeline Spec — Discovery V2

Technische Spezifikation der Discovery-Pipeline-Stufen und ihrer Gates.

## Schichten und Module

```text
otio_app/discovery_v2/
  ui/            # Streamlit, No-I/O außer expliziten Button-Starts
  application/   # Services, Eligibility, Recovery
  domain/        # Pydantic-Verträge und Regeln
  adapters/      # FFmpeg, Probe, Copy, Vision Gateway, …
  persistence/   # SQLite + JSON stores
  jobs/          # Worker außerhalb Streamlit-Reruns
```

## Stufe A — Inventory und Selection

- rekursiver read-only Scan
- relative Pfade; oberster Ordner = `source_group` (kein Kapitel)
- Artefakte unter `_otio_v2/inventory/`
- Selection an `scan_id` gebunden; neuer Scan macht alte Selection stale

## Stufe B — Registry und Validierung

- SQLite: `_otio_v2/registry/assets.sqlite3` (Schema laut Code/`REGISTRY_SCHEMA_VERSION`)
- historische Imports/Validierungen/Pläne/Working-Media/Analysis-Daten nicht automatisch löschen
- technische Validierung im Worker: Größe, mtime, SHA-256, Container, Codecs, Auflösung, rationale Framerate, Timecode oder `null`, Pixel-Format, Bit-Tiefe, Dublettenhinweise
- fehlender Timecode ist kein Fehler

## Stufe C — Media Intake

Freigegebene Aktionen/Profile:

| Aktion | Profil |
|---|---|
| bytegenaue Kopie | `copy-v1` |
| Container-Remux | `remux-mp4-v1` |
| H.264-Video-Transkodierung | `video-h264-v1` |
| TIFF→PNG | `image-png-v1` |

Regeln:

- gegenseitige Run-Sperre über Intake-Scopes
- Orphan → `worker_interrupted`
- Idempotenz/Reuse ohne Binärdubletten; Konflikte nie überschreiben
- Copy/Remux nur bei kompatiblem Codec/Container und `yuv420p`/`yuvj420p` + 8-bit; sonst Transcode/Block

Kanonische Working-Media-Pfade:

```text
_otio_v2/media/working/<asset_id>/<source_sha256>/<profile>/<asset_id>.<ext>
_otio_v2/media/temp/<run_id>/...
```

## Stufe D — Assetanalyse

1. **Contracts / Eligibility** — nur Working Media Rohstatus `completed` + exakte Planbindung
2. **Prepare** — Technical Shots, Representative Frames, lokale Signale (`analysis-prepare-v1`)
3. **Model Analysis** — Discovery Vision Gateway → FakeVisionAdapter (echte Provider gesperrt)
4. **Observation Review** — append-only Reviews; Editorial-Ready-Gate

Audio-only: visuelle Analyse `not_applicable`.

## Stufe E — Editorial bis Export (geplant)

Siehe `docs/MASTER_PLAN.md` Phasen 9–13. Eingabe aus Stufe D nur editorial-ready Observations.

## UI-No-I/O

Beim normalen Streamlit-Rendering verboten:

- ffprobe / FFmpeg / Pillow `Image.open`
- Hashing / Medien-`stat`/mtime
- automatischer Jobstart durch Rerun
- Provider-/API-Aufrufe

## Blockierte Formate (Phase 7+)

Kontrolliert blockiert (keine stille Konvertierung): HEIC/HEIF, 16-Bit-TIFF, BigTIFF, Mehrseiten-TIFF, TIFF mit ICC, exotische TIFF-Modi.
