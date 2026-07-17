# Phase 11 — Voice, Pausenregie und Timing Plan

**Auftrags-ID:** `DISCOVERY-V2-PHASE11-VOICE-PAUSE-TIMING-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung
**Basis-HEAD:** `33e7671c0bbe59331bf3db578606f88cf2537b15`
**Registry-Ausgang:** Schema **16**
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente

---

## 0. Konfliktprüfung gegen höhere SoT und Phase 10

| Thema | Quelle | Plan-Konformität |
|---|---|---|
| Script Lock vor Voice / ElevenLabs | MASTER_PLAN / PIPELINE_SPEC / ALPHA_SCOPE | ja |
| LLM-Pausenregie vor Python-Timing | MASTER_PLAN / EDITORIAL_QUALITY / MODEL_ROUTING | ja |
| Fake Voice zuerst; echte Voice hinter Gate | MASTER_PLAN / MODEL_ROUTING / ALPHA_EXECUTION | ja |
| Keine Visual-Edit-Timeline vor finalem Narration Timing | MASTER_PLAN / ALPHA_SCOPE / ALPHA_EXECUTION | ja |
| MANUAL; kein Auto-Start beim Rerun | ALPHA_SCOPE / 01-step | ja |
| Gateways zentral; kein Adapter aus UI/Domain | 00-core / D-9-001 | ja; Voice neu, Pause über Text-Gateway |
| `_otio_v2` only; Classic `_otio/` read-only | 00-core / CLASSIC / MEDIA | ja; Artefakte unter `narration/` |
| Working Media unverändert; keine Originalmutation | MEDIA_LIFECYCLE | ja; Narration-Audio ≠ Working Media |
| Python: Zeiten/Dauern/Überlappung/Frames | EDITORIAL_QUALITY | ja |
| LLM: Pause-Funktionen, nicht Frames | EDITORIAL_QUALITY | ja |
| Phase 10 endet mit Script Lock | D-10-005 / Phase-10-Plan | ja; Phase 11 startet danach |
| Lock-Invalidierung invalidiert Narration | D-10-006 | ja; Stale-Matrix |
| Candidate ohne Intake nie im Lock | D-10-007 | Phase-10-Regression nachholen (§30) |

**Phase-10-Abgleich (Code-Stand Schema 16):**

- `get_effective_script_lock(project)` prüft Fingerprint und markiert bei Abweichung
  `invalidated`; `editorial_project_state.current_script_lock_id` wird geleert.
- Lock speichert `lock_fingerprint`, Script-/Structure-/Coverage-/Observation-/Claim-
  Snapshots; Phase 11 muss dieselben Identitäten erneut gegen den aktuellen Zustand
  prüfen (`narration_input_stale` / `script_lock_*`).
- `DiscoveryTextGateway` kennt bisher `narrative|script|structure|coverage`;
  `pause_direction` wird als neue Operation hinter demselben Gateway geplant.
- Gegenseitige Sperren Analysis ↔ Editorial ↔ Supplementation bestehen; Narration
  wird analog eingebunden.
- Kein Discovery-V2-Voice-/Pause-/Timing-Modul vorhanden; Classic ElevenLabs nur
  read-only Referenz, kein Reuse der Orchestrierung.

Keine kritischen SoT-Konflikte. Offenes → §29 UNKNOWN.

---

## 1. Phase-11-Grenze

### Beginnt mit

Aktuell wirksamem Script Lock (`status=locked`, Fingerprint gültig) aus Phase 10.

### Endet mit

Validierter finaler **ResolvedNarrationTimeline** (Current State gesetzt).

### In Scope

```text
gültiger Script Lock
→ Voice-Profil / Fake Voice Gateway
→ Voice Generation (Satz-Audiosegmente)
→ technische Audio-Prüfung / Voice-Review (technisch)
→ LLM-Pausenregie (pause_direction über DiscoveryTextGateway + Fake)
→ deterministische Python-Timingauflösung
→ finale Narration Timeline
→ Historie, Stale, Recovery, MANUAL-UI, lokale Audio-Smokes
```

### Explizit nicht in Phase 11

- echter ElevenLabs-Aufruf oder andere echte Voice-API
- Visual Edit Plan / Shot- oder Assetzuordnung / Source-Ranges
- Humanity Review / Feasibility / Repair
- OTIO Export
- Phase 12+

---

## 2. Verbindlicher Script-Lock-Input

Vor **jedem** Voice-, Pause- oder Timing-Run:

1. Lock existiert (`current_script_lock_id` oder effektiver locked Lock)
2. Lockstatus ist exakt `locked` (nicht `invalidated` / `superseded`)
3. `get_effective_script_lock` bestätigt Fingerprint-Gleichheit
4. Script-ID und Script-Hash stimmen mit Lock überein
5. Satzstruktur (Sentence-IDs/Ordinale/Text-Hashes) stimmt mit Structure-Fingerprint
6. Claimentscheidungs-Snapshot stimmt
7. Coverage-Fingerprint stimmt
8. Observation-Set-Fingerprint stimmt
9. keine relevante Inputversion ist stale
10. kein aktiver Analysis-, Editorial- oder Supplementation-Run
11. kein anderer aktiver Narration-Run (Voice/Pause/Timing)

Fehler mindestens:

| Code | Wann |
|---|---|
| `script_lock_missing` | kein aktueller Lock |
| `script_lock_invalidated` | Lock nicht mehr wirksam |
| `script_lock_fingerprint_mismatch` | angezeigter ≠ bestätigter / neu berechneter Fingerprint |
| `narration_input_stale` | Lock-Inputs oder Narration-Zwischenartefakte stale |

Kein historischer oder ungültiger Lock als aktueller Input.

---

## 3. Domainmodelle (Pydantic-Verträge)

Alle Adapter-/LLM-Antworten: `extra="forbid"`. IDs stabil (UUID). Keine Abhängigkeit von `created_at` für Current-Auswahl.

### 3.1 VoiceProfile

| Feld | Typ / Hinweis |
|---|---|
| `voice_profile_id` | str |
| `project_id` | str |
| `language` | str (ISO-ähnlich, z. B. Projekt-/Scriptsprache) |
| `provider` | Literal `"fake"` in Alpha; andere Provider nicht aktiv |
| `voice_identifier` | str — Fake: z. B. `fake-voice-neutral-v1`; **keine** reale Personen-/Markenstimme |
| `voice_settings_version` | str — z. B. `fake-voice-settings-v1` |
| `output_profile` | siehe E3 — WAV PCM s16le 48 kHz mono |
| `status` | `active` \| `superseded` \| `invalid` |
| `created_at` | datetime |

### 3.2 VoiceGenerationRun

| Feld | Hinweis |
|---|---|
| `run_id` | str |
| `project_id` | str |
| `script_lock_id` | str |
| `script_id` | str |
| `voice_profile_id` | str |
| `input_fingerprint` | Hash aus Lock + Profile + Sentence-Satz |
| `provider` / `adapter_version` | Fake-Identität |
| `scope` | `voice_generation_only` |
| `status` | `queued` \| `running` \| `completed` \| `failed` \| `interrupted` |
| `sentence_count` | int |
| `segments_created` / `segments_reused` / `segments_failed` | int |
| `started_at` / `finished_at` | datetime \| null |
| `error_code` | sanitized |

Unvollständiger Run darf **nicht** `completed` sein, auch wenn Teilsegmente publiziert sind.

### 3.3 VoiceSegment

| Feld | Hinweis |
|---|---|
| `segment_id` | str — Identität aus Cache-Key (§12), nicht aus Dateiname/`created_at` |
| `run_id` | str (erster erzeugender oder reusender Run) |
| `script_lock_id` | str |
| `sentence_id` | str |
| `sentence_ordinal` | int ≥ 0 |
| `text_hash` | SHA-256 des Satztexts |
| `voice_profile_id` | str |
| `audio_format` | `wav_pcm_s16le` |
| `sample_rate_hz` | 48000 |
| `channels` | 1 |
| `sample_count` | int > 0 |
| `duration_seconds` | float > 0; konsistent zu Samples/Rate |
| `byte_size` | int |
| `audio_sha256` | str |
| `relative_path` | unter `narration/audio/...` |
| `status` | `published` \| `failed` \| `superseded` |
| `created_at` | datetime |

Ein Segment ist an exakte Satz- und Voice-Konfiguration gebunden. Kein Segment für leeren Text.

### 3.4 PauseDirectionPlan

| Feld | Hinweis |
|---|---|
| `pause_plan_id` | str |
| `script_lock_id` | str |
| `voice_run_id` | str |
| `prompt_version` / `model_identifier` / `gateway_version` / `response_schema_version` | Text-Gateway-Identität |
| `provider` | `fake` |
| `input_fingerprint` | Lock + Voice-Run + Segmentfingerprint + Prompt/Schema |
| `global_notes` | list[str] oder str |
| `status` | `draft` \| `completed` \| `failed` \| `stale` \| `superseded` |
| `created_at` | datetime |

### 3.5 PauseDirection

| Feld | Hinweis |
|---|---|
| `direction_id` | str |
| `pause_plan_id` | str |
| `position_kind` | `before_sentence` \| `after_sentence` \| `between_sentences` \| `timeline_start` \| `timeline_end` |
| `sentence_id` | nullable |
| `segment_id` | nullable |
| `anchor_ordinal` | int \| null — zur Validierung der Position |
| `function` | siehe Enum unten |
| `min_duration_intent_s` | float ≥ 0 |
| `preferred_duration_intent_s` | float ≥ 0 |
| `max_duration_intent_s` | float ≥ 0; `min ≤ preferred ≤ max` |
| `hardness` | `hard` \| `soft` |
| `rationale` | str |
| `uncertainty` | `low` \| `medium` \| `high` |

**Funktionen (verbindlich):**

`cold_open` · `hook_breath` · `sentence_transition` · `section_transition` ·
`emphasis` · `visual_breath` · `closing_hold` · `no_pause`

`no_pause` erzeugt **keinen** Timeline-Eintrag.

LLM plant Funktionen und Dauerabsichten — **keine** finalen Framewerte.

### 3.6 ResolvedNarrationTimeline

| Feld | Hinweis |
|---|---|
| `timeline_id` | str |
| `project_id` | str |
| `script_lock_id` | str |
| `voice_run_id` | str |
| `pause_plan_id` | str |
| `timing_profile_version` | str — z. B. `narration-timing-v1` |
| `timebase` | siehe §9 — rationale Framerate |
| `total_duration_seconds` | float |
| `total_frames` | int |
| `entries` | geordnete Liste |
| `input_fingerprint` | Lock + Voice-Run + Pause-Plan + Timebase + Timing-Profil |
| `status` | `completed` \| `stale` \| `superseded` \| `invalid` |
| `created_at` | datetime |

### 3.7 NarrationTimelineEntry

| Feld | Hinweis |
|---|---|
| `entry_id` | str |
| `ordinal` | int ≥ 0, lückenlos |
| `entry_type` | `voice` \| `pause` \| `visual_only` |
| `sentence_id` | nullable |
| `voice_segment_id` | nullable |
| `pause_direction_id` | nullable |
| `start_seconds` / `end_seconds` / `duration_seconds` | float; `end = start + duration` |
| `start_frame` / `end_frame` | int; monoton, nicht überlappend |
| `function` | Pause-Funktion oder `speech` für Voice |
| `technical_notes` | list[str] |

Invarianten: keine negativen/Null-Dauern (außer `no_pause` ohne Entry); keine Lücken außer expliziten Pause-/Visual-only-Einträgen; Framegrenzen monoton und nicht überlappend.

### 3.8 VoiceGenerationAttempt / NarrationAttempt

Analog Editorial/Supplementation: Attempt-Zeilen mit Provider-/Fingerprint-Feldern,
Status `queued|running|completed|failed|reused|interrupted`. Getrennte Semantik
pro Scope, kein vermischter Editorial-/Stock-Run.

---

## 4. Fake Voice Gateway

```text
UI
→ Voice / Narration Application Service
→ VoiceGenerationGateway (zentral)
→ FakeVoiceAdapter
```

**Nicht erlaubt:** Adapteraufruf aus Streamlit; Gatewayaufruf aus Domain;
direkte ElevenLabs-Nutzung aus UI/Fachmodulen.

### FakeVoiceAdapter

- kein Netzwerk, keine SDKs, keine reale Stimme
- deterministische lokale WAV-Ausgabe
- nur Python-Standardbibliothek (oder bereits vorhandene Abhängigkeiten)
- **keine neue Paketinstallation**
- gleiche Inputs → bytegleiche oder vertraglich reproduzierbare Outputs
- kontrollierte Fehler simulierbar (Timeout, Invalid, Partial)

### Verbindliches Fake-Ausgabeprofil (E3)

| Parameter | Wert |
|---|---|
| Container | WAV |
| Encoding | PCM signed 16-bit little-endian |
| Sample Rate | 48 000 Hz |
| Kanäle | mono (1) |

Keine semantische Behauptung über natürliche Sprachqualität.

### Deterministische Dauerfunktion (E1)

Für Fake-Alpha aus Textlänge (Zeichen, Whitespace normalisiert):

```text
raw = clamp(len(normalized_text) * 0.055, MIN_SENTENCE_S, MAX_SENTENCE_S)
duration_s = round(raw, 3)
sample_count = max(1, int(round(duration_s * 48000)))
```

| Grenze | Wert |
|---|---|
| `MIN_SENTENCE_S` | **0.40** |
| `MAX_SENTENCE_S` | **18.0** |
| max. Satzzeichenlänge vor Ablehnung | **2000** (danach `voice_generation_failed` / invalid input) |
| max. Segmente pro Voice-Run | **500** |

WAV enthält deterministische PCM-Samples (z. B. sinusähnliches Muster aus Hash des Cache-Keys) — **kein** Anspruch auf Verständlichkeit.

---

## 5. ElevenLabs-Grenze

ElevenLabs bleibt **separates Provider-Gate**.

| Regel | Verbindlich |
|---|---|
| Gateway providerneutral | ja |
| Direkte ElevenLabs-Nutzung aus UI/Domain | nein |
| API-Keys in SQLite/JSON | nein |
| Echter Provider-Smoke ohne Freigabe | nein |
| Voice-ID / Modell / Format / Limits / Kosten | **UNKNOWN** bis offizielle Prüfung |
| Alpha-Hauptpfad | Fake Voice ausreichend |

Classic `otio_app/services/voiceover_generation/*` nur read-only Referenz; kein Orchestrierungs-Reuse.

---

## 6. Satz- und Segmentstrategie

| Regel | Entscheidung |
|---|---|
| Mapping | **ein VoiceSegment pro aktuellem Satz** |
| Leerer Text | kein Segment; Run zählt Satz als skipped/invalid laut Vertrag |
| Maßgeblich | `sentence_id` + `ordinal` aus gelockter Struktur |
| Textänderung | neuer `text_hash` → **neue Segmentidentität** |
| Cache | unveränderter Satz + identische Voice-Konfiguration → Reuse nach explizitem Start |
| Auswahl | nie über Dateiname oder `created_at` |
| Satzverschmelzung | **verboten** ohne späteren expliziten Vertrag (nicht in Phase 11) |

**Segment-ID:** deterministisch aus Cache-Key-Feldern (§12), z. B. UUID5 über kanonischen Key-String. Historische Dateien bei neuer Identität bleiben erhalten (`superseded`).

---

## 7. Technische Audioprüfung

Python-/Audioadapter validiert vor Persistenz und vor Timing:

1. Datei existiert, reguläre Datei
2. Pfad relativ unter `_otio_v2/narration/audio`
3. WAV-Header gültig; Format = PCM s16le; Rate 48000; Kanäle 1
4. `sample_count > 0`; `duration_seconds > 0`
5. Dauer = `sample_count / sample_rate` (Toleranz ≤ 1 Sample)
6. `byte_size` stimmt; SHA-256 stimmt
7. Datei vollständig lesbar
8. kein absoluter Pfad; kein `_otio` (ohne `_v2`)

Kein Live-Probing im Streamlit-Rerun. FFmpeg/ffprobe für Phase-11-Fake-WAV **nicht** erforderlich (stdlib-WAV-Parse); vorhandene Probe-Adapter nicht für Narration-I/O in der UI verwenden.

---

## 8. LLM-Pausenregie

```text
UI
→ PauseDirection Application Service
→ DiscoveryTextGateway (request_kind=pause_direction)
→ FakeTextAdapter (erweitert) oder klarer Fake-Pause-Pfad hinter demselben Gateway
```

### Gateway-Erweiterung

- neue Prompt-/Schema-Konstanten: `PROMPT_VERSION_PAUSE_DIRECTION`, `RESPONSE_SCHEMA_PAUSE_DIRECTION` (z. B. `pause-direction-v1`)
- `TextGatewayRequest.request_kind` um `pause_direction` erweitern
- striktes Pydantic-Payload-Modell `extra="forbid"`
- Referenzvalidierung: Sentence-/Segment-IDs müssen zum gelockten Script und Voice-Run gehören

### Adapter-Input (nur strukturiert)

Script Lock Metadaten, Sätze (ID/Ordinal/Text/narrative_function), Voice-Segment-Dauern,
Hook-/Narrative-Metadaten. **Keine** Medienbinärdaten.

### LLM darf

- Pause-Position und Funktion wählen
- Visual-only-Intervall wünschen (`visual_breath` o. Ä.)
- Dauerabsichten innerhalb Alpha-Grenzen vorschlagen
- Übergangsfunktion begründen

### LLM darf nicht final entscheiden

exakte Framegrenzen, technische Überlappungen, Source-Ranges, Shotwahl, OTIO-Clips.

Retry analog Editorial: begrenzt (`max_retries` aus TextConfig); danach `pause_retry_exhausted`.

---

## 9. Python-Timingresolver

Deterministisch, rein lokal:

1. Voice-Segmente in Satzreihenfolge laden und technisch re-validieren
2. Segmentdauern aus geprüften Audiodaten übernehmen
3. Pause Directions validieren (Refs, Grenzen, Konflikte)
4. Dauerabsichten → zulässige technische Dauern (E4–E8)
5. Cold Open, Pausen, Visual-only einsetzen
6. fortlaufende Sekunden berechnen
7. auf Projekt-Timebase runden (E10)
8. Framegrenzen monoton berechnen; Überlappungen verhindern
9. Gesamtdauer berechnen; Timeline erneut validieren
10. atomar publizieren + Current State setzen

Keine Lücken außer expliziten Pause-/Visual-only-Einträgen.
`no_pause` → kein Entry.

### Timebase und Framerundung (E10, E11)

| Thema | Entscheidung |
|---|---|
| Quelle | `Project.fps` aus zentraler Projektkonfiguration (Default 25.0) |
| Speicherung | rationale Form `fps_numerator` / `fps_denominator` **plus** kanonischer Float nur für Anzeige; Domain speichert Numerator/Denominator |
| Bekannte Test-Timebases | 24000/1001 (≈23.976), 24/1, 25/1, 30000/1001 (≈29.97), 30/1 |
| Sekundenbasis | hochpräzise `Decimal` oder ganzzahlige Microseconds intern |
| Rundung | Frame-Index = `floor(seconds * fps + 1e-9)` für Starts; Endframe exklusiv oder inklusiv **einheitlich dokumentiert**: **Start inklusiv, End exklusiv**; Dauer-Frames = End − Start ≥ 1 für jeden Entry mit Dauer > 0 |
| Monotonie | `entry[i].start_frame == entry[i-1].end_frame`; keine Überlappung, keine doppelten Frames |

Domainmodell speichert **keine** hartcodierte Framerate-Konstante; nur Timing-Profilversion und Timebase-Felder.

---

## 10. Pause- und Timinggrenzen (Alpha, verbindlich)

| # | Grenze | Wert |
|---|---|---|
| E4 | Cold-Open Maximum | **3.0 s** |
| E5 | Normale Pause (`sentence_transition`, `hook_breath`, `emphasis`, `section_transition`) | min **0.15 s**, max **2.5 s** |
| E6 | Visual-Breath Maximum | **4.0 s** |
| E7 | Closing-Hold Maximum | **5.0 s** |
| E8 | Max. Gesamtpause relativ zur gesprochenen Dauer | **≤ 0.55 × sum(voice_durations)** |
| | Überlange Modellvorschläge | soft → clamp auf Max; hard über Max → `pause_direction_conflict` / Plan verwerfen nach Retries |
| E9 | Mehrere Directions an derselben Position | Priorität: `hard` > `soft`; bei zwei hard gleicher Position → Konflikt, Plan invalid; soft wird von hard verdrängt und als Event/Note protokolliert |
| | Widersprüchliche Funktionen gleicher Position | Konflikt → invalid |

`no_pause` an Position unterdrückt nur einen Pause-Entry; Voice-Entry bleibt.

---

## 11. Persistenz und Schema 17

**Migration:** Registry idempotent **16 → 17**; bestehende Daten erhalten.
Keine Tabellen für Visual Edit / Humanity / Feasibility / Repair / OTIO.

### Gemeinsamer Run-Vertrag

Eigene Narration-Run-Tabellen (nicht Editorial-/Supplementation-Runs wiederverwenden).
Gemeinsames Statusvokabular und Launcher-Muster OK; Semantik getrennt.

### 11.1 `voice_profiles`

| | |
|---|---|
| Zweck | versionierte Voice-Konfiguration pro Projekt |
| Spalten | Felder aus §3.1 + `project_id`, `created_at` |
| Unique | `(project_id, voice_profile_id)`; höchstens ein `active` via Current State |
| Historie | Profile `superseded` bei Änderung; Zeilen unveränderlich außer Status |
| JSON | `narration/voice_profiles/<id>.json` |

### 11.2 `voice_generation_runs`

| | |
|---|---|
| Zweck | Voice-Run-Lifecycle |
| FK | `script_lock_id`, `voice_profile_id`, `project_id` |
| Unique | `run_id` |
| Status | queued/running/completed/failed/interrupted |
| Current | `narration_project_state.current_voice_run_id` nur bei completed |
| JSON | `narration/runs/<run_id>.json` |

### 11.3 `voice_generation_attempts`

| | |
|---|---|
| Zweck | Attempt-/Retry-Historie inkl. Cache-Reuse |
| FK | `run_id` |
| Status | queued/running/completed/failed/reused/interrupted |
| Append-only Statuswechsel | ja |

### 11.4 `voice_segments`

| | |
|---|---|
| Zweck | publizierte Satz-Audios |
| Unique | `segment_id`; zusätzlich Unique Cache-Key-Index (siehe §12) |
| FK | Lock, Sentence, Profile, optional Run |
| Historie | neue Identität bei Text-/Profile-Änderung; alte `superseded` |
| JSON-Anteil | Metadaten; Audio separat `.wav` |

### 11.5 `pause_direction_plans` / `pause_directions`

| | |
|---|---|
| Zweck | LLM-Pausenregie-Snapshot |
| Unique | `pause_plan_id`; Directions Unique `(pause_plan_id, direction_id)` |
| FK | Lock, Voice-Run |
| Historie | Pläne `stale`/`superseded`; Directions unveränderlich |
| JSON | `narration/pause_plans/<id>.json` |

### 11.6 `narration_timelines` / `narration_timeline_entries`

| | |
|---|---|
| Zweck | finale aufgelöste Timeline |
| Unique | `timeline_id`; Entries `(timeline_id, ordinal)` Unique |
| FK | Lock, Voice-Run, Pause-Plan |
| Current | `narration_project_state.current_timeline_id` |
| JSON | `narration/timelines/<id>.json` |

### 11.7 `narration_project_state` (neu) **oder** Erweiterung `editorial_project_state`

**Entscheidung E12:** eigene Tabelle `narration_project_state` mit PK `project_id`, um Editorial-State nicht zu überladen; Felder:

- `current_voice_profile_id`
- `current_voice_run_id`
- `current_pause_plan_id`
- `current_timeline_id`
- `current_script_lock_id` (Kopie/Spiegel des wirksamen Lock-Inputs; muss mit effektivem Lock übereinstimmen)
- `updated_at`

**Keine Current-Auswahl nach `created_at`.**

Optionaler Spiegel `editorial_project_state.current_timeline_id` **nicht** erforderlich in Alpha.

---

## 12. Cache und Idempotenz

### Voice-Segment-Cache-Key

```text
script_lock_id
+ sentence_id
+ text_hash
+ voice_profile_id
+ provider
+ voice_identifier
+ voice_settings_version
+ adapter_version
+ output_profile
```

### Pause-Plan-Cache-Key

```text
script_lock_id
+ voice_run_id
+ segment_set_fingerprint
+ gateway_version + provider + model_identifier
+ prompt_version + response_schema_version
```

### Timing-Cache-Key

```text
script_lock_id
+ voice_run_id
+ pause_plan_id
+ timebase (num/den)
+ timing_profile_version
```

Cache-Reuse **nur nach explizitem Start**. Keine historische Ausgabe überschreiben;
Reuse = neue Run-/Attempt-Zeile mit `reused` + bestehende Segment-IDs/Artefakte.

---

## 13. Stale-Regeln

| Auslöser | Wird stale |
|---|---|
| Script Lock invalidiert oder ersetzt | Voice Run, Segments (als Current), Pause Plan, Timeline |
| Voice Profile geändert (neues active) | neue Voice-Ausgabe nötig; Pause Plan (wenn Dauern ändern); Timeline |
| Voice Segment ersetzt / Hash geändert | Pause Plan, Timeline |
| Pause-Plan-Version geändert | Timeline |
| Timingprofil oder Timebase geändert | Timeline |

Historische Dateien und DB-Zeilen bleiben. Keine stille Überschreibung.
Wirksame Current-IDs werden geleert oder auf gültige Nachfolger gesetzt — nie auf stale IDs zeigen.

---

## 14. Artefaktpfade

Nur unter `_otio_v2/narration/`:

```text
narration/voice_profiles/<voice_profile_id>.json
narration/audio/<voice_run_id>/<segment_id>.wav
narration/pause_plans/<pause_plan_id>.json
narration/timelines/<timeline_id>.json
narration/runs/<run_id>.json
narration/reports/<run_id>.json
narration/temp/<run_id>/
narration/latest_voice_run.json
narration/latest_pause_plan.json
narration/latest_timeline.json
```

Regeln: relative Pfade; kein `_otio`; keine Mutation von Original/Working Media/
Analysis/Editorial-Artefakten; Temp → validieren → atomar publizieren; Konflikte
nicht überschreiben; SQLite interne Wahrheit.

---

## 15. Atomizität

### Voice pro Satz

1. effektiven Lock prüfen
2. Cache prüfen
3. Audio in `narration/temp/<run_id>/` erzeugen
4. vollständig validieren (Hash, Samples, Dauer)
5. atomar nach `narration/audio/<run_id>/` publizieren
6. Segment persistieren

Einzelfehler: Segment `failed`, Run-Zähler aktualisieren; Run nur `completed`,
wenn alle nicht-leeren Sätze `published` oder `reused` und keine harten Fehler offen.

### Pause Plan

Gatewayoutput validieren → Refs prüfen → JSON Temp → reparse → atomar publizieren →
transaktional persistieren.

### Narration Timeline

vollständig im Speicher berechnen → Invarianten → JSON Temp → reparse → atomar
publizieren → SQLite + Current State transaktional.

---

## 16. Runs, Sperren und Recovery

### Scopes

- `voice_generation_only`
- `pause_direction_only`
- `narration_timing_resolve_only`

### Sperren (E13)

Maximal **ein** aktiver Narration-Run pro Projekt.

Ein Narration-Run blockiert:

- andere Narration-Runs
- Script-Lock-Erzeugung / Lock-relevante Mutation
- Editorial- und Supplementation-Starts
- Analysis-Starts (**konservative gegenseitige Sperre** in Alpha)

Analysis blockiert Narration. Kein projektübergreifendes Lock.

### Recovery

- orphan `queued`/`running` → `failed`
- unfertiger Attempt → `interrupted`
- Fehlercode `worker_interrupted`
- nur eigener Temp entfernt
- vollständig publizierte WAVs und gültige Pause-/Timeline-JSONs bleiben
- kein Voice-/Text-Gatewayaufruf während Recovery
- keine Mid-Request-Fortsetzung
- Neustart darf exakte Cache-Artefakte reuse; keine doppelten Segmentdateien

---

## 17. MANUAL-UI

Bevorzugt neue Discovery-Seite **Narration** (Script-Lock-Anzeige kann Summary aus Lock-Service ziehen).

### Script Lock

Anzeigen: Lock-ID, Scriptversion, Satzanzahl, Fingerprint, Wirksamkeit.

### Voice

Anzeigen: Voice-Profil, Provider, Voice-Identifier, Format, Sample Rate,
erwartete Segmentanzahl, bestehende Runs.

Button: **Voice erzeugen**

Hinweis sichtbar: *Lokaler Fake-Voice-Adapter: Es werden keine Texte an externe Dienste übertragen.*

### Pausenregie

Anzeigen: Segmentdauern, narrative Funktionen, vorhandener Pause Plan.

Button: **Pausenregie erzeugen**

### Timing

Anzeigen: Timebase, Pausen, Visual-only, erwartete Gesamtdauer.

Button: **Narration Timing auflösen**

### Review

Anzeigen: Segmente, technische Audiodaten, Pausenfunktionen, Timeline-Einträge
(Sekunden + Frames), Stale-Gründe, Runhistorie.

**Noch keine** Shot-/Assetzuordnung.

### UI-No-I/O

Beim Rendering verboten: Voice-/Text-Gateway, Netzwerk, Jobstart, Audiodatei öffnen,
Medien-stat, Hashing, FFmpeg/ffprobe, direkte SQLite-Abfrage, automatischer Timing Resolve.

Nur Application Services und persistierte Viewmodelle.

---

## 18. Fehlercodes

Mindestens:

`script_lock_missing` · `script_lock_invalidated` · `script_lock_fingerprint_mismatch` ·
`voice_profile_missing` · `voice_profile_invalid` · `voice_gateway_unconfigured` ·
`voice_provider_unavailable` · `voice_generation_failed` · `voice_segment_invalid` ·
`voice_segment_missing` · `voice_segment_hash_mismatch` · `voice_artifact_conflict` ·
`pause_gateway_unconfigured` · `pause_response_invalid` · `pause_response_schema_mismatch` ·
`invalid_pause_reference` · `pause_direction_conflict` · `pause_retry_exhausted` ·
`narration_input_stale` · `timing_resolution_failed` · `invalid_narration_timeline` ·
`invalid_timebase` · `narration_run_already_active` · `analysis_run_already_active` ·
`editorial_run_already_active` · `supplementation_run_already_active` ·
`narration_registry_write_failed` · `narration_artifact_write_failed` ·
`worker_interrupted` · `report_write_failed`

Keine Secrets, Vollpayloads oder absoluten Medienpfade in Fehlertexten.

---

## 19. Testplan (Gruppen)

1. Schema 16 → 17 + Datenhalt; idempotent; nur Phase-11-Tabellen
2. Voice-Profile-Versionierung
3. effektiver Script Lock als Pflichtinput
4. Fake Voice Gateway; kein Netzwerk; deterministische WAV
5. WAV-Technikprüfung
6. Satz-zu-Segment-Bindung; leerer Text; Textänderung → neue Identität
7. Segment-Cache + Idempotenz
8. partieller Voice-Fehler → Run nicht completed
9. Pause-Domainvertrag; zentraler Text-Gateway; Refs; Retry
10. Timingresolver; Timebase-Rundung (23.976/24/25/29.97/30)
11. monotone Frames; keine Überlappung; Visual-only; Gesamtdauer
12. Stale-Matrix
13. Atomizität und Artifact-Konflikt
14. Recovery (kein Gateway)
15. UI-No-I/O
16. keine Phase-12-Funktion
17. Classic-/Without-VO-Regression; `_otio_v2`-Isolation
18. kein ElevenLabs / echter Provider
19. lokale E2E-Smokes A–H
20. **Phase-10-Nachholung:** `accepted_for_import` ohne Intake/Analyse/Review/neuen Coverage Audit kann **nie** Bestandteil eines wirksamen Script Locks sein

---

## 20. Fake-End-to-End-Smokes

| ID | Inhalt |
|---|---|
| **A** | gültiger Lock → Fake Voice → Segments → Fake Pause → Timing Resolve → validierte Timeline |
| **B** | zweiter expliziter Voice-Run; unveränderte Sätze; gleiche Segment-IDs/Artefakte; kein Adapteraufruf für Cache-Hits |
| **C** | geänderte Voice-Konfiguration → neue Ausgabe; alte Segmente bleiben; Pause+Timeline stale |
| **D** | ungültiger/invalidierter Lock → Voice blockiert; kein Audioartefakt |
| **E** | Pause mit ungültiger Ref → verworfen; begrenzte Retries; keine Timeline |
| **F** | dieselbe Narration über Timebases 23.976/24/25/29.97/30; monotone Frames; erwartbare Rundungsunterschiede |
| **G** | Crash nach einer publizierten Segmentdatei → Run nicht completed; Datei bleibt; Neustart validates+reuses; kein Duplikat |
| **H** | UI zweimal rendern → kein Voice-/Text-Gateway, kein Audiozugriff, kein Jobstart |

Pro Smoke berichten: Lock-ID, Voice-Run-ID, Profile, Sentence-/Segment-IDs, Format/Rate/Kanäle/Samples/Dauern/Hashes, Pause-Plan-ID, Funktionen/Dauerabsichten, Timeline-ID, Timebase, Entries, Gesamtsekunden/Frames, Adapteraufrufe, Status, Fehlercode, PASS/FAIL.

---

## 21. Modulvorschlag

```text
otio_app/discovery_v2/
  domain/narration.py
  adapters/voice_config.py
  adapters/voice_fake.py
  adapters/voice_gateway.py
  adapters/narration_job_launcher.py
  application/voice_generation_service.py
  application/pause_direction_service.py
  application/narration_timing_service.py
  application/narration_job_recovery.py
  persistence/narration_repository.py
  narration_paths.py
  jobs/narration_worker.py
  ui/narration_page.py          # neu; Routing analog Editorial
```

Text-Gateway-/Fake-Adapter-Dateien um `pause_direction` erweitern (kein zweites paralleles Text-Gateway).

---

## 22. Implementierungsaufteilung (Makroauftrag)

```text
Schema 17 + Narration Domain/State
+ VoiceGenerationGateway + FakeVoiceAdapter
+ Voice Generation + Segment-Cache + Audio-Validierung
+ pause_direction über DiscoveryTextGateway + Fake
+ Python Timing Resolver + Timebase-Rundung
+ Runs/Launcher/Recovery + gegenseitige Sperren
+ Narration UI (MANUAL) + No-I/O
+ Tests + Smokes A–H
+ Phase-10-Regression (accepted Candidate ohne Intake ≠ Lock)
```

### Separates Gate (nie still aktivieren)

Echter ElevenLabs- oder anderer Voice-Provider.

---

## 23. Entscheidungen (verbindlich für Implementierungsauftrag)

| # | Thema | Entscheidung |
|---|---|---|
| E1 | Fake-Voice-Dauerfunktion | `clamp(len(text)*0.055, 0.40, 18.0)` s; Samples = round(duration×48000) |
| E2 | Satzdauer min/max | **0.40 s** / **18.0 s**; max Textlänge **2000**; max Segmente/Run **500** |
| E3 | Audioformat | WAV PCM s16le, **48000 Hz**, **mono** |
| E4 | Cold-Open Max | **3.0 s** |
| E5 | Normale Pause min/max | **0.15 s** / **2.5 s** |
| E6 | Visual-Breath Max | **4.0 s** |
| E7 | Closing-Hold Max | **5.0 s** |
| E8 | Max. Pausenverhältnis | **0.55 ×** gesprochene Gesamtdauer |
| E9 | Mehrfach-Directions | hard > soft; zwei hard gleich Position → Konflikt |
| E10 | Standard-Timebase | `Project.fps` (Default **25**); Speicherung als `fps_numerator`/`fps_denominator` |
| E11 | Framerundung | Start inklusiv, End exklusiv; `floor(seconds*fps+1e-9)`; monotone Aneinanderreihung |
| E12 | Current State | eigene Tabelle `narration_project_state` mit expliziten Current-IDs |
| E13 | Analysis-Sperre | konservativ gegenseitig: Narration ↔ Analysis/Editorial/Supplementation |
| E14 | Segment-Mapping | genau 1 Segment / Satz; keine Verschmelzung |
| E15 | Voice-Run completed | nur wenn alle nicht-leeren Sätze published/reused |
| E16 | Pause Gateway | `pause_direction` am bestehenden `DiscoveryTextGateway` |
| E17 | Timing Resolve | synchroner Application-Pfad erlaubt; schwerer Worker optional für Voice; Timeline Resolve darf sync sein analog Script Lock |
| E18 | Artefaktwurzel | ausschließlich `_otio_v2/narration/` |
| E19 | Provider Alpha | nur `provider=fake` enabled; andere nicht instanziieren |
| E20 | Narration-Audio ≠ Working Media | Voice-WAVs sind Narration-Artefakte; nie OTIO-/Working-Media-Quelle in Phase 11 |

Keine Grenzwerte im Implementierungsauftrag improvisieren — bei Lücke stoppen und Plan nachziehen.

---

## 24. UNKNOWN-Punkte

- echte ElevenLabs Voice-IDs, Modelle, Formate, Limits, Kosten, Auth
- produktive Sprachqualität / Prosodie
- Mehrsprecher / Mehrsprachen-Mixing jenseits eines Profils
- Streaming-TTS
- Cloud-Preview-CDN
- Mehrbenutzer-Queue
- automatische Waveform-UI

---

## 25. Nachzuholende Phase-10-Regression

Im Implementierungsauftrag dauerhaft testen:

> Ein `accepted_for_import` Candidate ohne erfolgreiches Intake, aktuelle Analyse,
> Observation Review und neuen Coverage Audit kann niemals Bestandteil eines
> wirksamen Script Locks sein.

---

## 26. DoD für späteren Implementierungsauftrag

- Schema 17 + Fake-Smokes A–H grün
- Nur wirksamer Script Lock startet Narration
- Fake Voice ohne Netzwerk; WAV-Vertrag eingehalten
- Pause nur über DiscoveryTextGateway; keine Frames vom LLM
- Timing deterministisch; Timebase-Tests grün
- Stale-/Invalidierung zuverlässig
- UI-No-I/O; kein Auto-Job
- kein ElevenLabs / echter Provider
- keine Phase-12-Funktion
- Classic/Without-VO unverändert; kein `_otio`
- Phase-10-Regression (Candidate≠Lock) grün
- Vollsuite: keine neuen Discovery-bedingten Failures; Baseline-18 unangetastet

---

## 27. Nächste erlaubte Aktion

Nach **Freigabe dieses Plans**:

→ Phase-11-**Implementierungs**-Makroauftrag gemäß §22.

Gesperrt: echter Voiceprovider/ElevenLabs, Phase 12+, Visual Edit Plan, OTIO.
