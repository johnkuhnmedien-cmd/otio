> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Media Lifecycle — Discovery V2

Lebenszyklus von Originalmedien über Working Media bis Export-Referenzen.

## Rollen der Medienobjekte

| Objekt | Rolle | Schreibbar? |
|---|---|---|
| Originalquelle im Projektbaum | Herkunft; nie ändern | nein (Discovery) |
| Classic `_otio/` | fremde Pipeline | read-only für Discovery |
| Discovery Working Media | kanonische Produktionsbasis | nur unter `_otio_v2/media/working/` |
| Analysis Frames / Previews | Analysehilfe | unter `_otio_v2/analysis/`; nicht Working Media |
| Temp | Laufzeit | `_otio_v2/**/temp/`; austauschbar |
| OTIO-Referenzen | Export | nur auf completed Working Media |

## Zustandsfolge

```text
Source (Inventory)
→ Selected
→ Registered
→ Technically Validated
→ Intake Planned
→ Working Media (ready → … → completed | failed)
→ Analysis Eligible (nur completed)
→ Prepared (shots/frames)
→ Model-Observed (Visual Observation)
→ Review (accepted | reanalyze_requested | rejected)
→ Editorial-Ready (Gate)
→ … spätere Editorial-/Exportnutzung
```

## Working Media Regeln

- Nur Rohstatus **`completed`** ist Analyse-, Produktions- und Exportbasis.
- Identity bindet `project_id`, `asset_id`, Source-SHA/Validation, erwartete Action und `processing_profile_version`.
- Historische Working-Media-Versionen bleiben erhalten; neue Identities bei Hash-/Profilwechsel.
- `source_relative_path` ist Herkunftsmetadatum, nie kanonischer Zielpfad.
- Preview/Working-Trennung: Analysis Frames dürfen nicht als OTIO-Media oder Working Media gelten.

## Hash und Pfadprüfung (Python)

- SHA-256 der Source und Working-Media-Integrität im Worker
- Pfade müssen unter `_otio_v2` liegen; Classic- und Originalpfade als Schreib-/Analyseziel ablehnen
- Hash-Mismatch und fehlende Dateien sind terminale/recoverable Fehlercodes laut Domain

## Preview vs. Production

- **Preview / Analysis Frames:** lokal, für Vision und Review; keine Produktionsquelle
- **Working Media:** einzige Produktions- und Exportquelle im Alpha
- Stock-/Adobe-Preview-first: gewünschtes Soll laut Audit-Vergleich; Discovery-Integration **UNKNOWN** bis Provider-Gate

## Lösch- und Retention-Politik

- Keine automatische Löschung historischer Imports, Validierungen, Intake-Pläne, Working Media, Analysis Identities, Shots, Frames, Observations oder Reviews
- Konflikte überschreiben nie bestehende kanonische Ausgaben
