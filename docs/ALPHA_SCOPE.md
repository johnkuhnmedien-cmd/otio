> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Alpha Scope — Discovery V2

Was Alpha leisten muss und was ausdrücklich ausgeschlossen ist.

## In Scope (Alpha)

- Dritter Projektmodus `discovery_v2` parallel zu Classic und Without-VO
- MANUAL-Hauptpfad von Inventory bis parsebarem OTIO-Export
- Working Media unter `_otio_v2/` (Copy, Remux, H.264-Transcode, TIFF→PNG)
- Lokale Assetanalyse inkl. Fake Vision und manuellem Observation Review
- Editorial Core, Coverage, Script Lock, Voice/Timing, Visual Edit Plan, Humanity Review, Feasibility, Freigabe, OTIO
- SQLite-Registry + versionierte JSON-Artefakte
- Stale-Gates und Identity-Bindungen
- pytest-Abdeckung neuer Discovery-Pfade ohne neue Discovery-bedingte Suite-Regressionen

## Betriebsstandard

- **MANUAL** = Alpha-Standard
- **CHECKPOINT** = vorbereitet für explizite Zwischenfreigaben
- **AUTOMATIC** = post-alpha

## Explizit Out of Scope (Alpha / Post-Alpha)

Mindestens verschoben:

- AUTOMATIC End-to-End-Orchestrierung
- echte Vision-Provider ohne separates Gate (aktuell Fake-only)
- vollständiges Adobe OAuth / License-before-Preview-Korrekturen als Discovery-Default (**UNKNOWN**/später)
- HEIC/HEIF und exotische TIFF-Varianten
- OCR, Gesichtserkennung, bestätigte Geolokalisierung
- automatische Synthetic-Erkennung
- verteilte Queue, Multi-User, Cloud Storage
- umfassende Performanceoptimierung
- Änderungen an Classic- oder Without-VO-Fachpfaden

## Qualitätsgrenzen Alpha

- keine Originaländerung
- keine `_otio/`-Schreibzugriffe durch Discovery
- nur `completed` Working Media für Analyse/Produktion/Export
- keine Secrets in Artefakten oder Logs
- kein ungefragter Upload
- Humanity Review + Feasibility + Nutzerfreigabe vor Export
- parsebares OTIO als Alpha-DoD

## Eingangsbedingung für Editorial (Phase 9+)

Nur editorial-ready Visual Observations:

- aktuelle Analysis Identity
- aktuelle Vision-Config-Versionen
- gültiges Response-Schema
- aktuelles Review = `accepted`

`accepted` ist keine Asset-, Fakten-, Geo-, Synthetic- oder Visual-Beat-Freigabe.
