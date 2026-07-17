> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Classic Migration Contract — Discovery V2

Vertrag zwischen Discovery V2 und den bestehenden Pipelines Classic (With-VO) sowie Without-VO.

## Parallelbetrieb

- Drei Modi: `with_voiceover`, `without_voiceover`, `discovery_v2`
- Modus wird bei Projektanlage gesetzt und nicht gemischt
- Discovery ist Greenfield-Unterbaum `otio_app/discovery_v2/`, keine Big-Bang-Migration der Classic-UI

## Unveränderlichkeitsvertrag

Discovery darf nicht:

- Classic- oder Without-VO-Fachverhalten ändern
- unter `_otio/` schreiben, ändern oder löschen
- Originalmedien mutieren
- Classic-Working-Media oder Classic-Caches als Discovery-Schreibziel verwenden

Discovery darf:

- Integrationshaut additiv erweitern (Mode, Routing, Navigation, Labels, Unique-Index inkl. `project_mode`)
- Adapter-Ideen aus Classic lesen (FFmpeg, OTIO-Export, Key-Laden) und **neu** hinter Discovery-Verträgen verdrahten

## Erlaubte Wiederverwendung (Adapter-Ebene)

Typischerweise wiederverwendbar nach Entkopplung:

- FFmpeg/ffprobe-Muster, Frame-Extraktion als Referenz
- API-Key-/Settings-Infrastruktur
- OTIO-Exporter-Kern nach Entkopplung von Classic-Staging/Bridge
- Stock-Client-Bausteine — Preview-first-Soll bleibt **UNKNOWN**/Gate

Nicht wiederverwenden als Orchestrierung:

- Classic `asset_analyzer.py`-Pipeline
- Classic Inventory-Cache als Discovery-Wahrheit
- Text-only `plan_llm_client` als Vision-Gateway

## Testschutz

- Classic- und Without-VO-Seitennamen/Navigationslisten bleiben verhaltenidentisch
- Neue Discovery-Tests dürfen keine neuen Discovery-bedingten Suite-Fehler erzeugen
- Fremde Baseline-Failures (außerhalb Discovery) werden ohne Auftrag nicht repariert

## Externe Audits

`/workspace/DISCOVERY_V2_PHASE1_AUDIT-001.md` und `/workspace/OTIO_WITHOUT_VO_IST_BERICHT.md` sind Vergleichs- und Belegquellen, **keine** höhere Source of Truth.
