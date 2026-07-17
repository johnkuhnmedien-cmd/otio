> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Media Lifecycle — Discovery V2

## Rollen

| Objekt | Rolle | Discovery-Schreibzugriff |
|---|---|---|
| Originalquelle | Herkunft | nein |
| Classic `_otio/` | fremde Pipeline | nein (Classic read-only) |
| Working Media | einzige Produktions- und OTIO-Medienquelle | `_otio_v2/media/working/` |
| Analysis Frames | Analysehilfe | `_otio_v2/analysis/` |
| Stock Preview | Entscheidungsvorschau | nie Working Media |
| Temp | Laufzeit | `_otio_v2/**/temp/` |

## Zustandsfolge (bis Editorial-Ready implementiert)

```text
Source → Selected → Registered → Validated → Intake Planned
→ Working Media (… → completed | failed)
→ Analysis Eligible → Prepared → Observed → Reviewed → Editorial-Ready
```

## Working Media

- Nur Rohstatus **`completed`** für Analyse, Produktion und OTIO-Export
- Identity-Bindung an Validation/Source-SHA, Action, Profilversion
- Analysis Frames und Stock Previews sind keine Working Media / kein OTIO-Media

## Adobe-Medienfolge

```text
Bestand → Suche → Preview → Validierung → Dublettenprüfung
→ Akzeptanz → OAuth-Prüfung → Lizenzierung → Originaldownload
→ Media Intake → Registry
```

- keine Lizenzierung vor Akzeptanz und OAuth-Prüfung
- Adobe OAuth-Variante: **UNKNOWN**
- akzeptierte ungenutzte Assets nicht automatisch löschen

## Stock-Eskalation

```text
lokal tiefer prüfen → Foto → bessere Suche → Satz gezielt umformulieren
→ erneut suchen → Karte oder Grafik → Nutzerentscheidung
```

Kein beliebiges Ersatzasset.

## Retention

Keine automatische Löschung historischer Imports, Validierungen, Pläne,
Working Media, Analysis-Daten, Observations oder Reviews.
Konflikte überschreiben keine kanonischen Ausgaben.
