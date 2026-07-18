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
- Aktueller HEAD: Coverage Stability C1 (Testcommit `94b6e0f`)
- V1–V3 Visual Edit: `70ffe6e` / `0fba7bd` / `f1b982a`
- Alpha-Produktstand-HEAD: `1ac7fba2bf2c1a7f0ae783a81c82495e2c7c600e`
- R1.1–R1.3 abgeschlossen · Script-Lock Realtest **erfolgreich**
- Chief-Dev Alpha-Produktstand: **APPROVED**
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Registry-Schema: **20**
- Teststand: **3017 collected / 2998 passed / 18 failed / 1 skipped**
- Provider: Fake-only · Adobe: UNKNOWN · NLE: lokaler OTIO-Serialize/Reparse
- Decisions: D-R1.1-001…004 · D-R1.2-001…003 · D-R1.3-001…004 ·
  D-VE-REWORK-001…006
- Visual Edit V1–V3: **abgeschlossen**; V3-Realtest:
  `additional_coverage_required` ehrlich
- **Coverage Stability C1 abgeschlossen** (Reproduktion, kein Produktfix)
- **Blocker End-to-End:** äquivalenter Coverage-Audit erhält neue Audit-ID →
  Gaps superseded → Eskalation/Decisions verloren → Script Lock erneut offen
- Plan: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- Fake-Audit-ID:
  `uuid5(NAMESPACE_URL, "otio-discovery-v2-editorial:coverage:…:run_id")`
- **Nächster erlaubter Schritt nach Freigabe: Coverage Stability C2**
- **C3/C4, V4 und R1.4 gesperrt** · echte Provider gesperrt
- Keine neue Produktphase · keine Nutzerregistry-Reparatur

## Nächste erlaubte Aktion nach Freigabe

→ **Coverage Stability C2** (Canonical Input / Dedup / Audit-Reuse)

**Noch gesperrt:** C3/C4 bis nach C2-Freigabe, Rework V4, R1.4–R1.6,
echte Provider, Style References, Shared Working Media, Schema-21 ohne Zwang,
neue Produktphase.

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
