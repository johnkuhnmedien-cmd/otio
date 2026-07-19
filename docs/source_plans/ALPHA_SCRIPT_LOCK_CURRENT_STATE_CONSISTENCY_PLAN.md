# ALPHA — Script Lock Current-State Consistency

**Plan-ID:** `DISCOVERY-V2-ALPHA-SCRIPT-LOCK-CURRENT-STATE-CONSISTENCY-PLAN-001`  
**Status:** Planung (keine Implementierung)  
**Schema-Ziel:** `REGISTRY_SCHEMA_VERSION = 20` (keine Migration)  
**Branch / PR:** `cursor/discovery-v2-integration` · `#69`  
**Bezug:** Fake-Alpha an Narration-Grenze blockiert (USA_v2 Realzustand)

---

## 1. Ausgangslage

### 1.1 Bestätigter Realzustand — USA_v2

| Feld | Wert |
|---|---|
| Projekt | `USA_v2` |
| `project_id` | `4e364f0c-9a6d-462c-b336-df9314f585ca` |
| `active_script_id` | `0fa95aec-d26a-4bfe-9b1b-c996d480ef5f` (Version **2**) |
| `active_narrative_plan_id` | `55400427-1248-5447-9c80-3f6e8363535a` |
| `selected_hook_id` | `2de191f0-35a4-56ee-83bc-ca8b11e014bd` |
| `active_coverage_audit_id` | `c2b32d64-3961-53bf-ab85-391932a2bf43` |
| `editorial_project_state.current_script_lock_id` | **NULL** |
| Einziger Lock | `07c69bb1-0d0b-4e16-b21b-c198f3a42d68` |
| Lock `script_id` / Version | `c4f76c9c-…` / **1** (≠ aktives Script 2) |
| Lock Narrative / Hook / Audit | `cedb79f9-…` / `1eef33cc-…` / `8d1b8336-…` (≠ Current) |
| Lock `status` | `locked` |
| `narration_project_state.current_script_lock_id` | `07c69bb1-…` (stale Pointer) |

### 1.2 UI-Symptome

| Oberfläche | Symptom |
|---|---|
| Editorial | Historischer Lock als „Aktueller Lock“; New-Lock-Button deaktiviert; widersprüchlicher Fingerprint-Hinweis |
| Narration | „Kein wirksamer Script Lock vorhanden“; Voice-Button deaktiviert |

### 1.3 Produktstand (nicht erneut lösen)

- Coverage Stability C1–C3.3 umgesetzt (Schema 20)
- C3.4 / C4 / V4 / R1.4 gesperrt
- Fake-only; keine Registry-Reparatur im Nutzerprojekt in diesem Plan

---

## 2. Root Cause (belegt)

### 2.1 Editorial zeigt historischen Lock als „Current“

**Datei:** `otio_app/discovery_v2/ui/editorial_page.py` → `_render_script_lock`

```
supp_view.script_locks[0]  # list_script_locks: ORDER BY lock_version DESC, alle Status
→ Caption „Aktueller Lock: …“
```

- Liest **nicht** `editorial_project_state.current_script_lock_id`
- Ruft **nicht** `get_effective_script_lock` auf
- `list_script_locks` filtert nicht auf `status='locked'` und nicht auf Editorial-Pointer

Folge bei USA_v2: Editorial-Pointer `NULL`, aber Lock-Zeile `07c69bb1-…` mit `status=locked` bleibt erste Liste → Caption behauptet „Aktueller Lock“.

### 2.2 Widersprüchlicher Fingerprint-Hinweis

Dieselbe UI mischt zwei Quellen:

| Block | Quelle |
|---|---|
| Caption „Aktueller Lock“ + alter Fingerprint-Prefix | historische `script_locks[0]`-Zeile |
| „Aktueller Lock-Stand“ / „Kein Fingerprint verfügbar“ | `preview_script_lock` (aktueller Editorial-Stand) |

Wenn Preview blockiert ist (`lock_fingerprint` leer), erscheint unten „Kein Fingerprint verfügbar“, während oben der historische Lock-Fingerprint gezeigt wird.

### 2.3 New-Lock-Button und historischer Lock

`can_click = displayed_fingerprint and confirmed and risks_ok` hängt nur an **Preview-Readiness**, nicht daran, ob ein historischer Lock existiert. Der Button ist bei USA_v2 deaktiviert, weil die Preview-Voraussetzungen (Gaps/Claims/Risiken/Fingerprint) für den **aktuellen** Stand nicht erfüllt sind — nicht weil ein historischer Lock den Button sperrt. Trotzdem vermittelt die Caption fälschlich, es gäbe bereits einen aktuellen Lock.

### 2.4 Narration blockiert korrekt (Effective), Pointer bleibt stale

**Datei:** `otio_app/discovery_v2/application/script_lock_service.py` → `get_effective_script_lock`

1. Preferiert `editorial_project_state.current_script_lock_id`
2. Fallback: `get_current_script_lock` = neueste Zeile mit `status='locked'`
3. Fingerprint-Revalidation gegen aktuellen Stand; bei Mismatch → Lock `invalidated`, Editorial-Pointer `NULL`
4. **Narration-Pointer wird nicht geleert**

Narration-UI (`narration_page.py`) und Voice-Gate nutzen `get_effective_script_lock` / `require_effective_lock_for_narration` → korrekt „kein wirksamer Lock“.

`narration_project_state.current_script_lock_id` wird nur in `mark_current_voice_run` / `mark_current_pause_plan` / `mark_current_timeline` gesetzt und bei Invalidierung **nicht** zurückgesetzt → stale Pointer bleibt stehen.

### 2.5 Zusätzlicher Fallback-Risiko

`get_effective_script_lock` fällt bei fehlendem Editorial-Pointer auf „latest locked row“ zurück. Das widerspricht dem Canonical-Vertrag dieses Plans (kein Fallback `latest locked → current`). Bei USA_v2 würde selbst dieser Fallback den historischen Lock laden und anschließend per Fingerprint invalidieren — aber die Editorial-UI nutzt ihn ohnehin nicht.

### 2.6 Editorial-State-Wipe ohne Lock-Invalidierung

`editorial_worker.py` kann `EditorialProjectState(...)` ohne `current_script_lock_id` schreiben → Pointer wird still `NULL`, während die Lock-Zeile `locked` bleiben kann, bis ein Gate `get_effective_script_lock` aufruft.

---

## 3. Abgrenzung

| Thema | Eigentümer |
|---|---|
| Wirksamer Current Script Lock | **dieser Plan (L1–L5)** |
| Coverage Gap Identity / Carry-Forward | C3 (C3.4 gesperrt) |
| UI Gap-Beschriftung (Intent vs Gap-ID) | C4 / separate UI-Korrektur |
| Visual Edit V4, R1.4 | gesperrt |
| Registry-Reparatur USA_v2 per Hand | ausgeschlossen (nur Realtest L5 nach Fix) |

Dieser Plan **ersetzt nicht** C2/C3 und darf keine Schema-21-Migration einführen.

---

## 4. Canonical Effective-Lock-Vertrag

### 4.1 Zentraler Resolver

```
resolve_effective_current_script_lock(project) -> EffectiveScriptLockResult
```

Single Source of Truth für Editorial-Anzeige, Narration-Gates, Visual-Edit-Gates und Pause/Timing.

### 4.2 Wirksamkeit — alle Bedingungen Pflicht

Ein Lock ist nur wirksam, wenn **alle** gelten:

| # | Bedingung |
|---|---|
| 1 | `editorial_project_state.current_script_lock_id` verweist auf genau diesen Lock |
| 2 | Lock `project_id` = aktuelles Projekt |
| 3 | Lock `script_id` + `script_version` = aktives Script |
| 4 | Lock `narrative_plan_id` = aktiver Narrative Plan |
| 5 | Lock `selected_hook_id` = ausgewählter Hook |
| 6 | Lock `coverage_audit_id` = aktueller Coverage Audit |
| 7 | Lock `observation_set_fingerprint` = aktueller Observation-Stand |
| 8 | Lock `lock_fingerprint` / Confirmation-Fingerprint = aktuell berechneter Preview-Fingerprint |
| 9 | Risk Confirmations gehören zum aktuellen Gap-/Risk-Stand (`gap_id:risk_code`) |
| 10 | Lock `status` = `locked` |

### 4.3 Explizit verbotener Fallback

```
latest locked row  →  aktueller Lock     # VERBOTEN
```

- Historischer Lock mit `status=locked` bleibt historisch, wenn der Editorial-Pointer fehlt oder auf einen anderen Lock zeigt.
- Bei `current_script_lock_id = NULL` → **kein** wirksamer Lock (fail-closed).

### 4.4 Beziehung zu bestehendem `get_effective_script_lock`

L2 ersetzt bzw. verschärft `get_effective_script_lock`:

- Editorial-Pointer Pflicht (kein latest-locked Fallback)
- Explizite Identity-Checks (Script/Narrative/Hook/Audit/Observation) zusätzlich zur Fingerprint-Gleichheit
- Einheitliche Fehlercodes (Abschnitt 9)
- Bei Unwirksamkeit: Pointer-Konsistenz gemäß Abschnitt 7

---

## 5. Fingerprint- und Risk-Confirmation-Vertrag

### 5.1 Fingerprint (bestehend, bleibt kanonisch)

`script_lock_fingerprint(...)` in `domain/supplementation.py` über:

- project / script / version / brief / narrative / hook / coverage_audit
- observation_set_fingerprint
- script_hash, structure_fingerprint, coverage_fingerprint
- accepted_open_risks (`gap_id:risk_code`)
- claim_decision_snapshot

Preview baut denselben Fingerprint für den **aktuellen** Stand (`preview_script_lock`).

### 5.2 Risk Confirmations

- Keys nur aus `persisted_accepted_lock_risk_keys` / Preview `accepted_open_risks`
- Bestätigung gilt nur für den angezeigten aktuellen Fingerprint
- Historische Confirmations eines alten Locks gelten nicht für einen neuen Stand

### 5.3 Anzeige

- Fingerprint nur aus Preview (aktueller Stand) oder aus wirksamem Effective Lock
- Historischer Lock-Fingerprint nie als „aktueller Stand“ beschriften

---

## 6. Editorial-UI-Vertrag

### 6.1 Kein wirksamer Current Lock

Anzeige:

```
Kein wirksamer Script Lock vorhanden.
```

Historische Locks optional unter „Historische Locks“ (ID, Status, Script-Version, Fingerprint-Prefix) — **niemals** als „Aktueller Lock“.

### 6.2 New-Lock-Button

Aktiv nur wenn:

- Preview hat Fingerprint (keine fachlichen Blocker)
- Bestätigungscheckboxen gesetzt (Stand + Risiken)
- **kein** wirksamer Current Lock für denselben Fingerprint (Duplikat-Schutz bleibt in `create_script_lock`)

Ein historischer Lock **darf den Button nicht deaktivieren**.

### 6.3 Widerspruchsfreie Anzeige

Verboten:

- oben historischer Lock als Current + unten „kein Fingerprint verfügbar“
- Caption mit historischem Fingerprint neben Preview-Fingerprint des neuen Stands ohne Kennzeichnung

Erlaubt:

- Current-Sektion leer / „kein wirksamer Lock“
- Preview-Sektion „Aktueller Lock-Stand“ nur bei verfügbarem Preview-Fingerprint
- Historie separat

---

## 7. Narration- und Artefakt-Vertrag

### 7.1 Gates

Voice, Pause und Timing müssen **denselben** `resolve_effective_current_script_lock` nutzen.

Narration darf **nicht** allein `narration_project_state.current_script_lock_id` vertrauen.

### 7.2 Stale Narration-Pointer (Schema-20-Entscheidung)

**Empfehlung (verbindlich für L2/L4):**

```
stale narration current_script_lock_id
+ kein wirksamer Editorial Effective Lock
→ Pointer bei Current-Auflösung ignorieren
→ bei Invalidierung / erfolgreicher Effective-Prüfung atomar auf NULL setzen
→ historische Voice/Pause/Timeline-Zeilen behalten ihre script_lock_id
```

Begründung:

- Schema 20 ohne Migration
- Current/Historical klar getrennt
- Kein stilles Weiterverwenden alter Artefakte
- Pointer-Clear ist atomar mit Lock-Invalidierung (eine Transaktion)

Alternativen (abgelehnt für Alpha):

- Nur ignorieren ohne Clear → stale DB-Zustand bleibt sichtbar/verwirrend
- Versionierter Sidecar Current-State → unnötige Komplexität für Schema 20

### 7.3 Nach erfolgreichem neuem Lock

| Feld | Aktion |
|---|---|
| `editorial_project_state.current_script_lock_id` | → neuer Lock |
| `narration_project_state.current_script_lock_id` | → `NULL` bis erster erfolgreicher Voice-Start unter dem neuen Lock **oder** sofort neuer Lock (L4 entscheidet konsistent; Default: `NULL` bis Voice, Gates lesen Editorial Effective Lock) |
| Voice Run / Segments / Pause Plan / Timeline des alten Locks | bleiben historisch; nicht Current |
| Current-Markierungen (`mark_current_*`) | nur für Artefakte mit `script_lock_id == effective.lock_id` |

Alte Artefakte dürfen nicht still für den neuen Lock weitergelten.

### 7.4 Pause/Timing-Alignment

Heute: `can_start_pause` / `can_resolve_timing` prüfen teils nur `current_voice_run_id` / `current_pause_plan_id`, nicht `effective.ok`.  
L3: Voice **und** Pause/Timing erfordern wirksamen Effective Lock (+ passende Current-Artefakt-Kette).

---

## 8. Schemaentscheidung

| Entscheidung | Wert |
|---|---|
| Schema | **20** unverändert |
| Neue Spalten / Migration | nein |
| Sidecar-Publikation | nein (Current bleibt in Project-State-Pointern) |
| Historische Lock-Zeilen | bleiben append-only (`locked` / `superseded` / `invalidated`) |

---

## 9. Fehlerfälle

Vorhandene Codes bevorzugen; neue nur wenn nötig.

| Code | Wann |
|---|---|
| `script_lock_current_pointer_missing` | Editorial `current_script_lock_id` ist NULL |
| `script_lock_current_pointer_stale` | Pointer zeigt auf Lock, der Identity/Fingerprint nicht mehr erfüllt |
| `script_lock_fingerprint_mismatch` | bestehend — Preview ≠ Lock-Fingerprint |
| `script_lock_editorial_state_mismatch` | Script/Narrative/Hook/Audit/Observation weichen ab |
| `script_lock_risk_confirmation_mismatch` | Risk Keys passen nicht zum aktuellen Gap-Stand |
| `narration_script_lock_stale` | Narration-Pointer ≠ Effective Lock / Pointer ohne Effective |
| `narration_artifact_lock_mismatch` | Voice/Pause/Timeline `script_lock_id` ≠ Effective Lock |
| `script_lock_missing` / `script_lock_invalidated` | bestehende Narration/Visual-Edit-Gates |

**Fail-closed:** kein eindeutig wirksamer aktueller Lock → keine Voice-Erzeugung (und keine Pause/Timing-Starts).

---

## 10. Testmatrix (spätere Umsetzung)

| # | Nachweis |
|---|---|
| T1 | Historischer `locked`-Datensatz ist nicht automatisch Current |
| T2 | `current_script_lock_id=NULL` → kein wirksamer Lock |
| T3 | Lock Script Version 1 unwirksam für aktives Script Version 2 |
| T4 | Lock mit altem Coverage Audit unwirksam |
| T5 | Editorial zeigt historischen Lock nicht als „Aktueller Lock“ |
| T6 | Historischer Lock deaktiviert New-Lock-Button nicht |
| T7 | Aktueller Fingerprint und Hilfetext widersprechen sich nicht |
| T8 | Narration ignoriert stale Narration-Lock-Pointer |
| T9 | Voice bleibt vor neuem Lock deaktiviert |
| T10 | Neuer Lock für aktuellen Stand wird erfolgreich erstellt |
| T11 | Nach neuem Lock erkennt Narration denselben Effective Lock |
| T12 | Voice wird danach freigeschaltet |
| T13 | Alte Voice-/Pause-/Timeline-Artefakte bleiben historisch |
| T14 | Alte Artefakte werden nicht für den neuen Lock wiederverwendet |
| T15 | Identischer gültiger Lock erzeugt keinen Duplikat-Lock |
| T16 | Schema bleibt 20 |
| T17 | Kein `_otio`-Write |
| T18 | Classic und Without-VO unverändert |
| T19 | Keine echten Provider |

---

## 11. Implementierungsreihenfolge

Jeder Schritt separat umsetzen und freigeben. Kein Vorziehen.

### L1 — Root-Cause-Fixtures

- Deterministische Fixtures für USA_v2-ähnlichen Split-Brain:
  - Editorial Pointer NULL
  - historischer `locked` Lock für Script v1
  - aktives Script v2 + neuer Audit
  - Narration Pointer auf historischen Lock
- Grüne Regressionstests, die **heutiges** Fehlverhalten bzw. Gate-Korrektheit dokumentieren (analog C3.1)
- Keine Produktänderung außer Test-only, sofern L1 rein reproduzierend bleibt

### L2 — Zentraler Effective-Lock-Resolver

- `resolve_effective_current_script_lock` (oder verschärftes `get_effective_script_lock`)
- Kein latest-locked Fallback
- Identity- + Fingerprint- + Risk-Checks
- Einheitliche Fehlercodes
- Schema 20

### L3 — Editorial- und Narration-Gate-Integration

- Editorial-UI: Effective Lock für Current-Anzeige; Historie getrennt; widerspruchsfreie Fingerprint-Texte; Button-Logik
- Narration/Voice/Pause/Timing: derselbe Resolver
- Visual-Edit-Gates angleichen, sofern sie Effective Lock nutzen

### L4 — Current-State- und Artefaktinvalidierung

- Atomare Clear von Editorial- **und** Narration-`current_script_lock_id` bei Unwirksamkeit
- Nach neuem Lock: Editorial Pointer setzen; Narration Pointer laut Abschnitt 7.3
- Current-Artefakte nur für Effective Lock; alte bleiben historisch
- Editorial-State-Upserts dürfen Lock-Pointer nicht still verwerfen ohne Invalidierung

### L5 — USA_v2-Realtest

- Fake-Alpha: Current Stand vorbereiten → neuer Lock → Narration erkennt Effective Lock → Voice freigeschaltet
- Keine stille Übernahme alter Voice/Pause/Timeline
- Keine Registry-Handreparatur als Produktlösung

---

## 12. Risiken und Einschränkungen

| Risiko | Mitigation |
|---|---|
| Entfernen des latest-locked Fallbacks bricht latente Pfade | L1/L2-Tests + gezielte Narration/Script-Lock-Suite |
| Editorial-Worker wischt Pointer | L4: Preserve-or-invalidate Vertrag |
| Stale Narration-Artefakte wirken „fertig“ | Current nur bei matching `script_lock_id` |
| USA_v2 manuell reparieren statt Produktfix | Verboten; L5 erst nach L2–L4 |
| Scope-Creep in C3.4/C4 | Explizit gesperrt |

---

## 13. Explizit nicht in diesem Plan

- Produktimplementierung (L1–L5 erst nach Freigabe)
- Registry-Reparatur / neuer Lock im Nutzerprojekt jetzt
- Voice-Erzeugung jetzt
- Schema-21 / Sidecar
- C3.4 Carry-Forward
- C4 Atomicity / UI Gap-Label
- Visual Edit V4
- R1.4 Polling
- echte Provider
- Reparatur der 18 Baseline-Fehler

---

## 14. Abnahmekriterien für den Plan

Der Plan ist abnahmefähig, wenn:

1. Root Cause mit Datei/Funktion belegt ist
2. Canonical Effective-Lock-Vertrag ohne latest-locked Fallback definiert ist
3. Editorial- und Narration-Verträge widerspruchsfrei sind
4. Stale-Pointer- und Artefaktregeln für Schema 20 festliegen
5. Fehlerfälle und Testmatrix vollständig sind
6. Reihenfolge L1–L5 klar und separat freigabefähig ist
7. Keine Produktdatei geändert wurde

---

## 15. Nächster erlaubter Schritt nach Planfreigabe

→ **L1 Root-Cause-Fixtures**

Danach gesperrt bis jeweilige Freigabe: L2 → L3 → L4 → L5.  
Weiter gesperrt: C3.4, C4, V4, R1.4, echte Provider.
