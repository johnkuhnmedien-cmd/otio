# Unified Cut — Prompt/Schema-Paket für Schnittmuster-MDs

Stand: Branch mit Unified Cut + Keyword-Sync (Enhanced).  
Zweck: Head of Dev kann `cut_styles/*.md` ohne Rate-Felder formulieren.

---

## 0. Architektur-Empfehlung (Engineering + Product)

**MD ersetzt nur die redaktionelle Stilpolitik.**  
Fest im Code-Wrapper bleiben:

- JSON-Output-Schema + Parser-Invarianten
- Kapitel-Scope / ID-Prefix
- Timing-Vertrag (`offset_seconds` relativ zum Satzanfang)
- Coverage-Gap-Pflicht für `{weak, none}`
- keine absoluten Timeline-Sekunden
- gültige Enums (`position`, `alignment`, `asset_fit`)

**Rhythmus-Modus im Produkt nicht umbauen** (fester Code-Prompt bleibt Source of Truth).  
`rhythm.md` höchstens Doku-Spiegel.

Geplantes Call-Paket (Zielbild):

1. Stil-MD (editorial)
2. Locked-Script-Slice (VO-Text)
3. Slim Inventory
4. Cut Settings (alle, inkl. shot_min/max)
5. Sentence Timings (+ Segment Timings wie heute)

UI später: Projekt-Default + Override pro Kapitel.

---

## 1. Aktueller Prompt-Builder

### Dateien
- `otio_app/services/without_voiceover_enhanced/script_prompts.py`
  - `build_unified_cut_prompt` → **Rhythmus**
  - `build_keyword_sync_unified_cut_prompt` → **Keyword-Sync**
  - `DEFAULT_CUT_RHYTHM_TARGETS` (nur Rhythmus)
- `otio_app/services/without_voiceover_enhanced/cut_plan_service.py`
  - `generate_unified_cut_for_folder` wählt Prompt nach `options.unified_cut_style`
- `otio_app/services/without_voiceover_enhanced/cut_plan_options.py`
  - `format_shot_constraints_for_prompt(options)` → Settings-Block (heute **nur Rhythmus**)

### Getrennte Promptfunktionen?
**Ja.**
- Rhythmus: `build_unified_cut_prompt` + `DEFAULT_CUT_RHYTHM_TARGETS` + `format_shot_constraints_for_prompt`
- Keyword: `build_keyword_sync_unified_cut_prompt` (ohne Rhythm-Block; Settings-Block derzeit **nicht** angehängt — Produktziel künftig: Settings inkl. min/max mitgeben)

### Reihenfolge im Prompt (beide)
1. Rolle / Modusregeln / Kapitel-Scope  
2. Editorial-Regeln (Timing, Boundaries, Slots, Pausen, Gaps)  
3. OUTPUT SCHEMA (JSON)  
4. FINAL VALIDATION  
5. **LOCKED SCRIPT** (JSON)  
6. **SEGMENT TIMINGS** (JSON)  
7. **SENTENCE TIMINGS** (JSON, wenn vorhanden)  
8. **LOCAL ASSETS** slim (JSON)  
9. STYLE PROFILE  
10. DRAMATURGY  
(+ optional USED-IN LEDGER, MIDDLE-FRAME VISION)

### Was das MD ersetzen soll
Nur die **editorialen Stilregeln** (Do/Don’t, Schnittphilosophie, Keyword-Strenge, Beispiele).  
**Nicht** ersetzen: Schema-Block, Kapitel-Scope, ID-/Safety-Regeln, Input-Anhänge.

---

## 2. Exaktes Output-Schema (echte Feldnamen)

Quelle: `models.py` → `UnifiedCutPlanDocument`, `CutBoundary`, `CutSlot`, `PauseDirective`.  
Parser: `unified_cut_plan.parse_unified_cut_response`.

### Wichtig: keine `start_boundary_id` / `end_boundary_id`
Slots sind **implizit** zwischen `boundaries[i]` und `boundaries[i+1]`.  
Invariante: `len(slots) == len(boundaries) - 1`.

### Top-Level
```json
{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "pause_directives": [],
  "boundaries": [],
  "slots": []
}
```

### Boundary
```json
{
  "cut_id": "Yosemite_cut_000",
  "sentence_id": "Yosemite_segment_001__s001",
  "position": "start",
  "offset_seconds": null,
  "alignment": "sentence_boundary"
}
```

Enums:
- `position`: `start | early | middle | late | end` (oder null, wenn nur offset)
- `alignment`: `mid_sentence | sentence_boundary | in_pause`
- braucht `position` **oder** `offset_seconds`
- wenn beide gesetzt: **`offset_seconds` gewinnt**
- `offset_seconds` = Sekunden **ab Satzanfang** (`sentence.start_seconds`), nicht Segmentanfang
- negative Offsets: nicht vorgesehen / nicht nutzen
- **keine Word-Timestamps** im LLM-Input — Onset aus Satztext + Proportion schätzen

### Slot
```json
{
  "slot_id": "Yosemite_slot_001",
  "local_asset_id": "yo_waterfall_01",
  "asset_fit": "strong",
  "asset_fit_reason": "...",
  "visual_intent": "...",
  "narrative_function": "evidence",
  "coverage_gap_id": null,
  "source_range_intent": "representative_middle_section",
  "needed_visual": "",
  "search_concepts": [],
  "must_include": [],
  "must_avoid": [],
  "desired_motion": "static|pan|tilt|tracking|drone|handheld|zoom|unknown",
  "desired_framing": "close|medium|wide|aerial|pov",
  "preferred_media_type": "video|photo|either",
  "fact_check_required": false,
  "covered_sentence_ids": ["Yosemite_segment_001__s001"]
}
```

Feld heißt **`local_asset_id`**, nicht `asset_id` (im Unified-Output).

### Pause
```json
{
  "after_segment_id": "Yosemite_segment_001",
  "after_sentence_id": "Yosemite_segment_001__s004",
  "pause_function": "breath|emphasis|anticipation|reveal|chapter_transition|reflection|no_pause",
  "duration_class": "short|medium|long",
  "visual_behavior": "hold_current_shot|next_shot_may_start_during_pause|cut_at_pause_start|cut_at_pause_end|editorial_choice",
  "editorial_reason": "..."
}
```
Keine Pausendauer in Sekunden im LLM-Output.

### Coverage Gap (abgeleitet, nicht Top-Level LLM-Output)
LLM schreibt Gap-Felder **inline am Slot**.  
Python leitet `CoverageGapsDocument` ab via `unified_to_rough`:

```json
{
  "gap_id": "Yosemite_gap_001",
  "related_shot_ids": ["Yosemite_slot_001"],
  "needed_visual": "prose…",
  "editorial_purpose": "evidence",
  "preferred_media_type": "video",
  "search_concepts": ["waterfall mist", "cascade wide shot"],
  "search_queries": ["waterfall mist", "cascade wide shot"],
  "must_include": [],
  "must_avoid": [],
  "desired_motion": "",
  "desired_framing": "",
  "fact_check_required": false,
  "covered_sentence_ids": ["…"],
  "reason": "…",
  "priority": "high|medium",
  "target_duration_seconds": null
}
```

Regel heute:
- Gap nur wenn `asset_fit in {weak, none}`
- **1 Gap ↔ 1 Slot** (`related_shot_ids = [slot_id]`)
- `search_concepts`: 2–4 englische Keyword-Phrasen, 2–5 Wörter
- Funnel nutzt Gaps aus `coverage_gaps.json`

---

## 3. Asset-Fit — IST vs. Empfehlung HoD

### IST (Code + Prompt heute)
| fit | Asset | Gap |
|---|---|---|
| `strong` | zuweisen | nein (`coverage_gap_id` null) |
| `acceptable` | zuweisen | nein |
| `weak` | **bestes lokales Asset behalten** | **ja (Upgrade-Gap)** |
| `none` | `local_asset_id = null` | **ja (required gap)** |

Validierung:
- `ASSET_FIT_VALUES` / `CutSlot._normalize_asset_fit` in `models.py`
- Parser setzt bei `none` Asset auf null; bei `weak/none` Gap-ID nach
- `GAP_FIT_VALUES = {weak, none}` → Funnel

### Empfehlung HoD (Produktentscheidung offen)
`weak` und `none` **nie** als finale Slot-Zuordnung — immer Gap, kein weak Asset behalten.

**Das ist heute NICHT so implementiert.**  
Wenn gewünscht: globale Wrapper-Policy ändern (nicht nur MD).  
Bis dahin MDs an **IST** ausrichten oder explizit „Zielpolitik“ kennzeichnen.

---

## 4. Slim Inventory (LLM-Input)

Quelle: `inventory/{folder}.slim.json` → `slim_assets_from_slim_document`  
(`otio_app/services/inventory_prompt_view.py`)

Beispiel-Row (englische Prompt-Keys):
```json
{
  "local_asset_id": "yo_waterfall_01",
  "asset_id": "yo_waterfall_01",
  "folder": "Yosemite",
  "file": "waterfall_wide.mp4",
  "duration_seconds": 12.4,
  "media_type": "video",
  "description": "wide waterfall mist among pines",
  "usable_in_s": 0.8,
  "motion": "static",
  "framing": "wide",
  "people": "",
  "people_action": "",
  "defects": []
}
```

Hinweise:
- `media_type`: `video` | `image`/`photo` (photo→image Mapping möglich)
- `folder` explizit
- `usable_in_s` = Schwarz-/Lead-In; nutzbare Länge ≈ `duration_seconds - usable_in_s`
- **keine vorab `asset_fit`** im Inventory
- Standbilder: `media_type` photo/image, oft ohne `duration_seconds`
- Supplements sind **nicht** Teil des Kapitel-Slim-Inventars beim Unified-LLM (kommen später über Funnel)

---

## 5. Cut Settings

Quelle: `CutPlanOptions` in `cut_plan_options.py`

| Feld | Bedeutung |
|---|---|
| `shot_min_sec` | Shot-Min (Rhythmus enforced in Python; Keyword aktuell relaxed — Ziel: wieder mitgeben) |
| `shot_max_sec` | Shot-Max |
| `video_head_trim_sec` | Head-Trim (Python) |
| `max_asset_usage` | max. Asset-Nutzung filmweit (Intro zählt nicht) |
| `min_asset_reuse_distance_shots` | Reuse-Abstand |
| `voiceover_preroll_sec` + `_mode` | Kapitel-Vorlauf (`fixed`/`llm`) |
| `voiceover_postroll_sec` + `_mode` | Kapitel-Nachlauf |
| `short_asset_tolerance_sec` | Toleranz „Asset zu kurz“ |
| `include_middle_frames` / `max_middle_frames_per_chapter` | Vision |
| `unified_cut_style` | `rhythm` \| `keyword_sync` |
| Funnel: `max_candidates_per_gap`, `max_full_download_attempts_per_gap` | eher Funnel als Cut-LLM |

**Keine** Keyword-spezifischen Settings im Code (kein onset tolerance / max offset Feld).  
Keyword-Onset-Regeln stehen heute nur im Prompt-Text.

Rhythmus hängt Settings via `format_shot_constraints_for_prompt` ein.  
Keyword-Prompt heute **ohne** diesen Block (Produktziel: künftig alle Settings inkl. min/max).

---

## 6. Timing / Sentences

LLM bekommt Sentence-Rows:
```json
{
  "sentence_id": "Yosemite_segment_001__s001",
  "segment_id": "Yosemite_segment_001",
  "text": "A roaring waterfall behind the pines.",
  "start_seconds": 0.0,
  "end_seconds": 4.2
}
```
- Zeiten **relativ zur Segment-Audio**
- **keine `words[]`** im Prompt
- Keyword-Onset: `alignment=mid_sentence` + `offset_seconds` vom Satzanfang
- First boundary: erste Satz-`start`, `position=start`, `offset_seconds=0/null`, `alignment=sentence_boundary`
- Last boundary: letzter Satz-`end`, `position=end`, kein Keyword-`mid_sentence`

Segment timings: `SegmentTimingsDocument` (segment_id, audio_path, duration_seconds, …).

---

## 7. Coverage-Gap-Logik (kurz)

- LLM: Gap-Felder am Slot bei `weak`/`none`
- Parser/Python: Gap-Dokument ableiten → Supplement-Funnel
- `weak`: Asset darf heute bleiben + Upgrade-Gap
- `none`: Asset null + Gap
- Suche: `search_concepts` (engl. Keywords)
- `media_type` am Gap: `preferred_media_type` am Slot
- `reason`: aus `asset_fit_reason` / Defaults

---

## 8. Call-Beispiele (synthetisch, schema-treu)

### A) Gut — Keyword-Cut
Boundary mid-sentence mit offset; Slot strong mit passendem Asset; last boundary = VO end.

### B) Problematisch (gegen Regeln)
- Buzzword „waterfall“, Asset ist Bridge/Valley (`strong` gelogen)
- oder `asset_fit=weak` ohne `search_concepts`
- oder last boundary als Keyword-`mid_sentence`
- oder `position: "mid_sentence"` (muss in `alignment`)

(Echte Projekt-JSONs bitte separat anonymisiert nachliefern, falls vorhanden.)

---

## 9. Relevante Tests
- `tests/test_unified_cut_plan_phase1.py`
- `tests/test_unified_cut_plan_phase2.py` (inkl. Keyword-Prompt-Contract)
- `tests/test_unified_timeline_phase3.py` (Timing, shot_min/max, Keyword-Skip)
- `tests/test_unified_phase6_phase7.py`
- `tests/test_enhanced_cut_plan_options_settings.py`
- `tests/test_without_voiceover_enhanced_intro_cut.py` (Intro Keyword-Sync Referenz)
- `tests/test_chapter_cut_service.py`

---

## 10. Was die MD-Library liefern soll

```
cut_styles/
├── README.md
├── _global_contract.md      # optional Doku; Enforcement besser im Code-Wrapper
├── rhythm.md                # Doku-Spiegel, Code bleibt aktiv
├── keyword_sync.md
├── list_hook.md
├── establishing_detail.md
├── geography_walk.md
├── reveal_hold.md
├── atmosphere_bed.md
└── chapter_open_close.md
```

Stil-MDs nur:
- Schnittphilosophie
- Do / Don’t
- Timing-Interpretation
- Asset-Match-Strenge
- Boundary-Verhalten
- stiltypische Beispiele mit **echten Feldnamen**

---

## Offene Produktentscheidung (bitte bestätigen)

**weak-Handling:**
- A) IST: weak = Asset behalten + Upgrade-Gap  
- B) HoD: weak/none = kein Asset + Gap  

Engineering wartet auf Entscheidung, bevor globale Wrapper-Policy geändert wird.
