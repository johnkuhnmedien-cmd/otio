> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Pipeline Spec — Discovery V2

Technische Stufen der Discovery-Pipeline in diesem Repository.

## Module (Code)

```text
otio_app/discovery_v2/
  ui/
  application/
  domain/
  adapters/
  persistence/
  jobs/
```

## Stufe A — Inventory und Selection (implementiert)

- rekursiver read-only Scan
- relative Pfade; oberster Ordner = `source_group` (kein Kapitel)
- Artefakte unter `_otio_v2/inventory/`
- Selection an `scan_id` gebunden; neuer Scan macht alte Selection stale

Beleg: Handoff / Inventory- und Selection-Tests.

## Stufe B — Registry und Validierung (implementiert)

- SQLite: `_otio_v2/registry/assets.sqlite3`
- `REGISTRY_SCHEMA_VERSION = "14"` — Code
- keine automatische Löschung historischer Imports/Validierungen/Pläne/Working-Media/Analysis-Daten
- technische Validierung im Worker (Größe, mtime, SHA-256, Container, Codecs, Auflösung, rationale Framerate, Timecode oder `null`, Pixel-Format, Bit-Tiefe, Dublettenhinweise)
- fehlender Timecode ist kein Fehler

## Stufe C — Media Intake (implementiert, Phase 7 APPROVED)

| Aktion | Profil |
|---|---|
| bytegenaue Kopie | `copy-v1` |
| Container-Remux | `remux-mp4-v1` |
| H.264-Video-Transkodierung | `video-h264-v1` |
| TIFF→PNG | `image-png-v1` |

- gegenseitige Run-Sperre; Orphan → `worker_interrupted`
- Idempotenz/Reuse ohne Binärdubletten; Konflikte nie überschreiben
- Copy/Remux nur bei kompatiblem Codec/Container und `yuv420p`/`yuvj420p` + 8-bit

```text
_otio_v2/media/working/<asset_id>/<source_sha256>/<profile>/<asset_id>.<ext>
_otio_v2/media/temp/<run_id>/...
```

## Stufe D — Assetanalyse (implementiert, Phase 8 APPROVED)

1. Contracts / Eligibility — nur Working Media Rohstatus `completed` + exakte Planbindung
2. Prepare — Technical Shots, Representative Frames, lokale Signale (`analysis-prepare-v1`)
3. Model Analysis — Discovery Vision Gateway → FakeVisionAdapter; echte Provider gesperrt
4. Observation Review — append-only Reviews; Editorial-Ready-Gate

Audio-only: visuelle Analyse `not_applicable`.

## Stufe E — Editorial bis Export (geplant)

Phasen 9–13 laut `docs/MASTER_PLAN.md` / Manifest. Noch kein Produktcode.
Eingabe nur editorial-ready Observations.

## UI-No-I/O (belegt)

Beim normalen Streamlit-Rendering verboten: ffprobe, FFmpeg, Pillow `Image.open`,
Hashing, Medien-`stat`/mtime, automatischer Jobstart durch Rerun, Provider-Aufrufe.

## Blockierte Formate (Phase 7)

HEIC/HEIF, 16-Bit-TIFF, BigTIFF, Mehrseiten-TIFF, TIFF mit ICC, exotische TIFF-Modi —
kontrolliert blockiert, keine stille Konvertierung.
