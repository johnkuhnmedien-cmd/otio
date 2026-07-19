> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Pipeline Spec — Discovery V2

## Module

```text
otio_app/discovery_v2/
  ui/  application/  domain/  adapters/  persistence/  jobs/
```

## Stufe A–D — implementiert (Phasen 7–8)

### Inventory / Selection

- read-only Scan; `source_group` ≠ Kapitel
- Selection an `scan_id`; neuer Scan → stale

### Registry / Validierung

- SQLite `_otio_v2/registry/assets.sqlite3`, Schema **14**
- Worker-Validierung inkl. SHA-256; fehlender Timecode kein Fehler

### Media Intake

Profile: `copy-v1`, `remux-mp4-v1`, `video-h264-v1`, `image-png-v1`.
Working Media unter `_otio_v2/media/working/...`; Temp unter `_otio_v2/**/temp/`.

### Assetanalyse

Eligibility nur `completed` Working Media → Prepare → Fake Vision Gateway →
Observation Review / Editorial-Ready-Gate.

## Stufe E — geplante Hauptpipeline

Siehe `docs/MASTER_PLAN.md`. Kurz:

Coverage → Stock-Supplementation → Script Lock → ElevenLabs →
LLM-Pausenregie → Python-Timing → Visual Edit Plan → Humanity →
Feasibility → Freigabe → OTIO.

## Adobe-Medienfolge (verbindlich)

```text
Bestand
→ Suche
→ Preview
→ Validierung
→ Dublettenprüfung
→ Akzeptanz
→ OAuth-Prüfung
→ Lizenzierung
→ Originaldownload
→ Media Intake
→ Registry
```

Zusätzlich:

- konkrete Adobe OAuth-Variante bleibt **UNKNOWN**
- keine Lizenzierung vor Akzeptanz und OAuth-Prüfung
- Stock Preview ist niemals Working Media
- akzeptierte ungenutzte Assets werden nicht automatisch gelöscht

## Stock-Eskalation (verbindlich)

```text
lokal tiefer prüfen
→ Foto
→ bessere Suche
→ Satz gezielt umformulieren
→ erneut suchen
→ Karte oder Grafik
→ Nutzerentscheidung
```

Kein beliebiges Ersatzasset. Provider-/OAuth-/Lizenzdetails dürfen UNKNOWN bleiben;
diese Eskalationsreihenfolge selbst ist verbindlich und nicht UNKNOWN.

## UI-No-I/O

Beim normalen Rendering verboten: ffprobe, FFmpeg, Pillow-Open, Hashing,
Medien-stat, automatischer Jobstart, Provider-Aufrufe.

## Blockierte Formate (Phase 7)

HEIC/HEIF, 16-Bit-/Big-/Mehrseiten-TIFF, TIFF mit ICC, exotische TIFF-Modi.
