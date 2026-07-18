# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Coverage Stability C1 abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Visual Edit Rework **V1–V3**: implementiert (`70ffe6e` / `0fba7bd` / `f1b982a`)
- Coverage Idempotency Plan: dokumentiert (`dbcc3bb`)
- **Coverage Stability C1**: Fixtures + reproduzierende Tests (`94b6e0f`)
- **End-to-End weiterhin blockiert** durch Coverage-Reset bei äquivalentem Audit
  (C1 dokumentiert das Fehlverhalten; kein Produktfix)
- Plan: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- **Nächster erlaubter Schritt nach Freigabe: Coverage Stability C2**
- **C3/C4, V4 und R1.4 gesperrt** bis Freigabe
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Idempotency Plan | dokumentiert |
| Coverage Stability C1 | **abgeschlossen** (Reproduktion) |
| Coverage Stability C2–C4 | nächster erlaubter Schritt (nach Freigabe: C2) |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Coverage Stability C1 — Nachweis

Reproduzierende Node-IDs:

- `tests/test_discovery_v2_coverage_stability_c1.py::test_equivalent_completed_runs_mint_different_audit_ids`
- `tests/test_discovery_v2_coverage_stability_c1.py::test_equivalent_second_audit_supersedes_existing_gaps`
- `tests/test_discovery_v2_coverage_stability_c1.py::test_equivalent_second_audit_resets_escalation_and_user_decision`
- `tests/test_discovery_v2_coverage_stability_c1.py::test_equivalent_second_audit_does_not_reuse_accepted_unresolved`
- `tests/test_discovery_v2_coverage_stability_c1.py::test_completed_manual_and_automatic_triggers_have_no_shared_input_reuse`
- `tests/test_discovery_v2_coverage_stability_c1.py::test_reproduction_uses_no_real_gateway_and_no_media_io`

Exakte FakeText-Audit-ID-Formel:

```text
uuid5(
  NAMESPACE_URL,
  "otio-discovery-v2-editorial:" + ":".join(
    ("coverage", project_id, script_id, observation_fingerprint, run_id)
  )
)
```

Quelle: `otio_app/discovery_v2/adapters/text_fake.py` (`_id` / `_coverage`).

Fehlender completed-input Reuse-Lookup: Worker reused nur bei exakter
`coverage_audit_id`-Trefferquote; nach `completed` startet ein neuer Run mit
neuer `run_id` → neue Audit-ID.

Supersede-/Gap-Materialisierungsreihenfolge (beobachtet):

1. `insert_coverage_audit` (Audit B)
2. `upsert_project_state` setzt `active_coverage_audit_id` auf B
3. später `materialize_gaps_from_current_coverage` →
   `supersede_gaps_not_in_audit` → `insert_coverage_gap` (neue UUID4-Gaps)

Fehlerfenster: Current zeigt bereits Audit B, während alte Gaps noch aktiv sind,
bis Materialisierung läuft.

Trigger: manueller `start_coverage_run` und automatische
`revalidate_coverage_after_accepted_reviews` teilen denselben Start-/Workerpfad;
aktive Runs werden per `editorial_run_already_active` blockiert; nach completed
kein fachlicher Input-Reuse.

## Teststand

**3017 collected / 2998 passed / 18 failed / 1 skipped** (~305s)

Baseline +6 C1-Tests (3011→3017, 2992→2998). 18 bekannte Classic/Without-VO-Fehler
und 1 VFR-Skip unverändert; nicht Gegenstand dieses Auftrags.

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **Coverage Stability C2** (Canonical Coverage Input / Dedup / Audit-Reuse)

Danach C3 (Gap Identity / Carry-Forward) → C4 (Atomarität / UI).

Noch gesperrt: V4, R1.4–R1.6, echte Provider, neue Produktphase,
Nutzerregistry-Reparatur.
