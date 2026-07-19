> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Classic Migration Contract — Discovery V2

## Parallelbetrieb

- Modi: `with_voiceover`, `without_voiceover`, `discovery_v2`
- Discovery unter `otio_app/discovery_v2/`; keine Big-Bang-Migration der Classic-UI

## Unveränderlichkeit / Classic read-only

Discovery darf nicht:

- Classic- oder Without-VO-Fachverhalten ändern
- unter `_otio/` schreiben, ändern oder löschen (**Classic read-only**)
- Originalmedien mutieren
- Classic-Arbeitswurzeln als Discovery-Schreibziel verwenden

Discovery darf:

- Integrationshaut additiv erweitern (Mode, Routing, Navigation, Uniqueness)
- Adapter-Muster lesen und neu hinter Discovery-Verträgen verdrahten

## Wiederverwendung

Referenz OK: FFmpeg/ffprobe-Muster, Key-/Settings-Infrastruktur, OTIO-Exporter-Kern nach Entkopplung.

Nicht als Discovery-Orchestrierung: Classic `asset_analyzer.py`, Classic Inventory-Cache,
Text-only `plan_llm_client` als Vision-Gateway.

Adobe-OAuth-Variante und Lizenzfluss-Details: **UNKNOWN**; Reihenfolge siehe
`docs/PIPELINE_SPEC.md` / `docs/MEDIA_LIFECYCLE.md`.

## Testschutz

- Classic-/Without-VO-Navigation verhaltenidentisch
- keine neuen Discovery-bedingten Suite-Fehler
- Baseline-Failures außerhalb Discovery ohne Auftrag nicht reparieren

## Audit-Referenzen

`DISCOVERY_V2_PHASE1_AUDIT-001.md` und `OTIO_WITHOUT_VO_IST_BERICHT.md` dienen nur
der Abgrenzung Classic/Without-VO — keine höhere Discovery-SoT.
