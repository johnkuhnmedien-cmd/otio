# Decisions — Discovery V2 / Alpha

Dieses Dokument ist Source of Truth für verbindliche Produktentscheidungen.
Bestehende Einträge werden nicht umgeschrieben; neue Entscheidungen werden
angehängt.

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
`DECISIONS.md`, `MASTER_PLAN.md`, `ALPHA_SCOPE.md`, `PIPELINE_SPEC.md`,
`MEDIA_LIFECYCLE.md`, `EDITORIAL_QUALITY.md` und `MODEL_ROUTING.md`.

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
