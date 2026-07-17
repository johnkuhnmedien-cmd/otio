# Alpha Execution Manifest

## 1. Zweck und Geltungsbereich

Dieses Dokument ist der **beschleunigte Alpha-Ausführungsplan** für Discovery V2.

Es bündelt die Reihenfolge der Makrophasen und Provider-Gates. Es ist ausdrücklich
**untergeordnet** gegenüber:

- `.cursor/rules/00-core-architecture.mdc`
- `.cursor/rules/01-step-discipline.mdc`
- `docs/DECISIONS.md`
- `docs/MASTER_PLAN.md`
- `docs/ALPHA_SCOPE.md`
- `docs/PIPELINE_SPEC.md`
- `docs/MEDIA_LIFECYCLE.md`
- `docs/EDITORIAL_QUALITY.md`
- `docs/MODEL_ROUTING.md`
- `docs/CLASSIC_MIGRATION_CONTRACT.md`

Bei Widerspruch gelten die höheren Quellen. Das Manifest darf keine fachliche
Entscheidung still verändern.

Die genannten höheren Dokumente sind als **RECONSTRUCTED_BOOTSTRAP**
gekennzeichnet: für dieses Repository neu konsolidiert; andere Projekte sind
keine fachliche Quelle; verbindlich ist der verifizierte Inhalt ab dem
Provenienz-Bereinigungscommit.

**MANUAL bleibt Alpha-Standard.** Keine stillen Automationen, keine ungefragten Uploads.

## 2. Aktueller Stand

- Phase 7 Media Intake — abgeschlossen
- Phase 8A Analysis Contracts — abgeschlossen
- Phase 8B Shot-/Frame-Prepare — abgeschlossen
- Phase 8C Fake Vision Model Analysis — abgeschlossen
- Phase 8D Observation Review / Editorial-Ready-Gate — abgeschlossen
- SoT Bootstrap + Provenienz-Bereinigung (Dokumentation) — abgeschlossen
- Registry-Schema: **14**
- Fake Vision: aktiv (`provider=fake`)
- Echter Vision-Provider: **gesperrt** (separates Gate)
- Phase 9 Produktarbeit: **nicht begonnen**

## 3. Architekturregeln

- UI → Application → Domain → Adapters → Persistence
- SQLite ist die interne Wahrheit
- JSON-Artefakte sind versioniert und relativ unter `_otio_v2/`
- LLM/Vision nur über zentrale Gateways
- Python für Timing, Technik und Export
- nur `completed` Working Media als Analysebasis
- Classic bleibt read-only für Discovery
- keine Originalmedien ändern
- keine `_otio/`-Schreibzugriffe durch Discovery

## 4. Beschleunigter Prozess

Normalfall pro Makrophase:

1. ein größerer Auftrag
2. gezielte Tests
3. Vollsuite
4. echter Kern-Smoke (lokal, MANUAL)
5. ein Review

R1 nur bei echtem Defekt oder kritischer Nachweislücke.

## 5. Phase 9 — Editorial Core und Coverage

Mindestens:

- Project Brief
- Narrative Plan
- Hook-Varianten
- Script Draft
- Claims
- Sätze
- Visual Beats
- Visual Intents
- Coverage Audit
- noch kein Script Lock

Eingabe aus Phase 8: nur **editorial-ready** Visual Observations (accepted, aktuell, gültig).
`accepted` ist keine Asset-Auswahl und keine Faktenfreigabe.

## 6. Phase 10 — Supplementation und Script Lock

Mindestens:

- Coverage-Gaps
- Eskalationsreihenfolge
- providerneutrale Supplementation
- Adobe OAuth bleibt UNKNOWN
- Script-Lock-Gate
- Stale-Regeln

## 7. Phase 11 — Voice, Pausen und Timing

Mindestens:

- Fake Voice zuerst
- optional ElevenLabs hinter Provider-Gate
- LLM-Pausenfunktion
- Python-Timingauflösung
- Narration Timeline

## 8. Phase 12 — Visual Edit Plan und Quality

Mindestens:

- konkrete Shot-Instanzen
- Satz/Shot-Many-to-Many
- Humanity & Authenticity Review
- Feasibility
- deterministische und redaktionelle Repairs

## 9. Phase 13 — Review und OTIO

Mindestens:

- Editorial Review
- Export Validation
- OTIO Export
- Reparse
- kompletter Alpha-End-to-End-Smoke

## 10. Provider-Gates

Getrennt dokumentieren und freigeben:

| Gate | Status |
|---|---|
| Vision Provider | gesperrt (Fake only) |
| Text-LLM-Provider | Classic/bestehend; Discovery Phase 9+ |
| ElevenLabs | gesperrt bis Phase 11 |
| Adobe Stock | UNKNOWN / später |

Keine stillen Provideraktivierungen.

## 11. Definition of Done (Alpha)

- vollständiger MANUAL-Hauptpfad
- keine Originaländerung
- Stale-Gates
- versionierte Artefakte
- validiertes Working Media
- Humanity Review
- Feasibility
- Nutzerfreigabe
- parsebares OTIO
- kein neuer Discovery-bedingter Testfehler

## 12. Post-Alpha

Mindestens verschoben:

- AUTOMATIC
- vollständiges Adobe OAuth
- HEIC und exotische TIFF-Varianten
- OCR
- Gesichtserkennung
- bestätigte Geolokalisierung
- automatische Synthetic-Erkennung
- verteilte Queue
- Multi-User
- Cloud Storage
- umfassende Performanceoptimierung

## 13. Stop-Regeln

Makrophase stoppen bei:

- Originaländerung
- Datenverlust
- Secret-Leak
- ungefragtem Upload
- falscher Medienversion
- nicht versionierter Fachausgabe
- unvalidiertem Script Lock
- ungültigen Timingdaten
- unzulässigem OTIO-Pfad
- nicht parsebarem Export
