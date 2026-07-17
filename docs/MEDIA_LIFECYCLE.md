> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Media Lifecycle — Discovery V2

## Rollen

| Objekt | Rolle | Discovery-Schreibzugriff |
|---|---|---|
| Originalquelle im Projektbaum | Herkunft | nein |
| Classic `_otio/` | fremde Pipeline | nein (read-only) |
| Working Media | kanonische Analyse-/Produktionsbasis | nur `_otio_v2/media/working/` |
| Analysis Frames | Analysehilfe | `_otio_v2/analysis/`; nicht Working Media |
| Temp | Laufzeit | unter `_otio_v2/**/temp/` |
| OTIO-Referenzen | geplanter Export | nur completed Working Media (Alpha-Ziel) |

## Zustandsfolge (implementiert bis Editorial-Ready)

```text
Source (Inventory)
→ Selected
→ Registered
→ Technically Validated
→ Intake Planned
→ Working Media (… → completed | failed)
→ Analysis Eligible (nur completed)
→ Prepared (shots/frames)
→ Model-Observed (Visual Observation)
→ Review (accepted | reanalyze_requested | rejected)
→ Editorial-Ready (Gate)
```

Spätere Editorial-/Exportzustände: geplant, **UNKNOWN** im Detail bis Phasen 9–13.

## Working Media Regeln (belegt)

- Rohstatus **`completed`** ist Voraussetzung für Assetanalyse-Eligibility — Code
- Identity bindet `project_id`, `asset_id`, Validation/Source-SHA, Action, `processing_profile_version` — Code / Handoff
- historische Versionen bleiben erhalten — Handoff
- `source_relative_path` ist Herkunftsmetadatum, kein kanonischer Zielpfad — Handoff
- Analysis Frames sind weder Working Media noch OTIO-Media — Prepare-Tests / Handoff

## Hash und Pfadprüfung

Python/Worker prüfen SHA-256 und Pfade unter `_otio_v2`; Classic- und Originalpfade als unzulässige Analyse-/Schreibziele — Code / Tests.

## Preview vs. Production

- Analysis Frames: lokal für Vision/Review; keine Produktionsquelle — Code Phase 8
- Working Media: einzige kanonische Medienbasis für Analyse und geplante Produktion/Export
- Stock-/Adobe-Beschaffung und Preview-Lizenzfluss für Discovery: **UNKNOWN** bis Provider-Gate

## Retention

Keine automatische Löschung historischer Imports, Validierungen, Intake-Pläne,
Working Media, Analysis Identities, Shots, Frames, Observations oder Reviews — Handoff.
Konflikte überschreiben keine kanonischen Ausgaben — Intake-Regeln.
