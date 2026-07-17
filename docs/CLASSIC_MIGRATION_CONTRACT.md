> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Classic Migration Contract — Discovery V2

Vertrag zwischen Discovery V2 und den Pipelines Classic (`with_voiceover`) sowie
Without-VO (`without_voiceover`) in diesem Repository.

## Parallelbetrieb (belegt)

- Drei Modi: `with_voiceover`, `without_voiceover`, `discovery_v2` — Code / Handoff
- Modus bei Anlage gesetzt; kein Mischmodus — Handoff / Audit-Abgrenzung
- Discovery-Code unter `otio_app/discovery_v2/`; keine Big-Bang-Migration der Classic-UI — Code-Lage

## Unveränderlichkeitsvertrag (belegt)

Discovery darf nicht:

- Classic- oder Without-VO-Fachverhalten ändern
- unter `_otio/` schreiben, ändern oder löschen
- Originalmedien mutieren
- Classic-Arbeitswurzeln als Discovery-Schreibziel verwenden

Discovery darf:

- Integrationshaut additiv erweitern (Mode, Routing, Navigation, Labels, Uniqueness inkl. `project_mode`)
- Adapter-Muster aus Classic lesen und neu hinter Discovery-Verträgen verdrahten

## Wiederverwendung

Erlaubt als Referenz/Adapter-Idee (Handoff Phase-8C-Planung):

- FFmpeg/ffprobe-Muster
- API-Key-/Settings-Infrastruktur
- OTIO-Exporter-Kern nach Entkopplung von Classic-Staging (Export in Discovery noch nicht implementiert)

Nicht als Discovery-Orchestrierung übernehmen:

- Classic `asset_analyzer.py`-Pipeline
- Classic Inventory-Cache als Discovery-Wahrheit
- Text-only `plan_llm_client` als Vision-Gateway

Stock-/Adobe-Lizenz- und Preview-Flüsse für Discovery: **UNKNOWN**.

## Testschutz

- Classic-/Without-VO-Navigation verhaltenidentisch halten — bestehende Routing-Tests / Handoff
- Keine neuen Discovery-bedingten Suite-Fehler
- Baseline-Failures außerhalb Discovery ohne Auftrag nicht reparieren

## Audit-Referenzen

Die Dateien `DISCOVERY_V2_PHASE1_AUDIT-001.md` und `OTIO_WITHOUT_VO_IST_BERICHT.md`
dienen nur der Abgrenzung Classic/Without-VO. Sie sind keine höhere Source of Truth
für Discovery V2.
