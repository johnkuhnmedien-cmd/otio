> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Master Plan — Discovery V2

Untergeordnet gegenüber `.cursor/rules/*` und `docs/DECISIONS.md`.

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
13. `docs/source_plans/*` (nachrangig; ggf. leer/nicht vorhanden; überschreibt nichts Höheres)

## Fachliche Hauptpipeline

```text
Projekt und Assets
→ Skript und Visual Beats
→ Coverage Audit
→ Stock-Supplementation
→ Script Lock
→ ElevenLabs
→ LLM-Pausenregie
→ Python-Timingauflösung
→ Visual Edit Plan
→ Humanity Review
→ Feasibility und Repair
→ Freigabe
→ OTIO-Export
```

Verbindliche Reihenfolgen:

- Coverage vor Stock-Supplementation
- Script Lock vor Voice / ElevenLabs — **keine finale Voice vor Script Lock**
- LLM-Pausenregie vor Python-Timingauflösung
- finaler Narration-Timing vor Visual-Edit-Timeline — **keine Visual-Edit-Timeline vor finalem Narration Timing**
- Humanity Review und Feasibility vor Freigabe/Export

## Pausenregie — Verantwortung

**LLM:** Pausenfunktion, Cold Open, visuelle Atemzüge, Übergangspausen,
andere redaktionelle Pausenentscheidungen.

**Python:** exakte Zeitauflösung, Dauern, Überlappungsprüfung, Framerundung,
technische Konsistenz.

## Implementierter Vorlauf (Phasen 7–8)

```text
Projekt anlegen (discovery_v2)
→ Inventory → Auswahl → Registry → Validierung
→ Media Intake (Copy / Remux / Transcode / TIFF→PNG)
→ Analysis Contracts → Prepare → Fake Vision → Observation Review
```

Eingabe Phase 9: nur editorial-ready Observations (`accepted`, aktuell, gültig).

## Makrophasen

| Phase | Inhalt | Status |
|---|---|---|
| 7 | Media Intake | APPROVED |
| 8 | Assetanalyse (8A–8D) | APPROVED |
| 9 | Editorial Core und Coverage | nächster Produktschritt; nicht begonnen |
| 10 | Supplementation und Script Lock | geplant |
| 11 | Voice, LLM-Pausenregie und Timing | geplant |
| 12 | Visual Edit Plan und Quality (inkl. Humanity) | geplant |
| 13 | Review und OTIO | geplant |

Ausführungsbündelung: `docs/ALPHA_EXECUTION_MANIFEST.md` (untergeordnet).

### Phase 9

Project Brief, Narrative Plan, Hook-Varianten, Script Draft, Claims, Sätze,
Visual Beats, Visual Intents, Coverage Audit; noch kein Script Lock.

### Phase 10

Coverage-Gaps, Stock-Eskalation, providerneutrale Supplementation, Script-Lock-Gate,
Stale-Regeln. Adobe OAuth-Variante: **UNKNOWN**.

### Phase 11

Fake Voice zuerst; optional ElevenLabs hinter Gate; LLM-Pausenregie;
Python-Timingauflösung; Narration Timeline.

### Phase 12

Shot-Instanzen; Satz/Shot Many-to-Many; **Humanity & Authenticity Review**
(eigener Schritt); Feasibility; deterministische und redaktionelle Repairs.

### Phase 13

Editorial Review; Export Validation; OTIO Export; Reparse; Alpha-E2E-Smoke (MANUAL).

## Betriebsarten

- MANUAL = Alpha-Standard
- CHECKPOINT = architektonisch vorbereitet, nicht als fertig implementiert behaupten
- AUTOMATIC = Post-Alpha / spätere Phase

## NEGATIVE_REFERENCE

Hinterlegte KI-Timelines sind ausschließlich `NEGATIVE_REFERENCE` — keine
Qualitätsvorlage, keine positive Schnitt-/Dramaturgieregel ableiten.
