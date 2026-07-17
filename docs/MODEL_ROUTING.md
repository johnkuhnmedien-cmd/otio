> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Model Routing — Discovery V2

Routing, Gates und Verbote für LLM-/Vision-/Voice-Aufrufe in Discovery.

## Zentrale Gateways

- **Discovery Vision Gateway** (`otio_app/discovery_v2/adapters/vision_gateway.py`) — multimodal, Discovery-only
- Classic Text-LLM-Router (`plan_llm_client`) bleibt Classic/bestehend; nicht als Discovery-Vision-Gateway erweitern
- Alle Discovery-Vision-Aufrufe: Application → Gateway → Adapter
- Keine direkten Providerimporte in UI, Domain oder Prepare-/Fachworkern außerhalb des Gateway-Adapters

## Aktueller Vision-Stand (Phase 8)

| Einstellung | Wert |
|---|---|
| Provider | `fake` |
| Model Identifier | `fake-vision-v1` |
| Enabled | `true` |
| Echte Provider | **gesperrt** |

Consent ist pro Model-Run erforderlich. Cache bindet Identity- und Config-Versionen sowie Frame-Hash-Fingerprint.

## Provider-Gates

| Gate | Status |
|---|---|
| Vision (Gemini/OpenAI/Anthropic/OpenRouter/xAI) | gesperrt bis eigener Auftrag |
| Text-LLM für Editorial (Phase 9+) | Classic-Infrastruktur nutzbar hinter Discovery-Application; keine hart codierten Modelle in Fachmodulen |
| ElevenLabs / Voice | gesperrt bis Phase-11-Gate; Fake Voice zuerst |
| Adobe Stock / OAuth | **UNKNOWN** / später |

Keine stillen Provideraktivierungen. OpenRouter und direkte Provider sind konfigurierbar, sobald ein Gate sie freigibt.

## Verbote

- hart codierte Modell-IDs in Domain-/Application-Fachmodulen
- Secret-Leak in Logs, Artefakten, Fehlermeldungen
- ungefragte Uploads
- Vision über Text-only-Client „nebenbei“
- Model-Start durch Streamlit-Rerun ohne Consent

## LLM- vs. Python-Zuständigkeit

Siehe `00-core-architecture.mdc`: LLM für redaktionelle Inhalte; Python für Timing, Ranges, Hashes, Validierung, Export.
