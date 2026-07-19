> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Alpha Scope — Discovery V2

## In Scope

Umgesetzt:

- Modus `discovery_v2` parallel zu Classic / Without-VO
- Working Media unter `_otio_v2/`
- Fake Vision + Observation Review; Registry Schema **14**
- Identity-/Stale-Gates für Inventory/Intake/Analysis

Geplant (MANUAL-Hauptpfad):

- Editorial Core, Coverage, Stock-Supplementation, Script Lock
- ElevenLabs hinter Gate, LLM-Pausenregie, Python-Timing
- Visual Edit Plan, Humanity & Authenticity Review, Feasibility, Freigabe, OTIO

## Betriebsarten

- **MANUAL** = Alpha-Standard
- **CHECKPOINT** = architektonisch vorbereitet; nicht als vollständig implementiert behaupten
- **AUTOMATIC** = Post-Alpha / spätere Phase

## Out of Scope / verschoben

- AUTOMATIC End-to-End-Orchestrierung
- echte Vision-Provider ohne Gate (aktuell Fake-only)
- konkrete Adobe-OAuth-Variante (**UNKNOWN**)
- HEIC/HEIF und exotische TIFF-Varianten
- OCR, Gesichtserkennung, bestätigte Geolokalisierung
- automatische Synthetic-Erkennung
- verteilte Queue, Multi-User, Cloud Storage
- Classic-/Without-VO-Fachänderungen

## Qualitätsgrenzen

- keine Originaländerung; Classic `_otio/` read-only
- nur completed Working Media für Produktion und OTIO-Export
- Stock Preview ist niemals Working Media
- keine finale Voice vor Script Lock
- keine Visual-Edit-Timeline vor finalem Narration Timing
- Humanity & Authenticity als eigener Review-Schritt
- keine Secrets; kein ungefragter Upload
- hinterlegte KI-Timelines = `NEGATIVE_REFERENCE`

## Eingang Phase 9+

Nur editorial-ready Visual Observations. Eine Visual Observation ist:

- keine Faktenbestätigung
- keine geografische Bestätigung
- keine Echtheitsbestätigung
- keine automatische Assetauswahl
