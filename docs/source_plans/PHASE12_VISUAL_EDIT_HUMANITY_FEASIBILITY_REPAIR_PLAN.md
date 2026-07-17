# Phase 12 — Visual Edit, Humanity, Feasibility und Repair Plan

**Auftrags-ID:** `DISCOVERY-V2-PHASE12-VISUAL-EDIT-HUMANITY-FEASIBILITY-REPAIR-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung
**Basis-HEAD:** `36a37875900a9c478ec80ef5c4b8e5eff45d5478`
**Registry-Ausgang:** Schema **18**
**Ziel-Schema (Implementierung):** Schema **19**
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente

---

## 0. Konfliktprüfung gegen höhere SoT und Phase 11

| Thema | Quelle | Plan-Konformität |
|---|---|---|
| Visual Edit erst nach finalem Narration Timing | MASTER_PLAN / ALPHA_SCOPE / ALPHA_EXECUTION / Phase-11-Plan | ja |
| Script Lock vor Narration und Visual Edit | PIPELINE_SPEC / D-10-005 / Phase 11 | ja; wirksamer Lock + aktuelle Timeline |
| Sentence ≠ Beat ≠ Intent ≠ Editorial Shot ≠ Technical Shot ≠ Asset | EDITORIAL_QUALITY / CLASSIC / 00-core | ja; §2 Modelltrennung |
| Humanity als eigener Review-Schritt | EDITORIAL_QUALITY / MASTER_PLAN | ja; nach Plan, vor Ready |
| Python: Zeiten, Ranges, Feasibility; LLM: Dramaturgie/Motives | EDITORIAL_QUALITY / MODEL_ROUTING | ja; §8 |
| Working Media = einzige Produktionsquelle | MEDIA_LIFECYCLE | ja; Preview/Temp/Original/Analysis Frames verboten |
| MANUAL; kein Auto-Start beim Rerun | ALPHA_SCOPE / 01-step | ja |
| Gateways zentral; Fake only | 00-core / MODEL_ROUTING | ja; FakeTextAdapter |
| `_otio_v2` only; Classic `_otio/` read-only | CLASSIC / MEDIA | ja; Artefakte unter `editing/` |
| KI-Timelines = NEGATIVE_REFERENCE | EDITORIAL_QUALITY | ja; keine positive Schnittvorlage |
| Keine Exportfreigabe / OTIO in Phase 12 | MASTER_PLAN Phase 13 | ja; Status ohne `approved_for_export` |
| Phase 11 endet mit validierter Narration Timeline | D-11 / Schema 18 | ja; Pflichtinput |

**Phase-11-Abgleich (Code-Stand Schema 18):**

- Wirksamer Lock: `get_effective_script_lock(project)` / Narration-Wrapper; Status `locked`.
- Narration Current State: `narration_project_state` mit `current_timeline_id`,
  `current_script_lock_id`, Voice-/Pause-Current-IDs.
- Timeline: `ResolvedNarrationTimeline` + `NarrationTimelineEntry`
  (`voice` | `pause` | `visual_only`); Status `completed` erforderlich.
- `DiscoveryTextGateway` kennt bisher
  `narrative|script|structure|coverage|pause_direction`;
  Phase 12 plant `visual_edit_plan|humanity_review|editorial_repair_proposal`.
- Gegentlichen Sperren Analysis ↔ Editorial ↔ Supplementation ↔ Narration bestehen;
  Visual-Edit-Runs werden analog eingebunden.
- Kein Discovery-V2-`editing/`-Modul; Classic Edit-Plan nur NEGATIVE_REFERENCE.
- `GraphicPlan` existiert (Phase 10); Metadaten ohne Working Media → Feasibility-Block.

Keine kritischen SoT-Konflikte. Offenes → §29 UNKNOWN.

---

## 1. Phase-12-Grenze

### Beginnt mit

1. Wirksamem Script Lock (`status=locked`, Fingerprint gültig)
2. Aktueller validierter Narration Timeline (`status=completed`, Current-ID gesetzt)
3. Aktuellen Sätzen, Visual Beats und Visual Intents aus der Lock-Kette
4. Aktuellem Coverage Audit und terminalen Gap-/Claim-Entscheidungen
5. Aktuellen completed Working Media, Analysis Identities, Technical Shots
6. Aktuellen akzeptierten Visual Observations

### Endet mit

Technisch machbarem und humanity-geprüftem **Visual Edit Plan** im Status
`ready_for_editorial_review`, bereit für die finale Editorial Review in **Phase 13**.

### In Scope

```text
wirksamer Script Lock
+ validierte Narration Timeline
+ aktuelle Working Media / Assetanalyse / Observations
→ Visual Edit Plan (Fake Gateway)
→ Humanity & Authenticity Review
→ technische Feasibility
→ redaktionelle + deterministische Repair-Schleife
→ versionierte Review-/Repair-Historie
→ MANUAL-UI „Visual Edit“
→ Recovery
→ lokale Fake-End-to-End-Smokes
→ Phase-12-Review-Paket (Plan + Humanity + Feasibility + Repair-Historie)
```

### Explizit nicht in Phase 12

- finale Editorial Approval
- Exportfreigabe / Status `approved_for_export`
- OTIO-Export, OTIO-Reparse, NLE-Datei
- echte Text-, Vision- oder Voiceprovider-Aktivierung
- neue Stock-Suche, Lizenzierung, Grafikgenerierung
- Phase 13

---

## 2. Verbindliche Modelltrennung

| Begriff | Ist | Ist nicht |
|---|---|---|
| Sentence | Script-Satz aus Lock-Kette | Visual Beat, Editorial Shot |
| Visual Beat | dramaturgische Beat-Einheit | Visual Intent, Editorial Shot |
| Visual Intent | visuelle Absicht / Motivbedarf | Editorial Shot, Asset |
| Editorial Shot | geplante redaktionelle Schnitteinheit | Technical Shot, Asset, Narration Entry |
| Technical Shot | technischer Analyseabschnitt | automatische Schnittplanung |
| Asset | Projektmedienidentität | Editorial Shot |
| Source Group / Ordner | Intake-/Organisationsgruppe | Kapitel oder Shotfolge |
| Narration Entry | Timeline-Eintrag (voice/pause/visual_only) | Editorial Shot |

Zusätzliche Regeln:

- Ein Satz kann mehrere Editorial Shots enthalten.
- Ein Editorial Shot kann mehrere Sätze oder Timeline-Einträge abdecken.
- Schnitte dürfen nicht mechanisch an Satzgrenzen liegen.
- Technical Shots sind Kandidatenbereiche, keine auto-generierten Editorial Shots.
- Source Groups/Ordner erzeugen weder Kapitel noch Shotfolgen.
- Narration-Pausen und Visual-only-Intervalle sind mögliche Schnittflächen,
  aber kein automatischer Schnittbefehl.

---

## 3. Verbindliche Inputs

### Erlaubt (nur aktuell und wirksam)

| Input | Anforderung |
|---|---|
| Script Lock | `locked`, Fingerprint gültig, Current |
| Narration Timeline | `completed`, Current, gleiche Lock-ID |
| Sätze / Beats / Intents | aus Lock-Kette; Fingerprints stimmen |
| Coverage Audit | aktuell; Gap-/Claim-Entscheidungen terminal |
| Working Media | `completed`, aktuelle Identity |
| Analysis Identity | aktuell zum Working Media |
| Technical Shots | zur aktuellen Analysis Identity |
| Visual Observations | Review-Entscheidung `accepted` (nicht unreviewed/rejected/stale) |
| GraphicPlan | nur als Metadaten-Referenz für `planned_graphic` |

### Verboten

- Originalmedien als direkte Produktionsquelle
- Preview, Temp, Quarantäne, Analysis Frames als Shotquelle
- historische oder stale Observations
- unreviewed oder rejected Observations
- Candidate-Metadaten ohne normalen Intake
- Dateinamen oder Ordnernamen als visuelle Evidenz
- NEGATIVE_REFERENCE-Timelines als positive Schnittvorlage

### Pflichtprüfungen vor jedem Phase-12-Run

1. Wirksamer Script Lock (`get_effective_script_lock`)
2. `narration_project_state.current_timeline_id` zeigt auf `completed` Timeline
3. Timeline `script_lock_id` == wirksamer Lock
4. Input-Fingerprint (Lock + Timeline + Observation-Set + Working-Media-/Analysis-Set)
   stimmt mit gespeichertem Plan-Fingerprint überein (bei Folgeoperationen)
5. Kein aktiver Analysis-, Editorial-, Supplementation-, Narration- oder Visual-Edit-Run
6. Keine stale Narration-/Observation-/Working-Media-Identität

Fehler mindestens: `script_lock_missing`, `script_lock_invalidated`,
`narration_timeline_missing`, `narration_timeline_stale`, `visual_edit_input_stale`.

---

## 4. LLM- und Python-Verantwortung

### Fake Text / späteres LLM darf entscheiden

- Shotfunktion
- Motivwahl aus zulässigen lokalen Kandidaten
- Dramaturgie, Shotgruppierung
- Übergangs-, Kontinuitäts-, Rhythmusabsicht
- Umgang mit Pausen und Visual-only-Intervallen
- redaktionelle Repair-Vorschläge
- Humanity-Risikoeinschätzungen
- alternative Shotfolgen

### Fake Text / späteres LLM darf nicht final entscheiden

- exakte Source-Ranges
- technische Clipgrenzen, Framerundung, Mediendauer
- Codec-/Dateimachbarkeit, Überlappungsfreiheit
- OTIO-Struktur, Exportfreigabe

### Python muss übernehmen

- Identitäten und Versionsbindung
- aktuelle Inputprüfung
- technische Kandidatenvalidierung
- genaue Timeline-Zeiten
- Source-Range-Berechnung und Clamping
- Framerundung
- Verfügbarkeitsprüfung, Wiederholungszählung
- deterministische Feasibility und technische Reparaturen
- Artefaktvalidierung, Persistenz, Current State

---

## 5. Domainmodelle (Pydantic-Verträge)

Alle Adapter-/LLM-Antworten: `extra="forbid"`. IDs stabil (UUID).
Keine Current-Auswahl nach `created_at`.

### 5.1 VisualEditPlan

| Feld | Typ / Hinweis |
|---|---|
| `plan_id` | str |
| `project_id` | str |
| `script_lock_id` | str |
| `narration_timeline_id` | str |
| `input_fingerprint` | str |
| `plan_version` | int (≥ 1) |
| `gateway_version` | str |
| `model_id` | str (Fake: `fake-visual-edit-v1`) |
| `prompt_version` | str |
| `schema_version` | str |
| `status` | siehe unten |
| `total_shot_count` | int |
| `expected_visual_duration_seconds` | float |
| `accepted_risks` | list[AcceptedRiskRef] |
| `created_at` | datetime |

**Status:**

`draft` · `review_required` · `repair_required` ·
`ready_for_editorial_review` · `superseded` · `invalidated`

**Kein** Status `approved_for_export` in Phase 12.

### 5.2 EditorialShot

| Feld | Typ / Hinweis |
|---|---|
| `shot_id` | str |
| `plan_id` | str |
| `ordinal` | int (≥ 0) |
| `shot_function` | str (z. B. hook / establish / detail / bridge / hold / closing) |
| `narration_entry_ids` | list[str] |
| `sentence_ids` | list[str] |
| `visual_beat_ids` | list[str] |
| `visual_intent_ids` | list[str] |
| `timeline_start_seconds` | float |
| `timeline_end_seconds` | float |
| `duration_seconds` | float (> 0) |
| `timeline_start_frame` | int |
| `timeline_end_frame` | int |
| `transition_intent` | str \| null |
| `continuity_intent` | str \| null |
| `rhythm_intent` | str \| null |
| `media_strategy` | Literal unten |
| `priority` | int |
| `uncertainty_notes` | list[str] |
| `status` | `planned` \| `assigned` \| `needs_repair` \| `blocked` \| `accepted_risk` |

**Medienstrategie:**

`local_video` · `local_photo` · `planned_graphic` · `intentional_visual_only`

`planned_graphic` bleibt bis zu realem Working Media nicht exportfähig.

### 5.3 ShotMediaAssignment

| Feld | Typ / Hinweis |
|---|---|
| `assignment_id` | str |
| `shot_id` | str |
| `asset_id` | str |
| `working_media_id` | str |
| `technical_shot_id` | str \| null |
| `visual_observation_id` | str |
| `assignment_priority` | int |
| `source_range_intent` | strukturierte Absicht (Anfang/Mitte/Ende, Aktion, Dauerwunsch, …) |
| `technical_source_in_seconds` | float \| null (Video) |
| `technical_source_out_seconds` | float \| null (Video) |
| `technical_source_in_frame` | int \| null |
| `technical_source_out_frame` | int \| null |
| `duration_seconds` | float |
| `selection_rationale` | str |
| `status` | `proposed` \| `resolved` \| `invalid` \| `blocked` |

Keine Preview- oder Originalpfade. Keine Analysis-Frame-Pfade.

### 5.4 ShotTransition

| Feld | Typ / Hinweis |
|---|---|
| `transition_id` | str |
| `from_shot_id` | str |
| `to_shot_id` | str |
| `editorial_function` | str |
| `technical_type` | Literal `"cut"` \| `"dissolve"` \| `"fade"` \| `"hold"` (Alpha-Minimum) |
| `desired_duration_seconds` | float |
| `resolved_duration_seconds` | float |
| `status` | `planned` \| `resolved` \| `blocked` |

Keine NLE-spezifischen Effekte erzwingen.

### 5.5 HumanityReview / HumanityFinding

**HumanityReview:** `review_id`, `visual_edit_plan_id`, `review_version`,
`input_fingerprint`, `status` (`completed` \| `stale` \| `superseded` \| `invalid`),
`overall_judgment` (`pass_with_risks` \| `needs_repair` \| `blocked`),
`findings`, `deterministic_signals`, `created_at`.

**HumanityFinding:** `finding_id`, `review_id`, `shot_id` nullable, `plan_level` bool,
`category`, `severity` (`info` \| `warning` \| `blocking`),
`rationale`, `evidence_refs`, `recommended_action`,
`user_status` (`open` \| `accepted_risk` \| `resolved_by_repair` \| `dismissed_invalid`).

**Kategorien (mindestens):**

`hook_quality` · `script_variation` · `local_detail` · `cliche_or_fact_list` ·
`generic_stock_risk` · `geographic_accuracy` · `asset_repetition` ·
`sentence_boundary_cut_risk` · `shot_duration_variance` · `similar_motif_sequence` ·
`visual_continuity` · `possible_synthetic_asset`

Das Review entscheidet nicht über Faktenwahrheit oder Asset-Echtheit.
Es markiert Risiken für menschliche Prüfung.

### 5.6 FeasibilityReport / FeasibilityIssue

**FeasibilityReport:** `report_id`, `plan_id`, `input_fingerprint`, `timebase`,
`status` (`completed` \| `stale` \| `superseded` \| `failed`),
`overall_technical_assessment` (`pass` \| `pass_with_warnings` \| `fail`),
`issues`, `metrics`, `created_at`.

**FeasibilityIssue:** `issue_id`, `report_id`, `shot_id` nullable,
`assignment_id` nullable, `error_code`, `severity` (`warning` \| `blocking`),
`technical_details`, `deterministically_repairable` bool,
`blocks_phase_13` bool.

### 5.7 RepairProposal / RepairRun / RepairResult

**RepairProposal:** `proposal_id`, `plan_id`, `humanity_review_id` nullable,
`feasibility_report_id` nullable, `source`
(`editorial_fake_llm` \| `deterministic_python` \| `user`),
`repair_type`, `affected_ids`, `description`, `expected_effect`,
`user_status` (`proposed` \| `selected` \| `applied` \| `rejected` \| `superseded`),
`version`.

**RepairRun:** `run_id`, `input_plan_id`, `selected_proposal_ids`,
`output_plan_id` nullable, `status`
(`queued` \| `running` \| `completed` \| `failed` \| `interrupted`),
`created_at`.

**RepairResult:** `result_id`, `run_id`, `changes`, `remaining_findings`,
`remaining_feasibility_issues`, `created_at`.

---

## 6. Visual-Edit-Plan-Strategie (Fake)

Ziel: strukturierte, reproduzierbare Fake-Planung **ohne** echte semantische
Schnittqualität vorzutäuschen.

### Verbindlich

1. Strukturiertes Inputpaket — keine Medienbinärdaten
2. Ausschließlich aktuelle akzeptierte Observation-Metadaten
3. Keine Dateinamen als Inhaltsbeschreibung
4. Technical Shots nur als technische Kandidatenbereiche
5. Konservative Shotzuordnung; sichtbare Unsicherheit (`uncertainty_notes`)
6. Nach Fake-Plan-Erzeugung Status zunächst `review_required`
   (nie direkt `ready_for_editorial_review`)
7. Ready erst nach Humanity + Feasibility + dokumentierten Repairs (§16)

### Fake-Demonstrationspflichten (Smokes)

Der Fake-Pfad muss mindestens erzeugen:

- ein Satz mit mehreren Editorial Shots
- ein Editorial Shot über mehrere Sätze
- mindestens ein Schnitt außerhalb einer Satzgrenze
- variierende Shotdauern
- keine rein mechanische Ein-Shot-pro-Satz-Struktur

### Inputpaket (Gateway, schematisch)

```text
script_lock_ref + fingerprints
narration_timeline (entries, timebase, totals)
sentences / beats / intents (IDs + kurze Texte/Labels)
coverage / gap / claim terminal decisions (IDs)
candidate assets:
  asset_id, working_media_id, media_kind (video|photo),
  duration, technical_shots[{id,start,end}],
  accepted_observation summary fields (no paths, no filenames as evidence)
graphic_plans (accepted metadata only)
limits from §23 decisions
```

---

## 7. Source-Range-Auflösung

### LLM-Absicht (nicht final)

Beispiele: Anfang/Mitte/Ende eines Technical Shots; bevorzugte Aktion;
gewünschte Dauer; Bewegungsrichtung; Kontinuitätsfunktion.

### Python-Auflösung (final)

Python prüft und berechnet:

- gültige Working-Media-Dauer
- gültige Technical-Shot-Grenzen
- verfügbare Source-Range und Handle-Anforderungen
- exakte Source-In/Out (Sekunden + Frames)
- Framerundung analog Narration-Timebase-Regeln
- keine negative Range; keine Range außerhalb der Quelle
- keine unbegründete Wiederverwendung derselben Range (Limits §23)
- Bilddauer bei Fotos (keine Video-Source-Range)
- Timebase-Konsistenz

**Keine Source-Range darf allein aus einer LLM-Zahl übernommen werden.**

Ablauf:

```text
LLM source_range_intent
→ Python candidate window from TechnicalShot + WorkingMedia
→ clamp to handles (E5)
→ round to frames
→ validate duration ∈ [min,max] for strategy
→ persist technical_source_* fields
```

Bei Out-of-bounds: blockieren (`source_range_out_of_bounds`) oder — wenn klar
reproduzierbar — deterministisch clampen und als Repair protokollieren.

---

## 8. Fotos, Visual-only und geplante Grafiken

### Foto (`local_photo`)

- verwendet validiertes completed Working Media
- besitzt keine technische Video-Source-Range (`technical_source_*` = null)
- erhält Timeline-Dauer (Grenzen E7)
- darf Ken-Burns-/Bewegungsabsicht als Metadatum tragen
- noch keine Effektberechnung oder OTIO-Animation

### Intentional Visual Only (`intentional_visual_only`)

- entspricht explizitem Narration-Entry (`visual_only` / redaktioneller Atemraum)
  oder bewusster Bridge ohne Voice
- keine Voice-Zuordnung erforderlich
- benötigt dennoch zulässige visuelle Strategie (lokales Asset oder akzeptierte Pause-Fläche)
- Feasibility prüft positive Dauer und Timeline-Abdeckung

### Planned Graphic (`planned_graphic`)

- referenziert akzeptierten `GraphicPlan`
- ist kein Working Media
- bleibt technisch nicht exportfähig
- erzeugt blockierenden Feasibility-Hinweis (`planned_graphic_not_exportable`)
  bis echtes Working Media vorliegt
- kein Platzhalterasset und keine automatische Ersatzdatei

---

## 9. Humanity & Authenticity Review

Eigenständiger Pflichtschritt **nach** Visual Edit Plan.

### Ebene A — Redaktionelle Bewertung (Fake LLM)

Gateway-Operation `humanity_review` bewertet die Kategorien aus §5.5
gegen Plan + strukturierte Observation-Metadaten + deterministische Signale.

### Ebene B — Deterministische Signale (Python)

Mindestens berechnen und an das Review anbinden:

| Signal | Regel (Alpha) |
|---|---|
| Asset-Wiederholungszahl | Zähler pro `asset_id` im Plan |
| Identische/überlappende Source-Ranges | Paarweise Intervallprüfung |
| Shotdauerverteilung | Varianz / Min-Max-Ratio (§23 E9) |
| Schnitte exakt an Satzgrenzen | Anteil (§23 E8, E10) |
| Ähnliche Motive in Folge | gleiche Observation-Motivklasse / gleiche Asset-ID-Folge (§23 E12) |
| Generischer / geo-unsicherer Anteil | aus Observation-Feldern (§23 E11) |
| Ungeklärte Synthetic-Risiken | Observation synthetic confidence unresolved |
| Kontinuitätsbrüche | Metadaten-Widersprüche (setting/camera jumps ohne Intent) |

Keine automatische Fakten- oder Echtheitsfreigabe.

---

## 10. Feasibility

Deterministisch prüfen (mindestens):

1. alle Shots besitzen positive Dauer
2. Shotfolge deckt Narration Timeline vollständig ab
3. keine Timeline-Überlappung
4. keine unerklärte Lücke
5. Start- und Endframes monoton
6. alle Assignment-Referenzen existieren
7. Asset gehört zum Projekt
8. Working Media ist aktuell und `completed`
9. Observation ist aktuell und `accepted`
10. Source-Ranges liegen innerhalb der Quelle
11. Technical-Shot-Zuordnungen sind gültig
12. keine Preview-/Temp-/Quarantäne-/Analysis-Frame-Quelle
13. Foto- und Videoverträge getrennt
14. Planned Graphic blockiert technische Exportfähigkeit
15. Übergangsdauern überschreiten keine Nachbarshots
16. Wiederverwendungslimits eingehalten
17. Timeline-Gesamtdauer stimmt mit Narration Timeline überein (Toleranz E14)

Gesamtbewertung `fail` bei jedem `blocking`-Issue.
`blocks_phase_13=true` für alle blocking Issues außer ausdrücklich akzeptierten
Dokumentationswarnungen (Alpha: blocking Issues werden nicht still akzeptiert;
Nutzer muss Repair wählen oder — nur bei Humanity — `accepted_risk` setzen).

---

## 11. Repair-Klassen

### Redaktionelle Repairs (Fake LLM / Nutzer)

Vorschläge, **keine stille Anwendung**:

- Shotfolge ändern, Motiv wechseln, anderen lokalen Kandidaten wählen
- Shot aufteilen / Shots zusammenführen
- Übergang ändern, Visual Breath anders nutzen
- Satzrevision als zukünftige Eskalation empfehlen (kein Auto-Script-Rewrite)

Nutzer wählt Proposals → RepairRun → neue Planversion.

### Deterministische Python-Repairs

Nur klar technische, reproduzierbare Fälle:

- Source-Range innerhalb erlaubter Handles clampen
- Framegrenzen monoton korrigieren
- Rundungsrest an definiertem Shot ausgleichen (letzter Shot der betroffenen Lücke)
- minimale Überlappung entfernen
- ungültige Null-Dauer blockieren oder nach Vertrag korrigieren (wenn Intent klar)
- Übergangsdauer an Nachbarshot begrenzen
- doppelte Kandidatenreferenz bereinigen, sofern eindeutig

Jede Reparatur wird versioniert protokolliert.
Keine willkürliche redaktionelle Ersatzentscheidung durch Python.

### Auto-Anwendung deterministischer Repairs (E16)

Bei Feasibility-Lauf: deterministisch reparierbare Issues mit
`deterministically_repairable=true` **dürfen** in einem dokumentierten
Auto-Repair-Pass angewendet werden, wenn E16 aktiv und keine Ambiguität besteht.
Anschließend **erneute** Feasibility-Validierung Pflicht.
Redaktionelle Proposals nie auto-anwenden.

---

## 12. Review- und Repair-Gate

`ready_for_editorial_review` nur wenn:

1. Visual Edit Plan aktuell (Current-ID, nicht stale/invalidated/superseded)
2. Humanity Review abgeschlossen (`completed`)
3. keine blockierenden Humanity Findings offen **oder** ausdrücklich `accepted_risk`
4. Feasibility Report abgeschlossen (`completed`) mit Assessment
   `pass` oder `pass_with_warnings` (keine offenen blocking Issues)
5. alle automatischen Repairs dokumentiert und erneut validiert
6. Planned Graphics entweder echtes Working Media besitzen **oder**
   ausdrücklich als blockierend verbleiben
   (dann **kein** Ready — siehe E22)
7. keine stale Narration-/Script-Lock-/Observation-/Working-Media-Identität
8. keine relevanten Runs aktiv

**Ready vs Planned Graphic (E22):** Solange ein Shot `planned_graphic` ohne Working Media
ist, bleibt der Plan maximal `repair_required` / Feasibility `fail` mit
`planned_graphic_not_exportable`. Ready ist in Alpha nur möglich, wenn keine solchen
Shots existieren **oder** der Nutzer den Shot auf lokale Strategie umstellt.
Keine stille Ersatzdatei.

Noch keine Exportfreigabe.

---

## 13. Schema-Vorschlag 19 (ab Schema 18)

Nur minimal notwendige Tabellen. Keine Tabellen für
`editorial_approvals`, `export_validations`, `otio_exports`,
`otio_reparse_results`, `nle_exports`.

### 13.1 `visual_edit_plans`

| Aspekt | Inhalt |
|---|---|
| Zweck | versionierter Visual Edit Plan |
| Spalten | plan_id PK, project_id, script_lock_id, narration_timeline_id, input_fingerprint, plan_version, gateway_version, model_id, prompt_version, schema_version, status, total_shot_count, expected_visual_duration_seconds, accepted_risks_json, created_at, artifact_relpath |
| FKs | project; logische Refs Lock/Timeline |
| Unique | (project_id, plan_version); plan_id |
| Status | §5.1 |
| Historie | neue Version bei Repair; alte `superseded` |
| Current | über `visual_edit_project_state.current_visual_edit_plan_id` |
| Stale | bei Inputänderung → `invalidated` / Clear Current |
| JSON | accepted_risks, optional uncertainty summary |
| Warum jetzt | Kernartefakt Phase 12 |

### 13.2 `editorial_shots`

| Aspekt | Inhalt |
|---|---|
| Zweck | geplante Editorial Shots |
| Spalten | shot_id PK, plan_id, ordinal, shot_function, timeline_start/end seconds+frames, duration_seconds, transition/continuity/rhythm_intent, media_strategy, priority, uncertainty_notes_json, status |
| FKs | plan_id → visual_edit_plans |
| Unique | (plan_id, ordinal); shot_id |
| Historie | an Planversion gebunden |
| JSON | uncertainty_notes |
| Warum jetzt | Shotliste |

### 13.3 Join-Tabellen

`editorial_shot_narration_entries` · `editorial_shot_sentences` ·
`editorial_shot_visual_beats` · `editorial_shot_visual_intents`

Je: `(shot_id, *_id)` PK/Unique, FK shot_id.
Zweck: Many-to-Many ohne JSON-only-Verlust der Abfragbarkeit.
Historie: mit Shot/Planversion; bei neuer Planversion neu geschrieben.
Warum jetzt: Modelltrennung und Testbarkeit.

### 13.4 `shot_media_assignments`

| Aspekt | Inhalt |
|---|---|
| Zweck | Asset-/Working-Media-Zuordnung + aufgelöste Ranges |
| Spalten | assignment_id, shot_id, asset_id, working_media_id, technical_shot_id NULL, visual_observation_id, assignment_priority, source_range_intent_json, technical_source_in/out seconds+frames NULL, duration_seconds, selection_rationale, status |
| Unique | assignment_id; optional (shot_id, assignment_priority) |
| JSON | source_range_intent |
| Warum jetzt | Python-Range-Wahrheit |

### 13.5 `shot_transitions`

Übergänge zwischen Shots; Spalten analog §5.4; FK from/to shot_id; Unique transition_id;
Unique (plan_id, from_shot_id, to_shot_id) über plan_id am Shot.

### 13.6 `humanity_reviews` / `humanity_findings`

Reviews versioniert pro Plan; Findings mit Kategorie/Schwere/Nutzerstatus.
Current: `current_humanity_review_id`.
Stale wenn Planversion wechselt oder Input-Fingerprint.

### 13.7 `feasibility_reports` / `feasibility_issues`

Analog; Current: `current_feasibility_report_id`.
Metrics JSON erlaubt (Wiederholungszähler, coverage seconds, …).

### 13.8 `repair_proposals` / `repair_runs` / `repair_results`

Proposals an Plan gebunden; Runs verbinden Input→Output-Plan;
Results protokollieren Änderungen.
Current: `current_repair_run_id` (letzter abgeschlossener oder aktiver — aktiv hat Vorrang in Sperrlogik).

### 13.9 `visual_edit_project_state`

| Spalte | Zweck |
|---|---|
| `project_id` PK | 1:1 |
| `current_visual_edit_plan_id` | Current Plan |
| `current_humanity_review_id` | Current Humanity |
| `current_feasibility_report_id` | Current Feasibility |
| `current_repair_run_id` | Current/letzter Repair-Run |
| `current_script_lock_id` | gebundener Lock (Spiegel/Check) |
| `current_narration_timeline_id` | gebundene Timeline |
| `updated_at` | Audit |

Current-Auswahl **nur** über diese expliziten IDs, nie `ORDER BY created_at`.

---

## 14. Current State und Stale-Regeln

### Current-IDs

- `current_visual_edit_plan_id`
- `current_humanity_review_id`
- `current_feasibility_report_id`
- `current_repair_run_id`
- zugrunde liegender Script Lock
- zugrunde liegende Narration Timeline

### Stale mindestens bei

| Ereignis | Wirkung |
|---|---|
| Script Lock ersetzt/invalidiert | Plan + Reviews + Feasibility invalidated; Currents clear |
| Narration Timeline geändert | ditto |
| Pause- oder Voice-Input geändert (Timeline stale) | ditto |
| Scriptstruktur geändert | ditto |
| Coverage oder Gap-Entscheidung geändert | ditto |
| Observation Set geändert | ditto |
| Working Media oder Analysis Identity geändert | ditto |
| Technical Shots geändert | ditto |
| Visual Edit Plan repariert (neue Version) | alte Humanity/Feasibility stale; neu erforderlich |
| Humanity-Review-Version / Prompt geändert | Review stale |
| Feasibility-Profilversion geändert | Report stale |

Historische Artefakte bleiben erhalten.
Keine Current-Auswahl nach `created_at`.

---

## 15. Text-Gateway

Bestehenden `DiscoveryTextGateway` erweitern um:

| Operation | Antwortmodell |
|---|---|
| `visual_edit_plan` | strukturierter Planentwurf (Shots, Intents, Assignments-Absichten, Transitions) |
| `humanity_review` | Findings + overall_judgment |
| `editorial_repair_proposal` | Liste RepairProposal (editorial) |

### Verbindlich

- nur `FakeTextAdapter` in diesem Auftrag / Alpha-Gate
- kein realer Provider
- strukturierte Pydantic-Antworten (`extra="forbid"`)
- keine Medienbinärdaten
- keine absoluten Pfade
- keine Dateinamen als visuelle Evidenz
- Prompt- und Schema-Versionierung
- begrenzte Retries (analog Editorial: max 2 Retries bei schema_mismatch; permanente Logikfehler ohne Retry)
- kein stiller Fallback
- Cache nach exakter Inputidentität (Fingerprint + operation + versions)

Python validiert Referenzen und löst Source-Ranges **nach** Gateway-Antwort.

---

## 16. Artefaktpfade

Nur unter `_otio_v2/editing/`:

```text
editing/plans/
editing/humanity_reviews/
editing/feasibility/
editing/repairs/
editing/runs/
editing/reports/
editing/temp/
editing/latest_visual_edit_plan.json
editing/latest_humanity_review.json
editing/latest_feasibility_report.json
editing/latest_repair_run.json
```

Regeln:

- ausschließlich relative Pfade persistieren
- kein `_otio`, kein doppeltes `_otio_v2`, kein `..`
- keine Working-Media-Datei verändern
- keine Narration- oder Analysis-Artefakte überschreiben
- JSON in eigenem Temp schreiben → erneut parsen/validieren → atomar publizieren
- SQLite bleibt interne Wahrheit; Latest-JSON ist Convenience-Spiegel

---

## 17. Runs und Sperren

### Scopes

`visual_edit_plan_only` · `humanity_review_only` ·
`feasibility_check_only` · `editorial_repair_only`

### Sperrmodell (E18)

- Maximal **ein** aktiver Phase-12-Run pro Projekt (alle Scopes teilen ein Gate).
- Gegenseitig konservativ sperren mit: Narration, Editorial, Supplementation, Analysis.
- Während eines Phase-12-Runs dürfen sich zugrunde liegende Inputs nicht ändern
  (Fingerprint-Check am Start und vor Publish).

### Recovery

- orphan `queued`/`running` → `failed`
- Attempt → `interrupted` / `worker_interrupted`
- nur eigener Temp unter `editing/temp/` wird bereinigt
- veröffentlichte gültige Artefakte bleiben
- kein Gatewayaufruf während Recovery
- keine automatische Fortsetzung
- expliziter Neustart und Cache-Reuse nach Inputidentität

---

## 18. MANUAL-UI

Neue Discovery-Seite, bevorzugt Titel **Visual Edit**
(`url_path` z. B. `discovery-visual-edit`; Konstante analog bestehender Pattern).

### Anzeigen

- Inputstatus: wirksamer Script Lock, Narration Timeline, Timebase, Gesamtdauer
- Working-Media- und Observation-Status
- Visual Edit Plan: Shotliste, Shotfunktion, Narration-/Satz-/Beat-/Intent-Bezüge,
  Timeline-Zeiten, Asset + Working Media, Source-Range, Übergang, Unsicherheiten
- Humanity: Findings nach Kategorie, Schweregrad, Evidenz, Nutzerentscheidung, offene Risiken
- Feasibility: Issues, blockierend ja/nein, Source-Range-/Timeline-Probleme,
  Planned Graphics, Wiederverwendung
- Repair: redaktionelle Vorschläge, deterministische Reparaturen,
  erwartete Änderungen, Nutzerauswahl, neue Planversion

### Buttons (explizit, kein Auto)

1. **Visual Edit Plan erzeugen**
2. **Humanity & Authenticity prüfen**
3. **Technische Machbarkeit prüfen**
4. **Ausgewählte Reparaturen anwenden**

Keine automatische Aktion beim Streamlit-Rerun.

### UI-No-I/O

Beim Rendering verboten:

- Text-Gateway, Jobstart
- Medienöffnung, Frameöffnung, FFmpeg/ffprobe
- Hashing, Medien-stat
- Source-Range-Berechnung
- automatische Feasibility / automatische Repair-Anwendung
- direkte SQLite-Abfrage in Streamlit
- OTIO-Erzeugung

Nur Application Services und persistierte Viewmodels.

---

## 19. Fehlercodes

Mindestens:

`script_lock_missing` · `script_lock_invalidated` ·
`narration_timeline_missing` · `narration_timeline_stale` ·
`visual_edit_input_stale` · `visual_edit_gateway_unconfigured` ·
`visual_edit_response_invalid` · `visual_edit_response_schema_mismatch` ·
`invalid_narration_entry_reference` · `invalid_sentence_reference` ·
`invalid_visual_beat_reference` · `invalid_visual_intent_reference` ·
`invalid_asset_reference` · `invalid_working_media_reference` ·
`invalid_observation_reference` · `invalid_technical_shot_reference` ·
`invalid_shot_timeline` · `invalid_source_range` · `source_range_out_of_bounds` ·
`planned_graphic_not_exportable` · `humanity_review_invalid` ·
`humanity_blocking_finding` · `feasibility_check_failed` ·
`feasibility_blocking_issue` · `repair_proposal_invalid` ·
`repair_conflict` · `repair_validation_failed` ·
`visual_edit_run_already_active` · `narration_run_already_active` ·
`analysis_run_already_active` · `editorial_run_already_active` ·
`supplementation_run_already_active` ·
`visual_edit_artifact_conflict` · `visual_edit_registry_write_failed` ·
`visual_edit_artifact_write_failed` · `worker_interrupted` · `report_write_failed`

Keine Secrets, Vollpayloads oder absoluten Medienpfade in Fehlertexten.

---

## 20. Testplan (Gruppen)

1. Schema 18 → 19 + Datenhalt; idempotent; nur Phase-12-Tabellen
2. Domainmodelltrennung (Sentence/Beat/Intent/Editorial/Technical/Asset/Source Group)
3. aktueller Script Lock und Narration Timeline als Pflichtinput
4. Fake Visual Edit Gateway; kein Netzwerk
5. keine Medienbinärdaten im Gateway
6. Shot über mehrere Sätze
7. mehrere Shots pro Satz
8. Schnitt nicht nur an Satzgrenzen
9. Source Group erzeugt keine Kapitel oder Shotfolge
10. Technical Shot erzeugt keinen Editorial Shot automatisch
11. Assignment nur auf aktuelles Working Media
12. accepted Observation erforderlich
13. Source-Range-Auflösung (Python final)
14. Fotos
15. Visual-only-Intervalle
16. Planned Graphic bleibt nicht exportfähig
17. Humanity-Kategorien
18. deterministische Humanity-Signale
19. Asset-Wiederholung
20. Satzgrenzen-Schnitte
21. Shotdauer-Varianz
22. geografische Unsicherheit
23. mögliche Synthetic-Risiken
24. Feasibility-Timeline
25. Feasibility-Source-Ranges
26. Übergangsdauern
27. deterministische Repairs
28. redaktionelle Repairs nicht automatisch
29. Repair-Historie
30. Stale-Matrix
31. Current State (explizite IDs)
32. Cache nach Inputidentität
33. Recovery (kein Gateway)
34. UI-No-I/O
35. keine Phase-13-Funktion / keine OTIO-Erzeugung
36. Classic-/Without-VO-Regression
37. `_otio_v2`-Isolation
38. Fake-End-to-End-Smokes A–H

---

## 21. Fake-End-to-End-Smokes

| ID | Inhalt |
|---|---|
| **A** | wirksamer Lock → aktuelle Timeline → Fake Visual Edit Plan → Humanity → Feasibility → Repair → `ready_for_editorial_review` |
| **B** | ein Satz mit mehreren Shots; ein Shot über mehrere Sätze; Schnitt außerhalb Satzgrenze; keine Source-Group-Kapitelbildung |
| **C** | Videoassignment innerhalb Technical Shot; Python löst gültige Range; Out-of-bounds blockiert oder deterministisch repariert |
| **D** | Preview/Temp/Original/Analysis Frame abgelehnt; nur aktuelles completed Working Media |
| **E** | generischer Stock-/Wiederholungs-/Satzgrenzen-Risikofall; blockierendes Finding; kein Ready ohne Entscheidung oder Repair |
| **F** | GraphicPlan vorhanden, kein Working Media; Feasibility blockiert; kein Ersatzasset |
| **G** | Plan repariert; alte Humanity/Feasibility stale; neue Prüfungen erforderlich; Historie bleibt |
| **H** | Orphan-Run; eigener Temp bereinigt; kein Gateway bei Recovery; UI zweimal rendern ohne Medien-I/O, Jobstart, OTIO |

Pro Smoke berichten: Lock-ID, Timeline-ID, Plan-ID/Version, Shot-IDs, Assignment-IDs,
Source-Ranges, Humanity-Review-ID, Feasibility-Report-ID, Repair-Run-ID, Status,
Fehlercodes, Adapteraufrufe, PASS/FAIL.

---

## 22. Modulvorschlag

```text
otio_app/discovery_v2/
  domain/visual_edit.py
  adapters/visual_edit_job_launcher.py
  application/visual_edit_plan_service.py
  application/humanity_review_service.py
  application/feasibility_service.py
  application/visual_edit_repair_service.py
  application/visual_edit_job_recovery.py
  persistence/visual_edit_repository.py
  editing_paths.py
  jobs/visual_edit_worker.py
  ui/visual_edit_page.py
```

Text-Gateway-/Fake-Adapter um
`visual_edit_plan` / `humanity_review` / `editorial_repair_proposal` erweitern.

UI-Routing: `PAGE_DISCOVERY_VISUAL_EDIT` + `_build_discovery_v2_pages`.

---

## 23. Entscheidungen (verbindlich für Implementierungsauftrag)

| # | Thema | Entscheidung |
|---|---|---|
| E1 | Max. Shots / Narration-Minute | **12** Shots / Minute Gesamttimeline; darüber `shot_density_warning` (Humanity warning); hart blockierend erst ab **20**/min (`invalid_shot_timeline`) |
| E2 | Alpha-Shotdauer | Video/Visual: **min 0.80 s**, **max 12.0 s**; Ausnahme Closing/Hold bis **16.0 s** mit `shot_function` in {`closing`,`hold`} |
| E3 | Max. Asset-Wiederverwendung | dasselbe `asset_id` ≤ **3** Assignments / Plan; darüber blocking Feasibility `feasibility_blocking_issue` (reuse) |
| E4 | Max. gleiche Source-Range | identische oder ≥90 %-überlappende Video-Range ≤ **1** Nutzung; zweite Nutzung blocking unless Repair merged shots |
| E5 | Min. Source-Handles | **0.10 s** Handle vor/nach gewünschter Range innerhalb Technical Shot und Working Media; wenn Technical Shot kürzer: Handle = 0 und Uncertainty-Note Pflicht |
| E6 | Übergangsdauer | Cut: **0**; Dissolve/Fade: **0.10–0.80 s**; darf keine Nachbarshot-Dauer überschreiten; sonst clamp + deterministic repair |
| E7 | Foto-Dauer | **min 1.20 s**, **max 6.0 s** Timeline-Dauer |
| E8 | Narration-Pausen | Pause-/visual_only-Entries sind **Schnittflächen-Kandidaten**; Fake darf schneiden oder überbrücken; kein Zwangs-Schnitt; überbrückende Shots müssen Entry-IDs referenzieren |
| E9 | Schnitt exakt an Satzgrenze | Shotgrenze liegt exakt auf Sentence-End-Frame (±0 Frames) **und** benachbarter Shot beginnt dort → zählt als sentence-boundary cut |
| E10 | Satzgrenzen-Schnitt-Schwelle | Anteil sentence-boundary cuts > **0.65** → Humanity `sentence_boundary_cut_risk` warning; > **0.85** → blocking finding |
| E11 | Shotdauer-Varianz | wenn max/min Dauer-Ratio < **1.25** bei ≥6 Shots → warning `shot_duration_variance` |
| E12 | Generischer Stock-Anteil | Assignments mit Observation-Signal `generic_stock_like` / niedriger Lokaldetail-Score ≥ **0.40** Anteil → warning; ≥ **0.60** → blocking |
| E13 | Ähnliche Motive in Folge | ≥ **3** aufeinanderfolgende Assignments mit gleicher Asset-ID oder gleicher Motiv-Hash-Klasse → warning; ≥ **4** → blocking |
| E14 | Timeline-Dauertoleranz | \|Σ shot durations − timeline total\| ≤ **1 Frame** in Projekt-Timebase |
| E15 | `accepted_unresolved` / offene Lock-Risiken | bleiben als Plan-`accepted_risks` sichtbar; erzeugen **keine** automatische Exportfreigabe; blockieren Ready nur wenn Kategorie in Humanity als blocking neu bestätigt wird |
| E16 | Priorität Repairs | (1) deterministische Feasibility-Auto-Repairs (eindeutig) → (2) Nutzer-gewählte redaktionelle Proposals → (3) erneutes Humanity → (4) erneutes Feasibility |
| E17 | Deterministische Auto-Repairs | bei Feasibility-Check: eindeutige technische Fixes auto + protokolliert; danach Re-Validate; Ambiguität → Proposal statt Auto |
| E18 | Current State | eigene Tabelle `visual_edit_project_state` mit expliziten Current-IDs (§13.9) |
| E19 | Sperrmodell | max. 1 aktiver Visual-Edit-Run; gegenseitig mit Analysis/Editorial/Supplementation/Narration |
| E20 | Artefaktwurzel | ausschließlich `_otio_v2/editing/` |
| E21 | Gateway | Operationen am bestehenden `DiscoveryTextGateway`; Fake only |
| E22 | Ready-Gate | nie ohne abgeschlossene Humanity + Feasibility; nie mit offenem Planned-Graphic-Blocker |
| E23 | Provider Alpha | kein realer Text/Vision/Voice/Stock; FakeTextAdapter only |
| E24 | Schema | Implementierung migriert 18 → **19** |

Keine Grenzwerte im Implementierungsauftrag improvisieren — bei Lücke stoppen und Plan nachziehen.

---

## 24. UNKNOWN-Punkte

- echte LLM-Modell-IDs, Kosten, Latenz, Prompt-Feintuning jenseits Fake
- produktive semantische Schnittqualität
- echte Synthetic-Detection-Provider
- NLE-spezifische Übergangseffekte / OTIO-Effektbinding (Phase 13)
- Ken-Burns-Parameterberechnung und Animationsexport
- Mehrbenutzer-kollaborative Review-Queues
- Cloud-Preview der Shotliste
- Adobe-/Stock-Nachlieferung während Phase 12 (bleibt gesperrt)

---

## 25. Implementierungsaufteilung (Makroauftrag)

```text
Schema 19 + Visual-Edit Domain/State
+ DiscoveryTextGateway Ops (visual_edit_plan / humanity_review / editorial_repair_proposal)
+ FakeTextAdapter Demonstrationspfad (§6)
+ Python Source-Range Resolver + Assignment Validation
+ Humanity Review (LLM + deterministic signals)
+ Feasibility Engine
+ Repair Proposals / Runs / Results (+ deterministic auto-pass)
+ Runs/Launcher/Recovery + gegenseitige Sperren
+ Visual Edit UI (MANUAL) + No-I/O
+ Tests + Smokes A–H
```

### Separates Gate (nie still aktivieren)

Echter Text-Provider; Vision; Stock; Voice/ElevenLabs; OTIO/Phase 13.

---

## 26. DoD für späteren Implementierungsauftrag

- Schema 19 + Fake-Smokes A–H grün
- Nur wirksamer Script Lock + completed Narration Timeline starten Visual Edit
- Fake Gateway ohne Netzwerk; keine Medienbinärdaten
- Modelltrennung durch Tests belegt
- Source-Ranges nur durch Python final
- Humanity eigener Schritt; Feasibility deterministisch
- Ready nur nach Gate §12
- Stale-/Invalidierung zuverlässig
- UI-No-I/O; kein Auto-Job
- kein echter Provider; kein OTIO; keine Phase-13-Funktion
- Classic/Without-VO unverändert; kein `_otio`
- Vollsuite: keine neuen Discovery-bedingten Failures; Baseline-18 unangetastet

---

## 27. Phase-12-Review-Paket (Liefergegenstand Ende Implementierung)

Persistiertes Paket unter `editing/reports/`:

1. Current Visual Edit Plan (JSON + IDs)
2. Current Humanity Review + Findings
3. Current Feasibility Report + Issues/Metrics
4. Repair-Historie (Proposals/Runs/Results)
5. Input-Fingerprint und gebundene Lock-/Timeline-IDs
6. Ready-Status und akzeptierte Risiken

Kein Export-Manifest, kein OTIO.

---

## 28. Nächste erlaubte Aktion

Nach **Freigabe dieses Plans**:

→ Phase-12-**Implementierungs**-Makroauftrag gemäß §25.

Gesperrt bis eigene Gates:

- echte Text-/Vision-/Voice-/Stock-Provider
- Phase 13 (Editorial Approval, Export Validation, OTIO, Reparse)
- NLE-Export
