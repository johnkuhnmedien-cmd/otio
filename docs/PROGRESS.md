# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Coverage Stability C2 vollständig abgeschlossen — C3-Planung aktiv — Schema weiterhin 20.**

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
- **Coverage Stability C2 + C2-R1**: vollständig abgeschlossen / USA_v2 abgenommen
- Decisions: D-COVERAGE-STABILITY-001…004
- **C3-Planung aktiv** (keine C3-Implementierung)
- C3-Plan: `docs/source_plans/ALPHA_COVERAGE_STABILITY_C3_GAP_IDENTITY_CARRY_FORWARD_PLAN.md`
- Roadmap: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- **Nächste erlaubte Aktion nach Planfreigabe: C3.1 Root-Cause-Fixtures**
- **C3.2–C3.4, C4, V4 und R1.4 gesperrt**
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Idempotency Plan | dokumentiert |
| Coverage Stability C1 | abgeschlossen (Reproduktion) |
| Coverage Stability C2 | **vollständig abgeschlossen** (Canonical Reuse + USA_v2) |
| Coverage Stability C2-R1 | **vollständig abgeschlossen** |
| Coverage Stability C3 | **Planung aktiv** (keine Implementierung) |
| Coverage Stability C3.1–C3.4 | gesperrt bis jeweilige Freigabe (nach Plan: zuerst C3.1) |
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

Nach Freigabe des C3-Plans:

→ **C3.1 Root-Cause-Fixtures** (Gap-ID-/Event-Bindung, Script-Lock-Auswirkung; keine Carry-Forward-Produktlogik)

Danach C3.2 → C3.3 → C3.4 nach Freigabe. C4 erst nach C3.

Noch gesperrt: C3.2–C3.4 bis Freigabe, C4, V4, R1.4–R1.6, echte Provider,
neue Produktphase, Nutzerregistry-Reparatur.
