# Coverage Gaps sprachübergreifend nutzbar machen

Stand: Branch `cursor/intro-three-and-revise-fce6` (Enhanced-Pipeline).
Anlass: DE-Projekt hat 62 Gaps ausgelöst und viele davon gefüllt. Das
EN-Projekt im selben Medienordner löst danach erneut 52 Gaps aus. Frage: Werden
die im DE-Lauf gewonnenen Assets überhaupt genutzt, und wie wird das nachhaltig?

Alle Aussagen unten sind gegen den Code geprüft; die Charakterisierungstests
liegen in `tests/test_crosslang_gapfill_reuse.py`.

---

## 1. Ist-Zustand: was geteilt wird und was nicht

Ein DB-Projekt = eine Sprache. Zwei Sprachen auf demselben Medienordner teilen
sich das Arbeitsverzeichnis, aber nicht die Redaktion.

| Artefakt | Pfad | Scope |
|---|---|---|
| Originalmedien | `{root}/{Ordner}/` | geteilt |
| Clean Media | `_otio_enhanced/clean/{Ordner}/` | geteilt |
| Asset-Inventar | `_otio_enhanced/inventory/{Ordner}.json` | geteilt |
| Slim-Inventar (LLM-Sicht) | `_otio_enhanced/inventory/{Ordner}.slim.json` | geteilt |
| Analyse-Cache | `_otio_enhanced/cache/inventory/{Ordner}/` | geteilt |
| Coverage Gaps | `_otio_enhanced/{LANG}/voiceover_generation/coverage/coverage_gaps.json` | pro Sprache |
| Accepted Supplements | `…/{LANG}/voiceover_generation/stock/accepted_supplements.json` | pro Sprache |
| Funnel-Report | `…/{LANG}/voiceover_generation/stock/supplement_funnel_report.json` | pro Sprache |
| Stock-Downloads | `…/{LANG}/voiceover_generation/stock/downloads/{gap}/{candidate}/` | pro Sprache |

Der Gap-Lebenszyklus ist damit vollständig sprachgebunden: Gaps entstehen aus
dem Unified Cut Plan (`asset_fit ∈ {weak, none}`), bekommen die ID
`gap_{slot_id}` und werden über `cut_plan_run_id` an genau einen Planlauf
gebunden. `local_media_service.list_export_ready_supplements` liefert nur
Einträge mit passender Run-ID. Eine DE-Freigabe kann einen EN-Gap deshalb
konstruktionsbedingt nie direkt schließen.

Der einzige echte Übergabepunkt zwischen den Sprachen ist das geteilte
Inventar — plus die Download-Vermeidung in
`enhanced_supplement_dedupe.find_existing_enhanced_provider_asset`, die
`inventory → accepted (alle Sprachen) → _supplemental → cut_plan_manifest`
nach derselben `provider:provider_asset_id` absucht.

---

## 2. Befunde

### 2.1 Das Asset kommt an — als Bürger zweiter Klasse

`supplement_funnel_service._persist_export_ready` ruft
`supplement_resolve_service._import_into_inventory` auf. Der Gap-Fill steht
danach im geteilten Inventar und ist für das EN-Projekt sichtbar. Geschrieben
werden aber nur zwölf Felder:

```304:318:otio_app/services/without_voiceover_enhanced/supplement_resolve_service.py
    asset = AssetMediaAnalysis(
        path=str(media_path),
        description=description or candidate.title or asset_id,
        frames_used=[str(p) for p in frames],
        asset_id=asset_id,
        asset_origin=candidate.provider or "supplement",
        provider=candidate.provider,
        source_url=candidate.source_page or candidate.download_url,
        media_type=candidate.media_type or ("image" if is_image_media(media_path) else "video"),
        supplement_validation_status=validation_status,
        supplement_validation_score=float(validation_score),
        approved_for_cut_plan=True,
        analysis_status="complete",
        license_metadata=license_meta,
    )
```

Es fehlen gegenüber einem regulär analysierten Asset unter anderem
`duration_seconds`, `usable_in_s`, `content_tags`, `caption`, `motion`,
`framing`, `people`, `aspect_ratio`, `motion_profile`, `framing_profile`,
`look_profile`, `quality_profile`, `defect_items`, `analysis_schema_version`
und `analysis_signature`.

Im Slim-Dokument, also in genau der Sicht, die der Cut-LLM bekommt, bleibt
davon eine Zeile mit vier Feldern übrig:

```json
{
  "id": "pexels_video_27608379",
  "file": "pexels_27608379_clean.mp4",
  "type": "video",
  "caption": "Zeigt die geforderte Küstenlinie aus der Luft; passt zur Passage."
}
```

Die Originalzeile daneben trägt `duration_s`, `tags`, `motion`, `framing`,
`usable_in_s`. Das Gap-Asset konkurriert im EN-Lauf also ohne die Signale, nach
denen der Cut-LLM und die Rhythmus-/Dauerprüfungen auswählen. Es kann gewählt
werden, aber es gewinnt selten.

### 2.2 Die Beschreibung ist eine Ranking-Begründung in Projektsprache

Der Funnel übergibt als Beschreibung `record.reason` — den Begründungstext aus
dem Kandidaten-Ranking, erzeugt mit `language=project.language`. Im Inventar
steht damit kein Asset-Steckbrief, sondern ein deutscher Satz darüber, warum
der Kandidat zu einer deutschen Passage passte. Das EN-Projekt liest diesen
Satz als einziges inhaltliches Merkmal des Assets.

Für Skript und Voice-Over ist das abgefedert (`native_speaker_language_block`
weist die Modelle an, Inventartexte nur als Inhaltsquelle zu behandeln). Für
die Asset-Auswahl bleibt es ein Qualitätsverlust, weil `content_tags` — das
sprachrobuste Matching-Signal — leer sind.

### 2.3 Ein Ordner-Sync löscht die Gap-Fills wieder

Das ist der gravierendste Punkt.
`cut_plan_inventory_bridge.is_external_inventory_media_path` erkennt nur zwei
Muster:

```391:398:otio_app/services/cut_plan_inventory_bridge.py
def is_external_inventory_media_path(path: Path | str) -> bool:
    """True für Supplement-Pfade außerhalb des Top-Level-Asset-Ordners."""
    from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME

    parts = Path(path).parts
    if SUPPLEMENTAL_FOLDER_NAME in parts:
        return True
    return _is_cut_plan_supplement_path(path)
```

Enhanced-Gap-Fills liegen nach `ensure_new_supplement_clean_media` aber unter
`_otio_enhanced/clean/{Ordner}/` — also weder unter `_supplemental/` noch unter
`cut_plan/supplement_assets/`. Zwei Folgen:

1. `folder_inventory_matches_media` zählt den Gap-Fill zu den Top-Level-Medien.
   Der Pfad steht in `media_files`, wird aber von `discover_folder_media_paths`
   nie gefunden. Das Inventar gilt ab sofort dauerhaft als "passt nicht mehr
   zum Ordner".
2. `materialize_folder_inventory_from_cache` rettet beim Neuaufbau nur Zeilen,
   für die `is_external_inventory_media_path` True liefert. Der Gap-Fill fällt
   heraus.

Ein `sync_folder_inventory_with_status` — ausgelöst über den Analyse-Tab, über
`selected_folders_have_inventory` oder über das Umschalten der manuellen
Ordner-Freigabe — entfernt die im DE-Lauf gewonnenen Assets damit still aus dem
geteilten Inventar. Der Test
`test_folder_sync_preserves_gapfill_rows` hält das als `xfail(strict=True)`
fest. Die Mediendatei bleibt liegen, nur die Inventarzeile ist weg; im
EN-Projekt existiert das Asset dann nicht mehr.

Nebenbefund für jede Reparatur: `discover_folder_media_paths` leitet aus
Cache-Einträgen Pfade der Form `folder_path / name` ab und filtert dabei nur
`_supplemental`. Wer Gap-Fills in den regulären Analyse-Cache schreibt, ohne
diesen Filter zu erweitern, erzeugt Phantom-Medienpfade und macht den Ordner
dauerhaft nicht mehr grün.

### 2.4 Kein lokaler Reuse-Durchlauf vor der Stock-Suche

Der Funnel sucht für jeden offenen Gap sofort bei den Stock-Providern. Die
einzige Inventarabfrage ist `_inventory_reuse_ids`, explizit als
"nur Anzeige, keine Auto-Wahl" markiert. Der generische Fallback greift erst,
wenn Stock komplett gescheitert ist, und wählt ein neutrales Ordner-Asset.

Die Download-Vermeidung greift erst eine Stufe später und nur bei identischer
`provider:provider_asset_id`. Weil `enrich_coverage_search_concepts` die
Suchbegriffe aus `needed_visual` und der Skriptpassage ableitet, sucht der
EN-Lauf mit anderen Begriffen als der DE-Lauf. Die Chance, exakt dasselbe
Provider-Asset zu treffen, ist Zufall. Praktisch werden für dieselbe
redaktionelle Lücke zwei verschiedene, inhaltlich fast gleiche Assets gekauft.

### 2.5 Keine sprachübergreifende Gap-Identität

Ein Gap ist über `gap_id` (= `gap_{slot_id}`, optional mit Ordner-Prefix) plus
`cut_plan_run_id` identifiziert. Beides ist an einen konkreten Planlauf einer
Sprache gebunden. Es gibt keinen Schlüssel, über den "DE-Gap 17" und
"EN-Gap 9" als dieselbe redaktionelle Lücke erkennbar wären — auch dann nicht,
wenn beide dasselbe Kapitel, dieselbe Bildidee und dieselbe Funktion haben.

### 2.6 Nicht jeder Fill landet überhaupt im Inventar

`generic_gap_fallback_service` und `cut_plan_service.accept_supplement_candidates`
schreiben nur in `accepted_supplements.json`. Beim generischen Fallback ist das
korrekt, weil ein bereits inventarisiertes Asset wiederverwendet wird. Bei
manuell angenommenen Suchtreffern ohne Download entsteht dagegen ein Eintrag,
den nur die eigene Sprache kennt.

---

## 3. Bewertung

Die Infrastruktur für sprachübergreifende Wiederverwendung existiert bereits:
geteiltes Inventar, geteiltes Clean-Verzeichnis, Provider-Identität, Dedupe
über Geschwistersprachen. Was fehlt, ist die Behandlung eines Gap-Fills als
vollwertiges Asset. Solange der Fill eine Ergänzung zum Schnittplan ist und
nicht ein neues Inventarobjekt, bleibt er an den Planlauf und damit an die
Sprache gebunden — und ein Ordner-Sync macht ihn wieder unsichtbar.

Deshalb würde ich das Problem nicht als "Gaps zwischen Sprachen mappen"
angehen, sondern umdrehen: **Ein gefüllter Gap ist ein Inventarzuwachs.** Wenn
das Asset mit denselben Parametern im Inventar steht wie jedes andere, wählt
der EN-Cut-Lauf es in Runde 1 ganz normal aus, und die Lücke entsteht gar nicht
erst. Ein Gap-Matching zwischen Sprachen wird dann überflüssig.

---

## 4. Lösungsideen

### Stufe 0 — Datenverlust stoppen (Voraussetzung für alles Weitere)

1. **Supplement-Herkunft explizit markieren statt aus dem Pfad raten.**
   `AssetMediaAnalysis` trägt bereits `asset_origin` und
   `supplement_validation_status`. Der Erhaltungspfad in
   `materialize_folder_inventory_from_cache` und der Vergleich in
   `folder_inventory_matches_media` sollten darauf prüfen
   (`asset_origin not in ("", "local_original")`) statt auf Pfadbestandteile.
   Das ist robust gegen jedes künftige Ablageschema.
2. `is_external_inventory_media_path` zusätzlich für Pfade unterhalb des
   Enhanced-Work-Dirs (`clean/`, `stock/downloads/`) True liefern lassen, damit
   Altbestände ohne Migration mitgerettet werden.
3. Den Filter in `discover_folder_media_paths` synchron erweitern, sonst
   entstehen Phantom-Medienpfade (siehe 2.3).
4. Der unmergte Branch `cursor/preserve-inventory-on-sync-3982`
   ("never delete shared inventory on sync") adressiert dieselbe Klasse von
   Problemen und sollte in diesem Zug bewertet werden.

Ohne diesen Schritt verliert jede weitere Maßnahme ihre Wirkung beim nächsten
Ordner-Sync.

### Stufe 1 — Gap-Fills regulär analysieren

Nach dem Download und dem Clean-Media-Schritt dieselbe Analyse fahren wie für
Originalmaterial. `asset_analyzer._analyze_single_media` ist dafür der passende
Einstiegspunkt: es extrahiert Frames, ruft `analyze_media_from_frames` und
füllt `caption`, `content_tags`, `motion_profile`, `framing_profile`,
`look_profile`, `quality_profile`, `defect_items`, `analysis_signature` und
`analysis_schema_version`.

Damit:

- steht der Gap-Fill im Slim-Dokument mit denselben Feldern wie jedes Original,
- greifen Dauer-, Rhythmus- und Aspect-Prüfungen,
- wird die Ranking-Begründung zu dem, was sie ist: Metadatum des Fills, nicht
  Beschreibung des Assets. `record.reason` gehört in ein eigenes Feld, nicht in
  `description`.

Kosten: ein zusätzlicher Vision-Call pro angenommenem Fill. Gemessen an einem
Stock-Kauf und an 52 unnötig neu ausgelösten Gaps ist das günstig. Der Call
lässt sich mit der ohnehin stattfindenden Thumbnail-/Validierungsrunde bündeln,
wenn deren Schema auf das v3-Asset-Schema erweitert wird — dann kostet es gar
nichts extra.

Wichtig für den Cache: die Analyse eines Gap-Fills darf nicht unter dem
Ordner-Cache-Scope landen, der die Grün-Logik speist. Entweder ein eigener
Scope (`cache/inventory/{Ordner}/_supplements/`) oder ein Flag im Cache-Eintrag,
das `discover_folder_media_paths` überspringt.

### Stufe 2 — Sprachneutrale Beschreibung

Die Analyse-Freitexte folgen heute `project.language`. Für ein geteiltes
Inventar ist das die falsche Achse: dasselbe Asset bekommt je nach Erstsprache
einen anderen Text.

Zwei Varianten:

- **a)** Inventar-Analysen grundsätzlich in einer festen Referenzsprache
  (Englisch) erzeugen, `content_tags` ohnehin englisch. Die Skript-Prompts
  behandeln Inventartexte bereits als reine Inhaltsquelle, sind also darauf
  vorbereitet.
- **b)** `description` sprachgetaggt mehrfach führen
  (`descriptions: {de: …, en: …}`) und beim Prompt-Bau die Projektsprache
  wählen, mit Fallback.

Variante a ist deutlich billiger und passt zur bestehenden Prompt-Politik.
Variante b ist nur nötig, wenn Inventartexte irgendwo direkt im Produkt
angezeigt werden.

Unabhängig davon: `content_tags` sind der eigentliche Hebel. Sie sind kurz,
englisch, und taugen als sprachübergreifender Matching-Schlüssel.

### Stufe 3 — Lokaler Reuse-Durchlauf vor der Stock-Suche

Vor `search_supplements_for_gaps` einen Durchlauf einziehen, der jeden offenen
Gap gegen das geteilte Inventar prüft — inklusive der Assets, die eine andere
Sprache beschafft hat. Kandidatenmenge: alle Inventarzeilen des Kapitelordners,
Signal: `content_tags` und `caption` gegen `search_concepts` und
`needed_visual`, dazu die harten Kriterien Dauer und Aspect.

Das ist derselbe Mechanismus, den der Cut-LLM schon anwendet, nur explizit auf
offene Gaps angesetzt und mit dem gesamten sprachübergreifenden Bestand. Ein
Treffer schließt den Gap ohne Netzwerkzugriff und ohne Kosten. Erst wenn hier
nichts passt, geht der Funnel zu den Providern.

Der Reuse-Zähler (`max_asset_usage`, `min_asset_reuse_distance_shots`) muss
dabei mitzählen, sonst kippt die Bildwiederholung — `reuse_identity_key`
liefert die passende kanonische Identität bereits.

### Stufe 4 — Gemeinsames Supplement-Ledger

`accepted_supplements.json` vermischt zwei Dinge: "dieses Asset ist beschafft,
lizenziert und technisch in Ordnung" (sprachneutral) und "dieser Gap in diesem
Planlauf wird davon geschlossen" (sprachgebunden).

Vorschlag zur Trennung:

- `_otio_enhanced/supplements/ledger.json` — geteilt. Ein Eintrag pro
  beschafftem Asset, Schlüssel `provider:provider_asset_id`, zusätzlich
  `sha256` für Assets ohne Provider-ID (Manual, generierte Bilder). Enthält
  Lizenz, Quelle, lokalen Pfad, Analysestand, Ordnerzuordnung.
- `…/{LANG}/…/accepted_supplements.json` — bleibt, verweist aber nur noch per
  Ledger-Schlüssel plus `gap_id` und `cut_plan_run_id`.

Damit ist Beschaffung einmalig und Verwendung n-fach. Die heutige
Cross-Language-Suche in `_iter_sibling_language_accepted_paths` wird zur
Migrationshilfe statt zum Dauerzustand.

Der Content-Hash ist hier der Punkt, an dem der Dedupe über Provider-IDs
hinauswächst: `_content_hash_key` berechnet SHA-256 bereits für
`stock/downloads` und `_supplemental`. Manuelle und generierte Assets (Nano
Banana) haben keine Provider-ID und werden heute nur über den Pfad erkannt.

### Stufe 5 — Semantischer Gap-Schlüssel

Zusätzlich zur `gap_id` einen sprachunabhängigen Schlüssel führen, etwa aus
Ordner, normalisierten `content_tags` der Bildidee und redaktioneller Funktion.
Damit lässt sich beantworten: "Diese Lücke war im DE-Projekt schon einmal da
und wurde mit Asset X geschlossen."

Wert vor allem für die Bedienung: eine Ansicht "48 von 52 EN-Gaps entsprechen
bereits gelösten DE-Gaps" mit Sammelübernahme. Wenn Stufe 1 bis 3 sitzen, ist
das Komfort, nicht Notwendigkeit — die meisten dieser Gaps entstehen dann gar
nicht mehr.

### Interimsmaßnahme

Der unmergte Branch `cursor/cross-lang-accepted-supplements-3982` blendet
`export_ready`-Supplements der Geschwistersprachen als zuweisbare lokale Assets
in den Unified-Cut-Prompt ein. Das behebt die Ursache nicht — die Zeilen tragen
`description = "[accepted EN] {title}"` und keine Analyseparameter — hilft aber
sofort und ohne Datenmigration. Als Brücke sinnvoll, solange Stufe 1 nicht
steht.

---

## 5. Reihenfolge

Stufe 0 ist Voraussetzung, sonst arbeitet alles Weitere gegen den nächsten
Ordner-Sync. Stufe 1 hat den größten Effekt pro Aufwand und ist auf
`_analyze_single_media` plus einen Aufrufpunkt in `_persist_export_ready`
begrenzt. Stufe 3 ist die erste Maßnahme, die die 52 Gaps sichtbar reduziert.
Stufe 2 und 4 sind Aufräumarbeiten mit Migrationsanteil, Stufe 5 ist optional.

Betroffene Module, nach Eingriffstiefe:

| Stufe | Module | Eingriff |
|---|---|---|
| 0 | `inventory_loader`, `cut_plan_inventory_bridge`, `media_inventory_cache` | klein, aber breite Testfläche |
| 1 | `supplement_funnel_service`, `supplement_resolve_service`, `asset_analyzer` | mittel, neuer Analysepfad |
| 2 | `gemini_client`, `asset_analyzer` | mittel, Reanalyse des Bestands nötig |
| 3 | `supplement_funnel_service`, neues Reuse-Modul | mittel, neue Auswahllogik |
| 4 | `paths`, `local_media_service`, `enhanced_supplement_dedupe`, Migration | groß |
| 5 | `models`, `gap_status_service`, UI | groß |

---

## 6. Tests

`tests/test_crosslang_gapfill_reuse.py` hält den heutigen Stand fest:

- geteiltes Inventar macht den Gap-Fill für die Geschwistersprache sichtbar,
- die Acceptance-Liste ist pro Sprache,
- die Inventarzeile eines Gap-Fills trägt keine v3-Analyseparameter,
- die Slim-Zeile besteht aus `id`, `file`, `type`, `caption`,
- `test_folder_sync_preserves_gapfill_rows` ist `xfail(strict=True)` und wird
  grün, sobald Stufe 0 umgesetzt ist.
