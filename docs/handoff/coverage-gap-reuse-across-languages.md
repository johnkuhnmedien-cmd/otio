# Beschaffte Assets sprachübergreifend nutzbar machen

Anlass: Ein DE-Projekt hat 62 Coverage Gaps ausgelöst und viele davon gefüllt.
Das EN-Projekt im selben Medienordner löst danach erneut 52 Gaps aus. Frage:
Werden die im DE-Lauf beschafften Assets überhaupt genutzt, und wie wird das
nachhaltig?

Dieses Dokument hält den Befund und die umgesetzte Lösung fest. Tests:
`tests/test_crosslang_gapfill_reuse.py` (DE/EN-Szenario) und
`tests/test_supplement_inventory_gate.py` (Vertrag des Eingangstors).

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
| `services/asset_analyzer` | Scope-Parameter, `analyze_supplement_media`, Supplements im Lauf |
| `services/media_inventory_cache` | Cache-Scopes, Supplement-Scan, Phantom-Schutz |
| `services/inventory_loader` | Erhalt über Herkunft, Wiederherstellung aus dem Cache |
| `services/without_voiceover_enhanced/supplement_resolve_service` | Adapter aufs Eingangstor |
| `services/without_voiceover_enhanced/manual_gap_assign_service` | `intake_source` |
| `services/without_voiceover_enhanced/coverage_gap_external_export` | Inbox meldet ihre Herkunft |
| `ui/project_workbench` | Abschnitt „Beschaffte Assets", Vorschau mit Supplementzahl |
