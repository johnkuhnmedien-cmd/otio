# Decisions — Discovery V2 / Alpha

Dieses Dokument ist Source of Truth für verbindliche Produktentscheidungen.
Bestehende Einträge werden nicht umgeschrieben; neue Entscheidungen werden
angehängt.

Source-of-Truth-Reihenfolge (Auszug): Regeln `00`/`01` → dieses Dokument →
`MASTER_PLAN` → … → `CHIEF_DEV_HANDOFF` → `docs/source_plans/*`
(nachrangig; ggf. leer; überschreibt nichts Höheres).
Bei Widerspruch gilt die höhere Quelle.

---

## D-BOOTSTRAP-001 — Rekonstruierte Bootstrap-Source-of-Truth

**Entscheidung:** Die fehlenden Architektur- und Planungsdokumente werden als
transparent gekennzeichnete **RECONSTRUCTED_BOOTSTRAP**-Basis angelegt und gelten
ab dem Bootstrap-Commit als Repositoryvertrag. Sie erheben keinen Anspruch, den
exakten Wortlaut früherer, nicht auffindbarer Originale wiederzugeben
(`DISCOVERY-V2-SOURCE-OF-TRUTH-RECOVERY-001` → `NOT_FOUND`).

**Kontext:** Nach Repository- und Dateisystemsuche waren keine historischen
Originale der höheren SoT-Dateien auffindbar. Die Rekonstruktion konsolidiert
akzeptierten Handoff, Code/Tests der Phasen 7–8, bestehende Decisions und
externe Audit-Referenzen (Vergleichsquellen, nicht höhere SoT). Ungeklärte
API-/OAuth-/Lizenz-/Providerdetails bleiben UNKNOWN.

**Betroffene Dateien:**

- `.cursor/rules/00-core-architecture.mdc`
- `.cursor/rules/01-step-discipline.mdc`
- `docs/MASTER_PLAN.md`
- `docs/ALPHA_SCOPE.md`
- `docs/PIPELINE_SPEC.md`
- `docs/MEDIA_LIFECYCLE.md`
- `docs/EDITORIAL_QUALITY.md`
- `docs/MODEL_ROUTING.md`
- `docs/CLASSIC_MIGRATION_CONTRACT.md`
- `docs/CHIEF_DEV_HANDOFF.md`

---

## D-8D-001 — Beschleunigte Makrophasen 9–13

**Entscheidung:** Nach Abschluss von Phase 8 folgt der Alpha-Pfad in fünf
beschleunigten Makrophasen (9 Editorial Core, 10 Supplementation/Script Lock,
11 Voice/Timing, 12 Visual Edit Plan/Quality, 13 Review/OTIO), wie im
Alpha Execution Manifest beschrieben.

**Kontext:** Phase 8 liefert lokale Assetanalyse inkl. Fake Vision und
Observation Review. Der weitere Alpha-Pfad wird gebündelt, ohne MANUAL oder
höhere Spezifikationen zu ersetzen.

---

## D-8D-002 — Alpha Execution Manifest ist untergeordnet

**Entscheidung:** `docs/ALPHA_EXECUTION_MANIFEST.md` ist ein
Ausführungs-/Bündelungsplan und bleibt untergeordnet gegenüber
`.cursor/rules/00-core-architecture.mdc`,
`.cursor/rules/01-step-discipline.mdc`, `DECISIONS.md`, `MASTER_PLAN.md`,
`ALPHA_SCOPE.md`, `PIPELINE_SPEC.md`, `MEDIA_LIFECYCLE.md`,
`EDITORIAL_QUALITY.md`, `MODEL_ROUTING.md` und
`CLASSIC_MIGRATION_CONTRACT.md`.

Bei Widerspruch gelten die höheren Quellen. Das Manifest darf keine fachliche
Entscheidung still verändern.

---

## D-8D-003 — Echter Vision-Provider bleibt separates Gate

**Entscheidung:** Phase 8 schließt mit Fake Vision ab. Gemini/OpenAI/Anthropic/
OpenRouter/xAI Vision bleiben gesperrt, bis ein eigener Provider-Gate-Auftrag
sie freigibt. Kein stiller Produktiv-Upload, kein HTTP/SDK in Discovery Vision.

---

## D-8D-004 — Akzeptierte Visual Observation ist keine Asset- oder Faktenfreigabe

**Entscheidung:** `accepted` bedeutet nur, dass die strukturierte Visual
Observation als geprüfte redaktionelle Eingabe für eine spätere Phase angeboten
werden darf. Es bedeutet nicht:

- automatische Asset-Auswahl für das Video
- faktische Bestätigung
- bestätigte Geografie
- bestätigte Synthetic-Klassifikation
- dramaturgische Eignung
- Freigabe für Visual Beats

---

## D-8D-005 — Observation-Reviewhistorie ist unveränderlich und versioniert

**Entscheidung:** Reviews sind append-only Ereignisse mit monoton steigender
`review_revision` je `observation_id`. Bestehende Reviewzeilen werden nicht
überschrieben oder gelöscht. Der aktuelle Review ist die höchste Revision.
`reanalyze_requested` startet keinen automatischen Model-Run.

---

## D-DOC-006 — Bootstrap nutzte fremdes Ausgangsmaterial

**Entscheidung / Feststellung:** Bei der ersten Bootstrap-Erstellung
(`44e6bf0c2ceeef787c607380f3cb8d6022947ef0`) wurden Dokumentstrukturen
beziehungsweise Inhalte aus einem anderen Projekt als Ausgangsmaterial
verwendet.

**Kontext:** Die so entstandenen Dateien sind keine wiedergefundenen Discovery-V2-
Originale. Sie erfordern eine Provenienz-Bereinigung gegen ausschließlich
Discovery-V2-Quellen.

---

## D-DOC-007 — Andere Projekte sind keine Discovery-V2-SoT

**Entscheidung:** Andere Projekte sind keine Source of Truth für Discovery V2.
Projektfremde Fachinhalte wurden entfernt oder anhand des aktuellen
Discovery-V2-Stands (Handoff, Code, Tests, akzeptierte Decisions/Manifest,
zugelassene Audit-Abgrenzung) neu verifiziert und geschrieben.

**Kontext:** Verbindlich ist der für Discovery V2 verifizierte Inhalt ab dem
Provenienz-Bereinigungscommit. Übernommene Dokumentstrukturen besitzen keine
normative Bedeutung. Nicht belegte externe Details bleiben UNKNOWN.

---

## D-DOC-008 — Korrektur zur Provenienzannahme in D-DOC-006

**Entscheidung / Feststellung:** Die Aussage in D-DOC-006 beruhte auf einer
später korrigierten Provenienzannahme. Es gibt keinen belastbaren Nachweis, dass
Cursor fachliche Source-of-Truth-Dokumente aus einem fremden Repository
übernommen hat.

**Kontext:** D-DOC-006 bleibt aus Append-only-Gründen historisch sichtbar und
gilt nur zusammen mit D-DOC-008 bis D-DOC-010.

---

## D-DOC-009 — Gelöschte GPT-Wissensdateien sind keine Repositoryquelle

**Entscheidung:** Gelöschte oder frühere GPT-Wissensdateien sind keine
Repositoryquelle und verändern weder Git-Historie noch Produktcode.

---

## D-DOC-010 — Wiederherstellung verbindlicher Bootstrap-Regeln

**Entscheidung:** Beim Provenance-Rework (`9daf2739c22279872bc096f5970eb02e2c2c8357`)
wurden mehrere verbindliche Discovery-V2-Regeln zu stark entfernt.
CHECKPOINT-Vorbereitung, LLM-Pausenregie, Providerkonfigurierbarkeit,
Adobe-Reihenfolge, Stock-Eskalation und Humanity-Kriterien werden mit dem
Authority-Restore-Commit wiederhergestellt.

**Kontext:** D-DOC-006 und D-DOC-007 bleiben historisch sichtbar, gelten aber nur
zusammen mit D-DOC-008 bis D-DOC-010.

---

## D-9-001 — DiscoveryTextGateway mit FakeTextAdapter

**Entscheidung:** Phase 9 verwendet einen eigenen zentralen DiscoveryTextGateway
mit FakeTextAdapter als erstem Adapter. Alle Editorial-Textaufrufe laufen über
diesen Gateway. Keine direkten Adapteraufrufe aus UI/Domain; kein stiller Fallback.

---

## D-9-002 — Phase 9 endet nach Coverage Audit

**Entscheidung:** Phase 9 endet nach dem Coverage Audit. Stock, Script Lock,
Voice, Timing und OTIO bleiben Folgephasen.

---

## D-9-003 — Getrennte versionierte Editorial-Modelle

**Entscheidung:** Project Brief, Narrative Plan, Hook, Script, Sentence, Claim,
Visual Beat, Visual Intent und Coverage Audit sind getrennte versionierte Modelle.

---

## D-9-004 — Many-to-Many Beats und keine Kapitel aus Source Groups

**Entscheidung:** Ein Satz kann mehreren Visual Beats zugeordnet sein, und ein
Visual Beat kann mehrere Sätze enthalten. Source Groups erzeugen keine Kapitel.

---

## D-9-005 — Nutzerbearbeitungen versionieren und stale machen

**Entscheidung:** Nutzerbearbeitungen erzeugen neue Script-Versionen und machen
strukturierte Ableitungen sowie Coverage stale. Strukturaktualisierung ist
explizit (`structure_pending` bis zum Structure-Run).

---

## D-9-006 — Coverage nur mit accepted Observations, max. fünf Kandidaten

**Entscheidung:** Coverage verwendet ausschließlich aktuelle accepted Observations,
begrenzt lokale Kandidaten auf fünf pro Intent (ohne stille Kürzung) und startet
keine Stock-Suche.

---

## D-10-001 — StockSearchGateway mit FakeStockSearchAdapter

**Entscheidung:** Phase 10 verwendet einen zentralen StockSearchGateway mit
FakeStockSearchAdapter. Echte Stockprovider bleiben gesperrt.

---

## D-10-002 — Candidate ≠ Asset / Working Media / Editorial Asset

**Entscheidung:** StockCandidate, Preview, Original, Working Media und Editorial Asset
sind getrennte Zustände. Candidate-Akzeptanz erzeugt kein Asset.

---

## D-10-003 — Coverage-Gap-Eskalation versioniert und append-only

**Entscheidung:** Die Coverage-Gap-Eskalationsreihenfolge ist versioniert und
append-only nachvollziehbar.

---

## D-10-004 — Claimentscheidungen menschlich und versionsgebunden

**Entscheidung:** Claimentscheidungen sind menschliche, append-only Ereignisse und an
die exakte Claim- und Script-Version gebunden.

---

## D-10-005 — Script Locks als unveränderliche Snapshots

**Entscheidung:** Script Locks sind unveränderliche Snapshots der aktuellen Brief-,
Hook-, Script-, Struktur-, Coverage-, Claim- und Observation-Kette.

---

## D-10-006 — Lock-Invalidierung bei Input-Änderung

**Entscheidung:** Änderungen an einem Lock-Input invalidieren den bisherigen Lock. Ein
neuer Lock ist erforderlich.

---

## D-10-007 — Candidate löst Gap erst nach Intake und Re-Audit

**Entscheidung:** Ein akzeptierter Stockkandidat löst einen Coverage Gap erst nach
normalem Intake, aktueller Analyse, Review und erneutem Coverage Audit.

---

## D-10-008 — Adobe OAuth / Lizenz / Auto-Download bleiben UNKNOWN

**Entscheidung:** Adobe OAuth, Lizenzierung, Preise und automatische Downloads bleiben
UNKNOWN und außerhalb des Fake-Alpha-Pfads.

---

## D-11-001 — VoiceGenerationGateway mit FakeVoiceAdapter

**Entscheidung:** Phase 11 verwendet einen zentralen providerneutralen
VoiceGenerationGateway mit FakeVoiceAdapter als erstem Adapter.

---

## D-11-002 — Wirksamer Script Lock als Narration-Startbedingung

**Entscheidung:** Phase 11 darf ausschließlich von einem aktuellen wirksamen Script
Lock gestartet werden.

---

## D-11-003 — Fake-Voice-WAV-Vertrag

**Entscheidung:** Der Fake-Voice-Pfad erzeugt deterministische lokale WAV-Segmente mit
PCM s16le, 48 kHz und Mono. Er behauptet keine natürliche Stimme.

---

## D-11-004 — Pausenregie über DiscoveryTextGateway

**Entscheidung:** Die Pausenregie läuft über den zentralen DiscoveryTextGateway. Sie
liefert Funktionen und Dauerabsichten, keine finalen Frames.

---

## D-11-005 — Deterministische Python-Timingauflösung

**Entscheidung:** Python löst Audio, Pausen, Visual-only-Intervalle, Sekundenwerte und
rationale Framegrenzen deterministisch auf.

---

## D-11-006 — Versionierte Narration-Artefakte und Stale-Kette

**Entscheidung:** Voice-Segmente, Pause-Pläne und Narration Timelines sind versioniert
und werden durch Änderungen ihrer exakten Inputkette stale.

---

## D-11-007 — Rationale Timebases

**Entscheidung:** Narration verwendet rationale Timebases. 23,976 und 29,97 werden als
24000/1001 beziehungsweise 30000/1001 gespeichert.

---

## D-11-008 — ElevenLabs bleibt separates Freigabegate

**Entscheidung:** ElevenLabs und andere reale Voiceprovider bleiben ein separates
Freigabegate und sind im lokalen Fake-Alpha-Pfad nicht aktiviert.

---

## D-11-009 — Direkte Versions-, Script- und Ordnungsbindungen

**Entscheidung:** Voice Profile, Voice Segment und Pause Direction speichern ihre
Versions-, Script- und Ordnungsbindungen nach der Phase-11-Verifikation direkt und
eindeutig.

---

## D-11-010 — Ungültige Pause-Referenzen sind permanent

**Entscheidung:** Ungültige Satz- oder Segmentreferenzen in einer Pause-Antwort sind
permanente Fehler und werden nicht erneut an den Textadapter gesendet.

---

## D-11-011 — Schema 18 als enge Phase-11-Korrekturmigration

**Entscheidung:** Schema 18 ist eine enge Phase-11-Korrekturmigration ohne neue
Fachphase oder neue Tabellen.

---

## D-12-001 — Editorial Shots sind eigenständige Modelle

**Entscheidung:** Editorial Shots sind eigenständige redaktionelle Modelle und weder
Technical Shots noch Assets.

---

## D-12-002 — Nur aktuelle Produktionsmedien in Phase 12

**Entscheidung:** Phase 12 verwendet ausschließlich aktuelle completed Working Media,
aktuelle Analysis Identities und akzeptierte Visual Observations.

---

## D-12-003 — LLM plant, Python löst technisch auf

**Entscheidung:** LLM beziehungsweise Fake Text plant redaktionelle Shotfunktionen und
Repair-Vorschläge; Python löst Source-Ranges, Frames und technische Machbarkeit auf.

---

## D-12-004 — Humanity & Authenticity als Pflichtreview

**Entscheidung:** Humanity & Authenticity ist ein eigenständiger Pflichtreview vor der
finalen Editorial Review.

---

## D-12-005 — Deterministische Feasibility

**Entscheidung:** Feasibility ist deterministisch und prüft Timeline, Source-Ranges,
Working-Media-Verträge und technische Blocker.

---

## D-12-006 — Repairs erzeugen neue Planversionen

**Entscheidung:** Repairs erzeugen neue Planversionen. Redaktionelle Repairs werden
nicht still angewandt.

---

## D-12-007 — Planned Graphics ohne Working Media blockieren

**Entscheidung:** Planned Graphics ohne reales Working Media blockieren die technische
Bereitschaft.

---

## D-12-008 — ready_for_editorial_review ist keine Exportfreigabe

**Entscheidung:** `ready_for_editorial_review` ist keine finale Exportfreigabe. OTIO
bleibt Phase 13 vorbehalten.

---

## D-13-001 — Finale Editorial Approval ist menschlich

**Entscheidung:** Finale Editorial Approval ist eine ausdrückliche menschliche
Entscheidung und kann nicht durch ein Modell oder einen automatischen Run erzeugt
werden.

---

## D-13-002 — Approval an exakten aktuellen Zustand gebunden

**Entscheidung:** Approval ist an den exakten aktuellen Script-, Narration-,
Visual-Edit-, Humanity-, Feasibility-, Repair- und Medienzustand gebunden.

---

## D-13-003 — Export Validation als Pflichtgate

**Entscheidung:** Export Validation ist ein deterministisches Pflichtgate vor jedem
OTIO-Export.

---

## D-13-004 — OTIO nur Working Media und Narration-WAVs

**Entscheidung:** OTIO referenziert ausschließlich aktuelle validierte Working Media
und aktuelle validierte Narration-Audiosegmente.

---

## D-13-005 — Zentraler Discovery-V2-OTIO-Adapter

**Entscheidung:** OpenTimelineIO-Erzeugung erfolgt über einen zentralen
Discovery-V2-Adapter und enthält keine redaktionelle Entscheidungslogik.

---

## D-13-006 — Completed erst nach Reparse und Semantik

**Entscheidung:** Ein OTIO-Export gilt erst nach erfolgreichem Reparse und
semantischem Vergleich als abgeschlossen.

---

## D-13-007 — KI-Timelines bleiben NEGATIVE_REFERENCE

**Entscheidung:** Die vorhandenen KI-Timelines bleiben ausschließlich
`NEGATIVE_REFERENCE` und sind keine positive Export- oder Schnittvorlage.

---

## D-13-008 — Phase 13 schließt den lokalen Fake-Alpha-Pfad ab

**Entscheidung:** Phase 13 schließt den lokalen Fake-Alpha-Pfad ab. Proprietäre
NLE-Exporte, reale Provider, Veröffentlichung und Cloud-Rendering bleiben außerhalb
des Alpha-Scopes.

---

## D-R1.1-001 — Acceptable Coverage Risks sind Allow-List

**Entscheidung:** Sichtbare akzeptierbare Coverage-Risiken werden nur über eine
explizite, deterministische Abbildung aus `missing_properties` abgeleitet.
Mindestens: `exact_match_not_verified` → `coverage_exact_match_not_verified`.
Unbekannte `missing_properties` gelten nicht automatisch als akzeptierbar.
Risikoannahme (`accepted_unresolved`) erfordert offene/in_progress Gaps auf
Eskalation `user_decision`, aktuelle Candidate Decisions alle `rejected`,
mindestens ein sichtbares akzeptierbares Risiko und ausdrückliche
Nutzerbestätigung. Historische Candidate Decisions zählen nicht; nur die
aktuelle Entscheidung pro Candidate.

---

## D-R1.1-002 — Coverage-Persistenz ist staged und idempotent

**Entscheidung:** Coverage-Materialisierung bleibt atomar bezüglich Current State:
ein fehlgeschlagener Run wird niemals Current; ein vorheriger gültiger Current
Audit bleibt bei Fehlern erhalten; keine halben Gap-Sätze; identischer
Wiederanlauf ist idempotent. Persistenzfehler werden staged ausgewiesen
(z. B. `coverage_artifact_publish_failed`, `coverage_audit_persist_failed`,
`coverage_current_state_update_failed`) statt undifferenziertem Catch-all.
Coverage-Audit-IDs müssen pro Run eindeutig sein (inkl. `run_id`).

---

## D-R1.1-003 — Script-Lock-Fingerprint ist serverseitig

**Entscheidung:** Der Script-Lock-Fingerprint wird serverseitig berechnet und
angezeigt; freies Editieren entfällt. Lock erfordert Checkbox-Bestätigung des
angezeigten Stands. Fingerprint-Änderung zwischen Anzeige und Klick blockiert
mit `code=script_lock_fingerprint_mismatch`. Ein historischer fehlgeschlagener
Coverage-Run blockiert den Lock nicht, solange ein gültiger Current Audit
vorliegt und alle Gaps terminal sind; geänderte Inputs seit dem Audit blockieren
mit `coverage_audit_stale`.

---

## D-R1.2-001 — Discovery-Route ist reload-fähig und kanonisch

**Entscheidung:** Discovery-V2-Projekt und -Seite besitzen einen reload-fähigen
kanonischen Routenzustand über Query-Parameter (`project_id`, `page`) und die
bestehenden Streamlit-`url_path`-Segmente. `st.session_state` darf UI-Zustand
cachen, ist aber nicht alleinige Wahrheit für Project-ID, Project Mode oder
aktuelle Discovery-Seite. Der Projektmodus wird aus dem persistierten Projekt
geladen und nicht blind aus der URL vertraut. Ungültige Project-IDs und
Moduskonflikte führen zu verständlicher Projektauswahl bzw. Moduskonflikt —
nicht zu einem stillen Classic-Fallback. Unbekannte Seiten fallen kontrolliert
auf die Discovery-Startseite (`overview`) zurück; die Project-ID bleibt erhalten.

---

## D-R1.2-002 — Erfolgreiche Mutationen laden frische Viewmodels

**Entscheidung:** Erfolgreiche synchrone Discovery-V2-Mutationen und explizite
Jobstarts speichern eine Flash-Meldung und lösen genau einmal `st.rerun()` aus.
Der Folgerender lädt das Application-Viewmodel neu. Es gibt kein stilles
Auto-Accept und keine doppelte fachliche Mutation durch den Rerun.

---

## D-R1.2-003 — Rendering und Rerun starten keine I/O-Folgen

**Entscheidung:** Rendering, Browser-Reload und kontrollierte Reruns starten
keine Jobs, Gateways oder Medienoperationen automatisch. Jobstarts erfolgen
nur über explizite Buttonklicks; ein Rerun nach Jobstart erzeugt denselben Job
nicht erneut. Automatisches Polling bleibt R1.4 vorbehalten.

---

## D-R1.3-001 — Vision-Analyseeinheiten sind assetgebunden

**Entscheidung:** Eine Fake-Vision-/Gateway-Anfrage entspricht genau einem Asset.
Alle zulässigen Representative Frames dieses Assets werden gemeinsam analysiert.
Frames verschiedener Assets werden nicht in derselben Anfrage vermischt.
Asset-ID und Frame-IDs sind explizit im Requestvertrag; Fake-Alpha-Parallelität
ist genau eins; die Queue-Reihenfolge ist deterministisch (Asset-ID).

---

## D-R1.3-002 — Visual Observations bleiben bis zur Reviewentscheidung unreviewed

**Entscheidung:** Fake Vision erzeugt Observations mit Reviewstatus `unreviewed`.
Es gibt kein stilles Auto-Accept durch Adapter oder Worker. Erst eine
ausdrückliche Einzel- oder Batch-Reviewentscheidung ändert den aktuellen Status.
Batch-Aktionen erzeugen append-only Einzelentscheidungen mit gemeinsamer
Batch-ID (Schema 20: kodiert in bestehenden TEXT-Feldern).

---

## D-R1.3-003 — Coverage nutzt nur aktuelle akzeptierte Observations

**Entscheidung:** Unreviewed und rejected Observations sind keine
Coverage-Inputs. Nach einer relevanten akzeptierten Reviewänderung wird
höchstens ein Coverage-only-Run angelegt und außerhalb des UI-Renders
ausgeführt. Mehrere Decisions derselben Batchaktion erzeugen höchstens einen
Coverage-Run. Historisch gültige Current Audits bleiben erhalten, bis der neue
Audit erfolgreich Current wird.

---

## D-R1.3-004 — Supplement erfüllt Coverage erst nach regulärem Pfad

**Entscheidung:** Supplement Assets können Gaps erst terminal lösen, wenn sie
als Kandidat markiert wurden, echtes Original via Media Intake vorhanden ist,
Working Media completed ist, Analysevorbereitung und Vision-Observation
vorliegen und die Observation ausdrücklich akzeptiert wurde. Candidate Preview,
ungeprüfte Observations oder Modellvorschläge allein lösen keinen Gap.
Terminalstatus bei bestätigtem Match: `resolved_with_supplement`.
