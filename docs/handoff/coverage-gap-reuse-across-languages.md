# Beschaffte Assets sprachübergreifend nutzbar machen

Anlass: Ein DE-Projekt hat 62 Coverage Gaps ausgelöst und viele davon gefüllt.
Das EN-Projekt im selben Medienordner löst danach erneut 52 Gaps aus. Frage:
Werden die im DE-Lauf beschafften Assets überhaupt genutzt, und wie wird das
nachhaltig?

Dieses Dokument hält den Befund und die umgesetzte Lösung fest. Tests:
`tests/test_crosslang_gapfill_reuse.py` (DE/EN-Szenario),
`tests/test_supplement_inventory_gate.py` (Vertrag des Eingangstors),
`tests/test_supplement_recovery.py` und
`tests/test_recover_supplement_inventory_script.py` (Bestandsprojekte),
`tests/test_supplement_recovery_job.py` (Hintergrund-Job),
`tests/test_gap_reset_service.py` (Räumen vor einem neuen Cut).

Für ein bereits laufendes Projekt ist Abschnitt 3.7 der Einstieg, für einen
frischen LLM-Cut Abschnitt 3.8.

---

## 1. Was geteilt wird und was nicht

Ein DB-Projekt = eine Sprache. Zwei Sprachen auf demselben Medienordner teilen
sich das Arbeitsverzeichnis, aber nicht die Redaktion.

| Artefakt | Pfad | Scope |
|---|---|---|
| Originalmedien | `{root}/{Ordner}/` | geteilt |
| Clean Media | `_otio_enhanced/clean/{Ordner}/` | geteilt |
| Asset-Inventar | `_otio_enhanced/inventory/{Ordner}.json` | geteilt |
| Slim-Inventar (LLM-Sicht) | `_otio_enhanced/inventory/{Ordner}.slim.json` | geteilt |
| Analyse-Cache Originale | `_otio_enhanced/cache/inventory/{Ordner}/` | geteilt |
| Analyse-Cache beschaffte Assets | `_otio_enhanced/cache/inventory/{Ordner}/_supplements/` | geteilt |
| Coverage Gaps | `…/{LANG}/voiceover_generation/coverage/coverage_gaps.json` | pro Sprache |
| Accepted Supplements | `…/{LANG}/voiceover_generation/stock/accepted_supplements.json` | pro Sprache |
| Funnel-Report | `…/{LANG}/voiceover_generation/stock/supplement_funnel_report.json` | pro Sprache |
| Stock-Downloads | `…/{LANG}/voiceover_generation/stock/downloads/{gap}/{candidate}/` | pro Sprache |

Der Gap-Lebenszyklus bleibt sprachgebunden: Gaps entstehen aus dem Unified Cut
Plan, tragen `gap_{slot_id}` und sind über `cut_plan_run_id` an einen Planlauf
gebunden. Eine DE-Freigabe kann einen EN-Gap deshalb nicht direkt schließen —
und soll es auch nicht. Der Übergabepunkt ist das geteilte Inventar.

---

## 2. Befund vor der Änderung

1. **Der Gap-Fill kam an, aber als Bürger zweiter Klasse.**
   `_import_into_inventory` schrieb zwölf Felder. Es fehlten
   `duration_seconds`, `usable_in_s`, `content_tags`, `caption`, `motion`,
   `framing`, alle vier Profile und `analysis_schema_version`. Im Slim-Dokument
   blieb eine Zeile aus `id`, `file`, `type`, `caption` übrig, während
   Originale `duration_s`, `tags`, `motion`, `framing`, `usable_in_s` tragen.

2. **Die Beschreibung war eine Ranking-Begründung in Projektsprache.** Der
   Funnel übergab `record.reason`. Im Inventar stand ein deutscher Satz
   darüber, warum ein Kandidat zu einer deutschen Passage passte.

3. **Ein Ordner-Sync löschte die Gap-Fills wieder.**
   `is_external_inventory_media_path()` kannte nur `_supplemental/` und
   `cut_plan/supplement_assets/`. Enhanced-Fills liegen unter
   `_otio_enhanced/clean/{Ordner}/` und fielen beim Rebuild aus dem Cache
   heraus.

4. **Kein lokaler Reuse-Durchlauf vor der Stock-Suche.** Die einzige
   Inventarabfrage im Funnel ist als „nur Anzeige, keine Auto-Wahl" markiert.

---

## 3. Umgesetzte Lösung

Leitgedanke: **ein gefüllter Gap ist ein Inventarzuwachs.** Steht das Asset mit
denselben Parametern im Inventar wie jedes andere, wählt der zweite Sprachlauf
es in Runde 1 normal aus. Ein Gap-Matching zwischen Sprachen wird überflüssig.

### 3.1 Ein Eingangstor für alle Funnels

Neu: `otio_app/services/supplement_inventory.py`.

```
Supplement-Funnel  ─┐
Coverage-Gap-Inbox ─┼─→ ingest_supplement_asset()
Manuelle Zuweisung ─┘        │
                             ├─ analyze_supplement_media()   (v3-Analyse)
                             ├─ Herkunft überlagern          (SupplementProvenance)
                             ├─ Supplement-Cache schreiben   (haltbarer Speicher)
                             └─ Upsert ins geteilte Inventar (Provider-Dedupe)
```

`_import_into_inventory` ist die Adapterschicht dorthin, sodass alle
bestehenden Aufrufer — Funnel, Inbox, manuelle Zuweisung, Legacy-Resolve —
ohne Änderung durch dasselbe Tor laufen. `intake_source` unterscheidet die
Herkunft (`funnel`, `inbox`, `manual`).

Der generische Fallback bleibt außen vor: er wählt ein bereits inventarisiertes
Ordner-Asset und braucht keinen Import.

### 3.2 Gleiche Parameter — durch denselben Prompt

`asset_analyzer.analyze_supplement_media` ruft dieselbe interne Analyse wie ein
Original: identischer Prompt (`analyze_media_from_frames`), identisches
v3-Schema, identische Signatur. Damit trägt ein Gap-Fill `caption`,
`content_tags`, `motion_profile`, `framing_profile`, `look_profile`,
`quality_profile`, `defect_items`, `analysis_signature` und
`analysis_schema_version`.

Die Frage „müssen wir denselben Prompt fahren?" ist damit mit Ja beantwortet —
und zwar nicht durch Nachbau, sondern durch Aufruf derselben Funktion. Eine
Kopie des Prompts würde bei der nächsten Prompt-Änderung wieder auseinander
laufen; `ASSET_DESCRIPTION_PROMPT_VERSION` ist Teil der Signatur, sodass eine
Prompt-Änderung Originale und beschaffte Assets gemeinsam als veraltet
markiert.

Neue Felder auf `AssetMediaAnalysis`:

| Feld | Zweck |
|---|---|
| `supplement_intake_source` | `funnel` \| `inbox` \| `manual` |
| `supplement_intake_note` | Beschaffungsbegründung — nicht mehr in `description` |

### 3.3 Eigener Scope für Cache und Frames

Beschaffte Assets liegen außerhalb des Medienordners.
`discover_folder_media_paths` leitet aber aus Cache- und Frame-Namen auf
Top-Level-Medien zurück. Ohne Trennung entstünde aus
`pexels_27608379_clean.mp4` ein Original, das es nie gab — der Ordner würde nie
wieder grün.

Deshalb: `CACHE_SCOPE_SUPPLEMENT` legt Cache unter
`cache/inventory/{Ordner}/_supplements/` und Frames unter
`frames/{Ordner}/_supplements/{stem}/`. Die Discovery überspringt beides,
zusätzlich filtert sie defensiv über `asset_origin`.

### 3.4 Erhalt statt Pfad-Raten

Ob eine Zeile beschafftes Material ist, entscheidet jetzt
`analysis_models.is_supplement_asset()` über `asset_origin` — nicht der Pfad.
Das ist der Kern der Nachhaltigkeit: Clean Media legt auch Originale außerhalb
des Medienordners ab, und jeder neue Funnel bringt einen neuen Ablageort mit.
Ein Pfadmuster wäre beim nächsten Funnel wieder falsch.

`folder_inventory_matches_media` und `materialize_folder_inventory_from_cache`
nutzen diese Prüfung. Zusätzlich stellt `_supplement_assets_to_preserve` fehlende
Zeilen aus dem Supplement-Cache wieder her — ohne neuen LLM-Aufruf. Ein einmal
analysiertes Asset hängt damit nicht mehr an einer einzelnen JSON.

Unverändert bleibt: ist ein Ordner nicht grün, wird seine Inventar-JSON
gelöscht. Der Supplement-Cache überlebt das und füllt beim nächsten grünen Sync
wieder auf.

### 3.5 Erkennung offener Assets im Analysen-Tab

Die Frage „wie erkennen wir, dass diese Assets nicht analysiert sind?"
beantwortet derselbe Mechanismus, der schon für Originale gilt:
`classify_asset_cache_status`. Eine Zeile ohne `analysis_signature` ist
`legacy`, eine mit veraltetem Schema, Prompt, Sampler oder Modell ist `stale` —
beides nicht `current`, also offen.

Das greift ohne Migration für Altbestände: alles, was vor dieser Änderung über
Funnel oder Inbox importiert wurde, hat keine Signatur und wird erkannt.

Sichtbar und ausführbar wird das an zwei Stellen:

- `analyze_asset_folders(..., analyze_supplements=True)` — der normale
  Analyselauf holt offene Supplements mit. Auch dann, wenn alle Originale
  bereits analysiert sind (der Skip-Pfad prüft die Supplements mit).
- Analysen-Tab: Abschnitt „Beschaffte Assets" mit Anzahl, Grund und eigenem
  Button. Die Ordnervorschau nennt die zusätzlichen Assets, damit die
  Kostenschätzung vor dem Lauf stimmt.

Statusabfragen lösen dabei keine Analyse aus — die Kostensicherheit aus
`tests/test_asset_analysis_ui_cost_safety.py` bleibt gewahrt.

### 3.6 Wiederverwendung statt Doppelkauf

`upsert_supplement_into_inventory` dedupliziert über Pfad, `asset_id` und
Provider-Identität. Zusammen mit der bestehenden Download-Vermeidung in
`enhanced_supplement_dedupe` landet dasselbe Stock-Asset genau einmal im
Inventar — sonst greifen `max_asset_usage` und der Reuse-Abstand nicht.

---

## 3.7 Bestandsprojekte: verlorene Zeilen zurückholen

Projekte, die vor dieser Änderung liefen, haben ihre Supplement-Zeilen bereits
verloren. Die Dateien liegen aber noch da, und drei Quellen hat der alte Sync
nie angefasst:

| Quelle | Pfad | Metadatenqualität |
|---|---|---|
| Acceptance-Listen aller Sprachen | `{LANG}/voiceover_generation/stock/accepted_supplements.json` | vollständig: Provider, Provider-Asset-ID, Lizenz, Gap, Pfad |
| Clean-Media-Manifeste | `clean_media/{Ordner}.json` | Ordner exakt; Provider aus dem Dateinamen |
| Stock-Downloads | `{LANG}/voiceover_generation/stock/downloads/{gap}/{candidate}/` | Ordner aus dem Gap-Prefix |

`services/supplement_recovery.py` liest alle drei, bestimmt den Ordner und
schickt jeden Fund durch dasselbe Eingangstor. Die Ordnerbestimmung läuft in
dieser Reihenfolge: Pfad unter `clean/{Ordner-Slug}/`, dann Clean-Manifest, dann
Kapitel-Prefix der `gap_id` (`Yellowstone_gap_003` → `Yellowstone`). Lässt sich
kein Ordner bestimmen, wird gemeldet statt geraten — eine falsch einsortierte
Datei wäre schlimmer als eine gemeldete.

Wichtig: Die Analysen der **Originale** waren nie betroffen. `inventory/` und
`cache/inventory/` liegen sprachneutral im geteilten Arbeitsverzeichnis; es gibt
also nichts zu kopieren. Verloren waren ausschließlich die Zeilen beschafften
Materials.

Zwei Wege:

- **UI:** Analysen-Tab → „Beschaffte Assets" → „Bestand beschaffter Assets
  prüfen". Erst prüfen (ändert nichts), dann nachtragen.
- **Terminal:** `scripts/recover_supplement_inventory.py --list`, dann
  `--project-root <Pfad> --dry-run`, dann ohne `--dry-run`.

Beide Wege sind idempotent: liegt schon eine aktuelle Analyse vor, wird sie aus
dem Cache übernommen statt neu bezahlt. Die Sprache, aus der man den Lauf
startet, ist gleichgültig — die Acceptance-Listen aller Sprachen werden gelesen
und das Ziel ist das geteilte Inventar.

### Eigenes Material gehört nicht in die Bestandsaufnahme

Acceptance-Listen enthalten nicht nur beschaffte Assets. Der generische Fallback
(`generic_gap_fallback_service`) und die manuelle Zuweisung eines lokalen Pfads
schreiben Einträge, deren `local_media_path` auf ein **bereits inventarisiertes
Original** zeigt — beim Fallback ist das der ganze Sinn: `provider` ist
`generic_fallback`, `license` ist `project_inventory`.

Solche Einträge dürfen nicht nachgetragen werden. Sonst ersetzt der Upsert die
Originalzeile durch eine Supplement-Zeile, ein Ordner-Sync stellt sie aus dem
Primär-Cache wieder her, und der nächste Scan meldet dieselben Dateien erneut —
eine Schleife, die pro Runde echte Gemini-Aufrufe kostet.

`supplement_inventory.is_local_original_media` entscheidet das über zwei
Signale: eine Inventarzeile mit `asset_origin=local_original` oder eine Datei
innerhalb eines Asset-Ordners. Geprüft wird an drei Stellen:

- im Scan der Bestandsaufnahme (alle drei Quellen),
- im Eingangstor selbst (`ingest_supplement_asset` wirft `ValueError`),
- in `list_supplement_assets`, damit die UI Altlasten nicht endlos anzeigt.

`purge_supplement_rows_for_own_material` reinigt Bestände, in denen das schon
passiert ist: falsche Inventarzeile weg, Supplement-Cache-Eintrag weg, danach
ein Sync, der das Original aus dem Primär-Cache zurückholt. Der normale
Supplement-Analyselauf ruft das selbst auf und meldet die Zahl.

### Kosten und Laufzeit

Jedes beschaffte Asset kostet **einmal** Frame-Extraktion plus einen
Gemini-Aufruf — hundert Bestands-Assets sind also hundert Aufrufe. Danach liegt
das Ergebnis mit Signatur im Supplement-Cache; jeder weitere Lauf ist ein
Cache-Treffer und kostet nichts. Der Analysen-Tab nennt die Zahl vor dem Start.

Weil das dauert, läuft die Bestandsaufnahme als Hintergrund-Job
(`services/supplement_recovery_job.py`) im selben Muster wie die Asset-Analyse:
Fortschrittsbalken mit Datei und Zähler, Stop-Knopf, globales Banner auf anderen
Seiten. Zwei Eigenschaften machen den Stop unkritisch:

- Jedes Asset wird sofort ins Inventar und in den Supplement-Cache geschrieben.
  Ein Abbruch verliert nur das laufende Asset.
- Ein neuer Lauf macht dort weiter, wo der alte endete, weil er nach
  Analysebedarf sortiert.

Für Etappen gibt es „Assets pro Durchlauf". Der Job nimmt dabei immer erst die
Assets ohne aktuelle Analyse, damit eine Etappe keinen Platz an bereits fertige
verliert.

## 3.7a Neuanalyse muss in der Inventar-JSON ankommen

Gefunden beim Prüfen eines echten Projekts, in dem zwei Kapitel noch auf einer
Legacy-Analyse standen: `should_skip_folder_analysis` akzeptiert Legacy-Zeilen
als „erfolgreich analysiert". `materialize_folder_inventory_from_cache` hat die
vorhandene JSON deshalb unverändert weiterverwendet — auch direkt nach einem
Analyselauf, der für dieselben Dateien bereits eine v3-Fassung in den Cache
geschrieben hatte. Bezahlt, aber nicht angekommen: die Slim-Sicht und damit der
Cut-LLM lasen weiter den alten Stand.

`_cache_is_newer_than_inventory` vergleicht jetzt pro Datei `analysis_signature`
und `description` zwischen Cache und Inventarzeile. Unterscheiden sie sich, wird
neu aufgebaut; sonst bleibt die JSON unangetastet (kein unnötiges Schreiben).

Beschaffte Assets bleiben dabei erhalten und werden **nicht** erneut analysiert:
sie liegen im eigenen Cache-Scope und tragen bereits eine aktuelle Signatur.
`test_reanalyzing_legacy_originals_keeps_supplements_untouched` prüft beides —
nur das Original geht erneut an Gemini, die Supplement-Zeile behält Signatur,
Tags und Beschaffungsnotiz.

## 3.7b Ein 503 darf keinen Ordner entwerten

Aus einem echten Lauf: 39 von 40 Assets analysiert, eines scheiterte an
`503 UNAVAILABLE — Deadline expired before operation could complete`. Folge:
`folder_is_fully_analyzed` False → `sync_folder_inventory_with_status` löscht die
Inventar-JSON des Ordners. Zwanzig bezahlte Analysen lagen im Cache, aber das
Kapitel hatte kein Inventar mehr.

Die Analyse hatte keinerlei Wiederholung: ein transienter Serverfehler landete
als dauerhafter Analysefehler im Cache. `gemini_client.is_transient_api_error`
unterscheidet jetzt (HTTP 408/429/500/502/503/504, Status `UNAVAILABLE`,
`RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`, `ABORTED`, dazu Timeouts
und Verbindungsfehler), und `_analyze_frames_with_retry` versucht es bis zu
dreimal mit 4 s und 12 s Abstand. Gewartet wird in halben Sekunden, damit Stop
nicht blockiert. Echte Fehler — etwa `400 INVALID_ARGUMENT` — werden nicht
wiederholt.

Die Löschregel selbst bleibt: ein nicht grüner Ordner hat kein Inventar, damit
Schnittplan und Edit-Plan nie einen Teilstand lesen. Beschaffte Assets gehen
dabei nicht verloren — der Supplement-Cache hält sie, und der nächste
erfolgreiche Lauf stellt die Zeile mit Herkunft, Beschaffungsnotiz und Signatur
wieder her, ohne neuen LLM-Aufruf.
`tests/test_asset_analysis_transient_retry.py` deckt alle drei Fälle ab.

## 3.8 Was ein neuer LLM-Cut mit den Gaps macht

Teilweise automatisch, aber nicht vollständig:

| Artefakt | Verhalten beim neuen Cut |
|---|---|
| `coverage_gaps.json` | wird aus den Slots des neuen Plans **komplett neu geschrieben** (`unified_to_rough` → `persist_coverage_gaps`); Gaps, die der neue Plan nicht erzeugt, verschwinden |
| Weak-Bestätigungen | werden für wiederkehrende Gap-IDs übernommen (`carry_over_user_confirmed_weak`, nur `priority=medium`) |
| Fertige Fills | werden auf die neue Run-ID **umgebogen**, wenn die Gap-ID wieder vorkommt (`rebind_gap_fills_to_current_run`) |
| `search_results.json` | bleibt unverändert |
| `supplement_funnel_report.json` | bleibt unverändert |

Der wunde Punkt ist die Wiederbindung. `gap_id` ist `gap_{slot_id}` und damit
deterministisch: Slot 3 heißt in jedem Lauf Slot 3. Ein neuer Gap an dieser
Position erbt deshalb den alten Kandidaten, obwohl es redaktionell um etwas
anderes gehen kann. `tests/test_gap_reset_service.py::test_new_cut_rebinds_fill_to_recurring_gap_id`
hält das fest.

`gap_reset_service.reset_open_coverage_gaps` räumt daher gezielt:

- offene Gaps aus `coverage_gaps.json` (External-Spiegel bleibt synchron),
- deren Kandidaten aus `search_results.json`,
- deren Einträge aus dem Funnel-Report (inklusive der ID-Listen),
- vorgemerkte Accepted-Einträge **ohne** fertiges Medium.

Zwei Invarianten: Dateien werden nie gelöscht, und `export_ready`-Einträge
bleiben im Ledger — sie sind bezahltes Material und zugleich Quelle der
Bestandsaufnahme aus 3.7.

Optional (`unbind_filled=True`) wird zusätzlich die Gap-Bindung fertiger Assets
gelöst: `gap_id` und `cut_plan_run_id` werden geleert, Asset, Pfad und Lizenz
bleiben. Danach muss der neue Cut jede Zuweisung neu aus dem Inventar verdienen.
Das ist genau dann richtig, wenn das Material inzwischen regulär im Inventar
steht — vorher wäre es ein Verlust an Zuordnung ohne Ersatz.

UI: Schnittplan-Tab → Gap-Übersicht → „Offene Coverage Gaps zurücksetzen",
erst prüfen, dann räumen.

---

## 4. Was das für das EN-Projekt bedeutet

Nach einem Analysedurchlauf im EN-Projekt stehen alle im DE-Lauf beschafften
Assets mit vollem Profil im geteilten Inventar und im Slim-Dokument. Der
EN-Cut-Lauf sieht sie wie Originale und kann sie in Runde 1 zuweisen. Gaps
entstehen dann nur noch dort, wo tatsächlich nichts Passendes existiert.

Ein Nebeneffekt beim ersten Lauf: Die Beschreibung beschaffter Assets ändert
sich von der Ranking-Begründung zur Bildbeschreibung. Das ändert den
Inventar-Hash, bestehende Schnittpläne melden „Inventory changed" und wollen
einen Rebuild. Das ist korrekt — das Inventar hat sich inhaltlich geändert.

---

## 5. Was bewusst offen bleibt

- **Sprache der Beschreibung.** Analyse-Freitexte folgen weiter
  `project.language`; dasselbe Asset bekommt je nach Erstsprache einen anderen
  Text. Entschärft ist das durch `content_tags` (kurz, englisch, sprachrobust)
  und dadurch, dass die Skript-Prompts Inventartexte ausdrücklich nur als
  Inhaltsquelle behandeln. Eine feste Referenzsprache für Inventar-Analysen
  wäre der nächste saubere Schritt.
- **Lokaler Reuse-Durchlauf vor der Stock-Suche.** Der Funnel geht weiter
  direkt zu den Providern. Mit vollständigen `content_tags` ist ein Vorlauf
  gegen das geteilte Inventar jetzt erst sinnvoll umsetzbar.
- **Gemeinsames Supplement-Ledger.** `accepted_supplements.json` vermischt
  weiterhin Beschaffung (sprachneutral) und Gap-Zuweisung (sprachgebunden).
  Durch das geteilte Inventar ist das entschärft, aber nicht aufgelöst.
- **Semantischer Gap-Schlüssel.** Es gibt weiterhin keine Kennung, über die
  „DE-Gap 17" und „EN-Gap 9" als dieselbe redaktionelle Lücke erkennbar wären.
  Mit den Stufen oben entstehen die meisten dieser Gaps gar nicht mehr.

---

## 6. Berührte Module

| Modul | Änderung |
|---|---|
| `analysis_models` | `is_supplement_asset`, `supplement_asset_paths`, zwei Herkunftsfelder |
| `services/supplement_inventory` | neu: Eingangstor, Statusliste, Nachanalyse |
| `services/supplement_recovery` | neu: Bestandsaufnahme für Altprojekte |
| `services/supplement_recovery_job` | neu: Hintergrund-Job mit Fortschritt und Stop |
| `ui/analysis_jobs_ui` | Bestandsaufnahme im Job-Monitor und im globalen Banner |
| `services/without_voiceover_enhanced/gap_reset_service` | neu: offene Gaps vor einem neuen Cut räumen |
| `scripts/recover_supplement_inventory.py` | neu: Wiederherstellung im Terminal |
| `ui/without_voiceover_enhanced/cut_plan_tab` | Reset-Abschnitt in der Gap-Übersicht |
| `services/asset_analyzer` | Scope-Parameter, `analyze_supplement_media`, Supplements im Lauf |
| `services/media_inventory_cache` | Cache-Scopes, Supplement-Scan, Phantom-Schutz |
| `services/inventory_loader` | Erhalt über Herkunft, Wiederherstellung aus dem Cache |
| `services/without_voiceover_enhanced/supplement_resolve_service` | Adapter aufs Eingangstor |
| `services/without_voiceover_enhanced/manual_gap_assign_service` | `intake_source` |
| `services/without_voiceover_enhanced/coverage_gap_external_export` | Inbox meldet ihre Herkunft |
| `ui/project_workbench` | Abschnitt „Beschaffte Assets", Vorschau mit Supplementzahl |
