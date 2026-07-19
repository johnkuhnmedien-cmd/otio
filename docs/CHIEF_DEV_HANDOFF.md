> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Chief Dev Handoff — Discovery V2

Operative Details: `DISCOVERY_V2_HANDOFF.md`.

## Source-of-Truth-Reihenfolge

1. `.cursor/rules/00-core-architecture.mdc`
2. `.cursor/rules/01-step-discipline.mdc`
3. `docs/DECISIONS.md`
4. `docs/MASTER_PLAN.md`
5. `docs/ALPHA_SCOPE.md`
6. `docs/PIPELINE_SPEC.md`
7. `docs/MEDIA_LIFECYCLE.md`
8. `docs/EDITORIAL_QUALITY.md`
9. `docs/MODEL_ROUTING.md`
10. `docs/CLASSIC_MIGRATION_CONTRACT.md`
11. `docs/PROGRESS.md`
12. `docs/CHIEF_DEV_HANDOFF.md`
13. `docs/source_plans/*` (nachrangig; überschreibt nichts Höheres)

Untergeordnet: `docs/ALPHA_EXECUTION_MANIFEST.md`.

## Repository-Lage

- Repository / Worktree: Discovery-V2-Integration (`cursor/discovery-v2-integration`)
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Aktueller HEAD: Projekt-öffnen-Routing-Hotfix (`a541ab1` + Docs)
- V1–V3 Visual Edit: `70ffe6e` / `0fba7bd` / `f1b982a`
- Alpha-Produktstand-HEAD: `1ac7fba2bf2c1a7f0ae783a81c82495e2c7c600e`
- R1.1–R1.3 abgeschlossen · Script-Lock Realtest **erfolgreich**
- Chief-Dev Alpha-Produktstand: **APPROVED**
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Registry-Schema: **20**
- Teststand: **3271 collected / 3252 passed / 18 failed / 1 skipped**
- Provider: Fake-only · Adobe: UNKNOWN · NLE: lokaler OTIO-Serialize/Reparse
- Decisions: D-R1.1-001…004 · D-R1.2-001…003 · D-R1.3-001…004 ·
  D-VE-REWORK-001…006 · D-COVERAGE-STABILITY-001…008 ·
  **D-SCRIPT-LOCK-CURRENT-001…006** · **D-STRUCTURE-RECOVERY-001**
- Visual Edit V1–V3: **abgeschlossen**
- **Coverage Stability C2 vollständig akzeptiert** (C1+C2+C2-R1 + USA_v2)
- **C3.1 / C3.2 abgeschlossen** (`36367d2` / `47cfafd`)
- **C3.3 Exact Match Engine umgesetzt** (`7ba6468`)
- **Script-Lock Current-State Plan dokumentiert** (`6a33d0f`)
- **Script-Lock L1 Root-Cause-Fixtures abgeschlossen** (`a492c54`)
- **Script-Lock L2 Effective Resolver umgesetzt** (`5bca917`)
- **Script-Lock L3 Editorial-/Narration-Gates umgesetzt** (`6852e7c`)
- **Script-Lock L4 Current-State-Invalidierung umgesetzt** (`f1ddcb2`)
  - Plan: `docs/source_plans/ALPHA_SCRIPT_LOCK_CURRENT_STATE_CONSISTENCY_PLAN.md`
  - atomare Editorial-/Narration-Pointer-Clears; Historie erhalten
  - neuer Lock = Editorial Current only; Narration bindet bei Voice-Start
  - Decisions: D-SCRIPT-LOCK-CURRENT-005 / D-SCRIPT-LOCK-CURRENT-006
- **Structure-Finalization-Hotfix umgesetzt** (`aaf7696`)
  - vollständige Struktur → kanonisch `review_requested` (nicht mehr dauerhaft `structure_pending`)
  - unvollständig → fail-closed + sichtbarer Fehlercode; UI sync „Struktur aktualisiert.“ / Fehler
  - Tests: `tests/test_discovery_v2_structure_finalization.py`
- **Structure-Persistence Crash-Recovery-Rework umgesetzt** (`3af76c8`)
  - Temp-Stage → SQLite-Commit → atomic versioned + `latest_script` Publish → Run completed
  - FS-Fehler nach Commit fail-closed (`editorial_artifact_write_failed` / Preview `registry_artifact_mismatch`); Retry aus Registry
  - Intent-Upsert; keine stille Löschung referenzierter Coverage Results
  - Decision: D-STRUCTURE-RECOVERY-001
  - Tests: `tests/test_discovery_v2_structure_persistence_atomicity.py`
- **Active-Script-Pointer-Recovery-Hotfix umgesetzt** (`1dac126`)
  - `diagnose_active_script_recovery` / `recover_active_script_current_state`
  - nur genau ein verifizierter Kandidat; bewusster Button; keine Auto-Auswahl
  - setzt atomar Script-/Narrative-/Hook-Pointer; kein Content-/Coverage-/Lock-Rewrite
  - Tests: `tests/test_discovery_v2_active_script_recovery.py`
- **Projekt-öffnen-Routing-Hotfix umgesetzt** (`a541ab1`)
  - `activate_project_for_editing` + pending `st.switch_page` nach Mode-Nav-Aufbau
  - Discovery → `discovery-v2` Overview; Classic → `analysen`
  - behebt `StreamlitAPIException: Could not find page: analysen` unter Discovery-Shell
  - Tests: `tests/test_project_list_open_routing.py`
- Offener UI-Befund: Visual-Intent-ID teilweise als Gap-ID beschriftet (nicht C3)
- **Fake-Alpha weiterhin bis L5 pausiert**
- **Nächste erlaubte Aktion nach Chief-Dev-Freigabe: L5 USA_v2-Realtest**
- **L5, C3.4, C4, V4 und R1.4 gesperrt** · echte Provider gesperrt
- Keine neue Produktphase · keine Nutzerregistry-Reparatur

## Nächste erlaubte Aktion nach Freigabe

**L5 USA_v2-Realtest** nach Chief-Dev-Freigabe.

Gesperrt: L5 bis Freigabe; C3.4, C4, UI Gap-Label, V4, R1.4, echte Provider.
Fake-Alpha weiterhin bis L5 pausiert.

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard
- Approval nur durch Menschen; Checkbox nie vorselektiert
- Export Validation vor jedem OTIO
- OTIO nur Working Media + aktuelle Narration-WAVs
- Completed erst nach Reparse + Semantik
- Classic `_otio/` read-only; Discovery unter `_otio_v2/`
- KI-Timelines = `NEGATIVE_REFERENCE`
- Gateways zentral; keine stillen Provider
- Keine Registry-Manipulation durch die UI
- Discovery Route: URL + Application Service; session_state nicht alleinige Wahrheit
- Rendering/Reload/Rerun starten keine Jobs/Gateways/Medien-I/O automatisch

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO mit Reparse, keine neuen Discovery-bedingten Testfehler.
