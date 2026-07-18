# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Coverage Stability C2 + C2-R1 vollständig abgenommen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Visual Edit Rework **V1–V3**: implementiert
- Coverage Idempotency Plan: dokumentiert
- **Coverage Stability C1**: Reproduktion (`94b6e0f`)
- **Coverage Stability C2**: Canonical Input + Active-/Completed-Reuse (`7e8db48`)
- **Coverage Stability C2-R1**: Legacy Fail-Closed (`86b5ac1`)
- Decisions: D-COVERAGE-STABILITY-001…004
- **USA_v2-Realtest: erfolgreich** (C2-Abnahme)
- Plan: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- **Nächster zu planender Schritt: Coverage Stability C3**
- **C4, V4 und R1.4 gesperrt**
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Idempotency Plan | dokumentiert |
| Coverage Stability C1 | abgeschlossen (Reproduktion) |
| Coverage Stability C2 | **abgenommen** (Canonical Reuse + USA_v2) |
| Coverage Stability C2-R1 | **abgenommen** (Legacy Fail-Closed + USA_v2) |
| Coverage Stability C3 | nächster zu planender Schritt |
| Coverage Stability C4 | **gesperrt** |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## USA_v2 Coverage-Reuse-Realtest — Abnahme

Projekt: **USA_v2** · Schema **20**

| Feld | Wert |
|---|---|
| Aktiver Audit | `c2b32d64-3961-53bf-ab85-391932a2bf43` |
| Letzter Run | `7bb2273b-96ca-4942-9924-6de34b29d471` |

Zweiter identischer Coverage-Aufruf:

- completed Current Audit **reused**
- kein neuer Run
- keine neue Audit-ID
- keine neuen Gap-IDs
- keine Gap-Statusverluste

Aktuelle Gaps:

| Gap-ID | Status |
|---|---|
| `094f0390-cfb2-41e7-a19f-65ca4d583fb0` | `resolved_by_graphic_plan` |
| `2c36238e-6a1e-44ee-ae00-0abf8f398acf` | `accepted_unresolved` |
| `95a924fe-2995-4da7-80ff-d172cca3221b` | `in_progress` / `user_decision` |

Offener UI-Befund (nicht C2-blockierend): Visual-Intent-ID wird teilweise als Gap-ID beschriftet.

## Coverage Stability C2-R1 — Kurzstand

- Audits ohne gespeicherten Canonical Fingerprint: **kein Reuse**
- Keine Rekonstruktion aus mutable Brief/Narrative/Script-Bundle
- Genau ein normaler Recompute → neuer Audit speichert Fingerprint
- Zweiter identischer Aufruf → Completed Reuse (kein Gateway/Worker/Gaps)
- Active-Run-Reuse unverändert
- Diagnosegrund: `legacy_audit_missing_canonical_fingerprint`
- Decision: D-COVERAGE-STABILITY-004

## Teststand

**3043 collected / 3024 passed / 18 failed / 1 skipped** (~322s; +9 C2-R1-Tests)

18 bekannte Classic/Without-VO-Fehler und 1 VFR-Skip unverändert.

## Nächste erlaubte Aktivität

→ **Coverage Stability C3** (Gap Identity / Carry-Forward) — planen/implementieren nach Freigabe

Danach C4 (Atomarität / UI).

Noch gesperrt: C4, V4, R1.4–R1.6, echte Provider, neue Produktphase,
Nutzerregistry-Reparatur.
