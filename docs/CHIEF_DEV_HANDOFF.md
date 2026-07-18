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
- Alpha-Produktstand-HEAD: `1ac7fba2bf2c1a7f0ae783a81c82495e2c7c600e`
- R1.1 Produktcommit: `f3e015bad59e063ec2717f8ef793ca55a1362ae2`
- R1.1 Script-Lock Identity: `8f4b9aacf0d854ff4dda02f80d37fa883d7797d7`
- R1.2 Produktcommit: `4c6bfd99c50de075c15597d94c728918697bf6c9`
- R1.3 Produktcommits: `45a5b4fd3144b7b0bfa6e0489e8cd9bbdbc9cc96`,
  `8b4c2ad1d1562e50657f2759a8e68debda469bf2`
- Chief-Dev Alpha-Produktstand: **APPROVED**
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Registry-Schema: **20**
- Teststand: **2988 collected / 2969 passed / 18 failed / 1 skipped**
- Provider: Fake-only · Adobe: UNKNOWN · NLE: lokaler OTIO-Serialize/Reparse
- **R1.1 abgeschlossen** · **R1.2 abgeschlossen** · **R1.3 abgeschlossen**
- Script-Lock Root Cause: Fingerprint an UI-Risikobestätigungen gekoppelt
  (zirkulär) → Preview jetzt fachlich unabhängig; Schlüssel `gap_id:risk_code`
- Manueller Realtest Script Lock (USA_v2) muss wiederholt werden
- R1.3 Acceptance Evidence weiterhin offen
- Decisions: D-R1.1-001…004 · D-R1.2-001…003 · D-R1.3-001…004
- **Nächster erlaubter Schritt nach Freigabe: R1.4**
- R1.5–R1.6 weiterhin gesperrt
- Keine neue Produktphase

## Nächste erlaubte Aktion nach Freigabe

→ **R1.4** (Job-UX / Progress-Polling)

**Noch gesperrt:** R1.5–R1.6, echte Provider, Style References,
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
- Discovery Route: URL + Application Service; session_state nicht alleinige Wahrheit
- Rendering/Reload/Rerun starten keine Jobs/Gateways/Medien-I/O automatisch

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO mit Reparse, keine neuen Discovery-bedingten Testfehler.
