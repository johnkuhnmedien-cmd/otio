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
- Aktueller Planungs-Ausgang-HEAD: `1ac7fba2bf2c1a7f0ae783a81c82495e2c7c600e`
- Chief-Dev Alpha-Produktstand: **APPROVED**
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Registry-Schema: **20**
- Teststand: **2915 collected / 2896 passed / 18 failed / 1 skipped**
- Provider: Fake-only · Adobe: UNKNOWN · NLE: lokaler OTIO-Serialize/Reparse
- Interne Alpha-Erprobung: **End-to-End blockiert** am Coverage-/Script-Lock-Gate
- Reproduzierter Blocker:
  - Coverage `partially_covered`
  - Gap `in_progress` @ Escalation `user_decision`
  - `missing_properties=["exact_match_not_verified"]`
  - `risk_flags=[]`
  - Candidates alle `rejected`
  - UI „Risiko unaufgelöst akzeptieren“ disabled
  - Script-Lock-Fingerprint leer
- Zusätzlich beobachtet: `editorial_registry_write_failed`, stale Viewmodels,
  Reload verliert Projekt/Seite, fehlendes Job-Polling, Analyse-Frame-Limit,
  Einzel-Observation-Review, Claim-Status unsichtbar, Intake ohne Speicher-Preflight
- **Aktueller Schritt: R1-Planung** —
  `docs/source_plans/ALPHA_UX_WORKFLOW_STABILIZATION_R1_PLAN.md`
- Keine neue Produktphase

## Nächste erlaubte Aktion nach Freigabe

→ **R1.1 Blocker-Implementierung**

Umfasst nur:

1. Coverage Gap `accepted_unresolved` für Partial-Coverage-Pfad
2. `editorial_registry_write_failed` RCA + atomare Writes
3. Script-Lock-Gate verständlich
4. Fingerprint-UX (Checkbox, kein Freitext; Sicherheit erhalten)

**Noch gesperrt:** R1.2–R1.6, echte Provider, Style References,
Shared Working Media, Schema-21 ohne Zwang, neue Produktphase.

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

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO mit Reparse, keine neuen Discovery-bedingten Testfehler.
