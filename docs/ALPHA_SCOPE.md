> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Alpha Scope — Discovery V2

## In Scope

Bereits umgesetzt (Code / Tests / Handoff):

- Projektmodus `discovery_v2` parallel zu Classic und Without-VO
- Working Media unter `_otio_v2/` (Copy, Remux, H.264-Transcode, TIFF→PNG)
- Lokale Assetanalyse inkl. Fake Vision und manuellem Observation Review
- SQLite-Registry Schema **14** + versionierte JSON-Artefakte
- Identity-/Stale-Gates für Selection, Intake und Analysis

Geplant für Alpha (Manifest / D-8D-001; noch nicht implementiert):

- MANUAL-Hauptpfad bis parsebarem OTIO-Export
- Editorial Core, Coverage, Script Lock, Voice/Timing, Visual Edit Plan
- Humanity Review, Feasibility, Nutzerfreigabe, OTIO

## Betriebsstandard

- **MANUAL** = Alpha-Standard — Manifest
- **AUTOMATIC** = post-alpha — Manifest
- Weitere Automatisierungsmodi: **UNKNOWN**

## Explizit Out of Scope / verschoben

Aus Manifest Post-Alpha und Handoff-Sperren:

- AUTOMATIC End-to-End-Orchestrierung
- echte Vision-Provider ohne separates Gate (aktuell Fake-only)
- vollständiges Adobe OAuth — **UNKNOWN**/später
- HEIC/HEIF und exotische TIFF-Varianten
- OCR, Gesichtserkennung, bestätigte Geolokalisierung
- automatische Synthetic-Erkennung
- verteilte Queue, Multi-User, Cloud Storage
- umfassende Performanceoptimierung
- Änderungen an Classic- oder Without-VO-Fachpfaden

## Qualitätsgrenzen Alpha

- keine Originaländerung
- keine `_otio/`-Schreibzugriffe durch Discovery
- nur `completed` Working Media als Analysebasis (implementiert) und als geplante Produktions-/Exportbasis
- keine Secrets in Artefakten oder Logs
- kein ungefragter Upload
- Humanity Review + Feasibility + Nutzerfreigabe vor Export (geplant)
- parsebares OTIO als Alpha-DoD (geplant)

## Eingangsbedingung Phase 9+

Nur editorial-ready Visual Observations — Code Observation-Review-Service / D-8D-004:

- aktuelle Analysis Identity
- aktuelle Vision-Config-Versionen
- gültiges Response-Schema
- aktuelles Review = `accepted`

`accepted` ist keine Asset-, Fakten-, Geo-, Synthetic- oder Visual-Beat-Freigabe.
