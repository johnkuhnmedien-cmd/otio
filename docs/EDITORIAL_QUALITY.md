> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Editorial Quality — Discovery V2

## Grundsatz

LLM: redaktionelle Entscheidungen inkl. LLM-Pausenregie.
Python: exakte Zeitauflösung, Dauern, Überlappungen, Framerundung, technische Konsistenz.
Alpha: MANUAL; CHECKPOINT vorbereitet; AUTOMATIC post-alpha.

## Observation Review (implementiert)

- `accepted` / `reanalyze_requested` / `rejected` / sonst `unreviewed`
- Reviews append-only mit `review_revision`
- Visual Observation ist **keine** Fakten-, Geo-, Echtheitsbestätigung und **keine** automatische Assetauswahl

## Coverage und Supplementation (geplant)

- Coverage Audit vor Stock-Supplementation
- Stock-Eskalation verbindlich (siehe `PIPELINE_SPEC.md` / `MEDIA_LIFECYCLE.md`)
- kein beliebiges Ersatzasset

## Script Lock und Timing (geplant)

- keine finale Voice vor Script Lock
- LLM-Pausenregie vor Python-Timingauflösung
- keine Visual-Edit-Timeline vor finalem Narration Timing

LLM-Pausenregie umfasst: Pausenfunktion, Cold Open, visuelle Atemzüge,
Übergangspausen und andere redaktionelle Pausenentscheidungen.

## Humanity & Authenticity Review (eigener Schritt)

Mindestens prüfen:

- Hook-Qualität
- Skriptvariation
- lokale Details
- Klischees und Faktenlisten
- generischer Stock-Anteil
- geografische Genauigkeit
- Asset-Wiederholung
- Schnitte an Satzgrenzen
- Shotdauer-Varianz
- ähnliche Motive in Folge
- visuelle Kontinuität
- möglicherweise synthetische Assets

Humanity & Authenticity bleibt ein **eigener** Review-Schritt vor Freigabe/Export.

## Feasibility und Repair

- Python: deterministische technische Repairs
- LLM: redaktionelle Reparaturvorschläge
- Export erst nach bestandener Feasibility und Nutzerfreigabe

## NEGATIVE_REFERENCE

Hinterlegte KI-Timelines sind ausschließlich `NEGATIVE_REFERENCE` —
keine Qualitätsvorlage; keine positive Schnitt- oder Dramaturgieregel ableiten.

## Qualitätsverbote

- keine stillen Automationen im Alpha (MANUAL)
- Shot ≠ Satz als stilles 1:1 (Many-to-Many geplant)
- keine OTIO-Medien außerhalb completed Working Media
- Classic `_otio/` read-only
