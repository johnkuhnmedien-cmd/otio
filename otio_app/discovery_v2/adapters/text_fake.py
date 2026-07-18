"""Deterministic offline fake text adapter for Discovery V2 Phase 9."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from otio_app.discovery_v2.domain.editorial import (
    CoverageAuditStatus,
    CoverageStatus,
    HookUserStatus,
    NarrativePlanStatus,
    RESPONSE_SCHEMA_COVERAGE,
    RESPONSE_SCHEMA_NARRATIVE,
    RESPONSE_SCHEMA_SCRIPT,
    ScriptDraftStatus,
    ScriptSourceKind,
    TextGatewayRequest,
    compute_text_sha256,
)
from otio_app.discovery_v2.domain.narration import (
    NARRATION_RUN_SCOPE_PAUSE,
    PauseFunction,
    PauseHardness,
    PausePositionKind,
    PauseUncertainty,
    PROMPT_VERSION_PAUSE_DIRECTION,
    RESPONSE_SCHEMA_PAUSE_DIRECTION,
)
from otio_app.discovery_v2.domain.visual_edit import (
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    PROMPT_VERSION_HUMANITY_REVIEW,
    PROMPT_VERSION_VISUAL_EDIT_PLAN,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_HUMANITY_REVIEW,
    RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    TEXT_REQUEST_KIND_HUMANITY_REVIEW,
    TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    VISUAL_EDIT_MODEL_IDENTIFIER,
)


class FakeTextTransientError(RuntimeError):
    """Synthetic retryable failure used by gateway retry tests."""

    def __init__(self, code: str = "timeout") -> None:
        super().__init__(code)
        self.code = code


FakeTextHook = Callable[[TextGatewayRequest], dict | str | Exception | None]
_TEST_HOOK: FakeTextHook | None = None


def set_fake_text_test_hook(hook: FakeTextHook | None) -> None:
    global _TEST_HOOK
    _TEST_HOOK = hook


def reset_fake_text_test_hook() -> None:
    set_fake_text_test_hook(None)


class FakeTextAdapter:
    """Offline adapter returning untrusted payloads for gateway validation."""

    def generate(self, request: TextGatewayRequest) -> dict:
        if _TEST_HOOK is not None:
            hooked = _TEST_HOOK(request)
            if isinstance(hooked, Exception):
                raise hooked
            if hooked is not None:
                return hooked  # type: ignore[return-value]

        forced = _forced_error_from_request(request)
        if forced == "timeout":
            raise FakeTextTransientError("timeout")
        if forced == "rate_limit":
            raise FakeTextTransientError("rate_limited")

        if request.request_kind == "narrative":
            payload = self._narrative(request)
        elif request.request_kind in {"script", "structure"}:
            payload = self._script_or_structure(request)
        elif request.request_kind == "coverage":
            payload = self._coverage(request)
        elif request.request_kind == "pause_direction":
            payload = self._pause_direction(request)
        elif request.request_kind == TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN:
            payload = self._visual_edit_plan(request)
        elif request.request_kind == TEXT_REQUEST_KIND_HUMANITY_REVIEW:
            payload = self._humanity_review(request)
        elif request.request_kind == TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL:
            payload = self._editorial_repair_proposal(request)
        else:  # pragma: no cover - Pydantic guards this.
            payload = {}

        if forced == "invalid_json":
            return "not-json"  # type: ignore[return-value]
        if forced == "extra_field":
            payload["unexpected_extra"] = True
        if forced == "schema_error":
            if "script" in payload:
                payload["script"].pop("script_id", None)
            elif "narrative_plan" in payload:
                payload["hooks"] = payload["hooks"][:2]
            elif "coverage_audit" in payload:
                payload["coverage_audit"]["schema_version"] = "wrong"
        if forced == "bad_refs":
            if "script" in payload and payload["visual_beats"]:
                payload["visual_beats"][0]["sentence_ids"] = ["missing-sentence"]
            if "coverage_audit" in payload and payload["coverage_audit"]["results"]:
                payload["coverage_audit"]["results"][0]["visual_intent_id"] = "missing-intent"
            if "directions" in payload and payload["directions"]:
                payload["directions"][0]["sentence_id"] = "missing-sentence"
        if forced == "too_many_candidates" and "coverage_audit" in payload:
            for result in payload["coverage_audit"]["results"]:
                result["candidate_asset_ids"] = [f"asset-{i}" for i in range(6)]
        return payload

    def _narrative(self, request: TextGatewayRequest) -> dict:
        brief = request.project_brief
        if brief is None:
            topic = "Discovery V2"
            language = "de"
            brief_id = "missing-brief"
            brief_version = 1
        else:
            topic = brief.topic
            language = brief.language
            brief_id = brief.project_brief_id
            brief_version = brief.brief_version
        plan_id = _id("narrative", request.project_id, brief_id, request.input_fingerprint)
        observation_ids = [obs.observation_id for obs in request.observations]
        plan = {
            "schema_version": RESPONSE_SCHEMA_NARRATIVE,
            "narrative_plan_id": plan_id,
            "project_id": request.project_id,
            "project_brief_id": brief_id,
            "brief_version": brief_version,
            "central_question": f"Was macht {topic} fuer das Publikum relevant?",
            "editorial_thesis": f"{topic} wird aus lokaler Evidenz als nachvollziehbare Geschichte aufgebaut.",
            "hook_strategy": "Konkrete Beobachtung zuerst, dann Kontext und Unsicherheit offenlegen.",
            "narrative_roles": ["context", "local evidence", "user decision"],
            "arc": "Hook, Einordnung, lokale Beispiele, offene Entscheidung.",
            "transition_logic": "Jede These fuehrt zu einer sichtbaren lokalen Beobachtung.",
            "ending_function": "Nutzer prueft offene Punkte vor Phase 10.",
            "uncertainties": ["FakeText ist kein semantisches Modell.", f"language={language}"],
            "input_observation_ids": observation_ids,
            "input_observation_fingerprint": request.input_fingerprint,
            "prompt_version": request.prompt_version,
            "gateway_version": request.gateway_version,
            "model_identifier": request.model_identifier,
            "provider": request.provider,
            "status": NarrativePlanStatus.ACTIVE.value,
            "created_at": _now(),
        }
        hooks = []
        hook_specs = [
            ("question", "Warum dieser Moment jetzt zaehlt"),
            ("contrast", "Das sichtbare Detail gegen die offene Frage"),
            ("decision", "Die Entscheidung, die noch nicht automatisiert ist"),
        ]
        for idx, (kind, effect) in enumerate(hook_specs, start=1):
            hook_id = _id("hook", plan_id, str(idx))
            refs = observation_ids[:idx] if observation_ids else []
            hooks.append(
                {
                    "schema_version": RESPONSE_SCHEMA_NARRATIVE,
                    "hook_id": hook_id,
                    "narrative_plan_id": plan_id,
                    "hook_text": f"Hook {idx}: {topic} beginnt mit einer lokalen Beobachtung.",
                    "hook_type": kind,
                    "intended_effect": effect,
                    "risks": ["Kann ohne Nutzerpruefung zu allgemein wirken."],
                    "local_evidence_refs": refs,
                    "user_status": HookUserStatus.PROPOSED.value,
                    "created_at": _now(),
                }
            )
        return {"narrative_plan": plan, "hooks": hooks}

    def _script_or_structure(self, request: TextGatewayRequest) -> dict:
        if request.script is None:
            script_id = _id("script", request.project_id, request.input_fingerprint)
            version = 1
            language = request.project_brief.language if request.project_brief else "de"
            narrative_id = (
                request.narrative_plan.narrative_plan_id
                if request.narrative_plan
                else "missing-narrative"
            )
            brief_id = request.project_brief.project_brief_id if request.project_brief else "missing-brief"
            brief_version = request.project_brief.brief_version if request.project_brief else 1
            selected_hook = request.selected_hook_id
            topic = request.project_brief.topic if request.project_brief else "Discovery V2"
            hook_text = next(
                (hook.hook_text for hook in request.hooks if hook.hook_id == selected_hook),
                "Ein lokaler Einstieg",
            )
            sentence_texts = [
                hook_text,
                f"{topic} wird mit akzeptierten lokalen Beobachtungen eingeordnet.",
                "Einige Aussagen bleiben bewusst unsicher und brauchen Nutzerbestaetigung.",
                "Die visuelle Umsetzung trennt Satz, Beat und Intent.",
            ]
            full_text = " ".join(sentence_texts)
            status = ScriptDraftStatus.REVIEW_REQUESTED.value
            source_kind = ScriptSourceKind.LLM.value
            supersedes = None
        else:
            script_id = request.script.script_id
            version = request.script.script_version
            language = request.script.language
            narrative_id = request.script.narrative_plan_id
            brief_id = request.script.project_brief_id
            brief_version = request.script.brief_version
            selected_hook = request.script.selected_hook_id
            full_text = request.script.full_text
            sentence_texts = _split_sentences(full_text)
            status = request.script.status.value
            source_kind = request.script.source_kind.value
            supersedes = request.script.supersedes_script_id

        sentence_ids = [_id("sentence", script_id, str(i)) for i in range(len(sentence_texts))]
        script = {
            "schema_version": RESPONSE_SCHEMA_SCRIPT,
            "script_id": script_id,
            "script_version": version,
            "project_id": request.project_id,
            "language": language,
            "full_text": full_text,
            "sentence_order": sentence_ids,
            "narrative_plan_id": narrative_id,
            "selected_hook_id": selected_hook,
            "project_brief_id": brief_id,
            "brief_version": brief_version,
            "prompt_version": request.prompt_version,
            "gateway_version": request.gateway_version,
            "model_identifier": request.model_identifier,
            "provider": request.provider,
            "source_kind": source_kind,
            "supersedes_script_id": supersedes,
            "content_sha256": compute_text_sha256(full_text),
            "status": status,
            "created_at": _now(),
        }
        claims = []
        sentences = []
        for idx, text in enumerate(sentence_texts):
            claim_id = _id("claim", script_id, str(idx))
            claim_status = "user_confirmation_required" if idx % 2 == 0 else "uncertain"
            claims.append(
                {
                    "claim_id": claim_id,
                    "script_id": script_id,
                    "statement": text,
                    "claim_type": "editorial_statement",
                    "confidence": 0.45,
                    "evidence_refs": [
                        {"kind": "observation", "id": obs.observation_id}
                        for obs in request.observations[:1]
                    ],
                    "user_note": "FakeText: visual observations do not prove factual claims.",
                    "status": claim_status,
                }
            )
            sentences.append(
                {
                    "sentence_id": sentence_ids[idx],
                    "script_id": script_id,
                    "ordinal": idx,
                    "text": text,
                    "narrative_function": "hook" if idx == 0 else "context",
                    "claim_ids": [claim_id],
                    "visual_beat_ids": [],
                }
            )
        beats = self._beats(script_id, sentence_ids)
        beat_by_sentence: dict[str, list[str]] = {}
        for beat in beats:
            for sid in beat["sentence_ids"]:
                beat_by_sentence.setdefault(sid, []).append(beat["visual_beat_id"])
        for sentence in sentences:
            sentence["visual_beat_ids"] = beat_by_sentence.get(sentence["sentence_id"], [])
        intents = []
        for idx, beat in enumerate(beats, start=1):
            intents.append(
                {
                    "visual_intent_id": _id("intent", beat["visual_beat_id"]),
                    "visual_beat_id": beat["visual_beat_id"],
                    "desired_motif": "Lokales, akzeptiertes Bildmotiv",
                    "action": "zeigt den redaktionellen Punkt ohne Stock-Suche",
                    "setting": "lokale Projektbeobachtung",
                    "geographic_requirements": None,
                    "authenticity_requirements": ["accepted_observation_required"],
                    "allowed_media_kinds": ["image", "video"],
                    "priority": idx,
                }
            )
        return {
            "script": script,
            "sentences": sentences,
            "claims": claims,
            "visual_beats": beats,
            "visual_intents": intents,
        }

    def _beats(self, script_id: str, sentence_ids: list[str]) -> list[dict]:
        if len(sentence_ids) >= 3:
            groups = [sentence_ids[:2], sentence_ids[1:3], sentence_ids[3:] or sentence_ids[-1:]]
        else:
            groups = [[sid] for sid in sentence_ids]
        beats = []
        for idx, group in enumerate(groups):
            beat_id = _id("beat", script_id, str(idx))
            beats.append(
                {
                    "visual_beat_id": beat_id,
                    "script_id": script_id,
                    "function": "editorial_section",
                    "description": f"Visual Beat {idx + 1} fuer {len(group)} Satz/Saetze.",
                    "sentence_ids": group,
                    "rhythm_function": "orientation" if idx == 0 else "development",
                    "continuity_requirements": ["no_source_group_as_chapter"],
                    "intended_duration_hint_seconds": 4.0 + idx,
                }
            )
        return beats

    def _coverage(self, request: TextGatewayRequest) -> dict:
        script = request.script
        if script is None:
            script_id = "missing-script"
            script_version = 1
            brief_version = 1
            narrative_id = "missing-narrative"
        else:
            script_id = script.script_id
            script_version = script.script_version
            brief_version = script.brief_version
            narrative_id = script.narrative_plan_id
        # Include run_id so repeated coverage runs with identical inputs do not
        # collide on coverage_audits.coverage_audit_id (UNIQUE).
        audit_id = _id(
            "coverage",
            request.project_id,
            script_id,
            request.input_fingerprint,
            request.run_id,
        )
        candidate_asset_ids = sorted(set(request.candidate_asset_ids))[:5]
        accepted_ids = [obs.observation_id for obs in request.observations]
        results = []
        for intent in request.visual_intents:
            if candidate_asset_ids:
                status = CoverageStatus.PARTIALLY_COVERED.value
                action = "Nutzerentscheidung fuer lokale Kandidaten; Phase 10 kann Luecken eskalieren."
                missing = ["exact_match_not_verified"]
            else:
                status = CoverageStatus.NOT_COVERED.value
                action = "Phase 10 kann lokale Ergaenzung pruefen; keine Stock-Suche in Phase 9."
                missing = ["local_asset_candidate"]
            results.append(
                {
                    "visual_intent_id": intent.visual_intent_id,
                    "coverage_status": status,
                    "candidate_asset_ids": candidate_asset_ids,
                    "accepted_observation_ids": accepted_ids[:5],
                    "rationale": "FakeText bewertet nur lokale strukturierte Eingaben.",
                    "confidence": 0.6 if candidate_asset_ids else 0.2,
                    "missing_properties": missing,
                    "recommended_next_action": action,
                }
            )
        return {
            "coverage_audit": {
                "schema_version": RESPONSE_SCHEMA_COVERAGE,
                "coverage_audit_id": audit_id,
                "project_id": request.project_id,
                "script_id": script_id,
                "script_version": script_version,
                "brief_version": brief_version,
                "narrative_plan_id": narrative_id,
                "input_observation_fingerprint": request.input_fingerprint,
                "status": CoverageAuditStatus.COMPLETED.value,
                "created_at": _now(),
                "prompt_version": request.prompt_version,
                "gateway_version": request.gateway_version,
                "model_identifier": request.model_identifier,
                "provider": request.provider,
                "results": results,
            }
        }

    def _pause_direction(self, request: TextGatewayRequest) -> dict:
        plan_id = _id("pause-plan", request.project_id, request.run_id, request.input_fingerprint)
        directions = []
        ordered_sentences = sorted(request.sentences, key=lambda item: item.ordinal)
        segment_by_sentence = {
            str(item.get("sentence_id")): str(item.get("segment_id"))
            for item in request.pause_voice_segments
        }
        if ordered_sentences:
            first = ordered_sentences[0]
            directions.append(
                _pause_direction_payload(
                    plan_id,
                    "timeline-start",
                    len(directions),
                    PausePositionKind.TIMELINE_START.value,
                    PauseFunction.COLD_OPEN.value,
                    0.0,
                    0.35,
                    3.0,
                    PauseHardness.SOFT.value,
                    "Kurzer lokaler Fake-Cold-Open vor dem ersten Satz.",
                    sentence_id=None,
                    segment_id=None,
                    anchor_ordinal=0,
                )
            )
            directions.append(
                _pause_direction_payload(
                    plan_id,
                    first.sentence_id,
                    len(directions),
                    PausePositionKind.AFTER_SENTENCE.value,
                    PauseFunction.HOOK_BREATH.value if first.ordinal == 0 else PauseFunction.SENTENCE_TRANSITION.value,
                    0.15,
                    0.25,
                    2.5,
                    PauseHardness.SOFT.value,
                    "Fake-Pause fuer verstaendliche Satztrennung.",
                    sentence_id=first.sentence_id,
                    segment_id=segment_by_sentence.get(first.sentence_id),
                    anchor_ordinal=first.ordinal,
                )
            )
        for previous, current in zip(ordered_sentences, ordered_sentences[1:]):
            function = (
                PauseFunction.SECTION_TRANSITION.value
                if current.narrative_function != previous.narrative_function
                else PauseFunction.SENTENCE_TRANSITION.value
            )
            directions.append(
                _pause_direction_payload(
                    plan_id,
                    f"{previous.sentence_id}:{current.sentence_id}",
                    len(directions),
                    PausePositionKind.BETWEEN_SENTENCES.value,
                    function,
                    0.15,
                    0.2,
                    2.5,
                    PauseHardness.SOFT.value,
                    "Deterministische Fake-Pausenregie zwischen Saetzen.",
                    sentence_id=current.sentence_id,
                    segment_id=segment_by_sentence.get(current.sentence_id),
                    anchor_ordinal=current.ordinal,
                )
            )
        if ordered_sentences:
            last = ordered_sentences[-1]
            directions.append(
                _pause_direction_payload(
                    plan_id,
                    "timeline-end",
                    len(directions),
                    PausePositionKind.TIMELINE_END.value,
                    PauseFunction.CLOSING_HOLD.value,
                    0.0,
                    0.45,
                    5.0,
                    PauseHardness.SOFT.value,
                    "Kurzer Schluss-Hold fuer Review ohne Visual Edit Plan.",
                    sentence_id=last.sentence_id,
                    segment_id=segment_by_sentence.get(last.sentence_id),
                    anchor_ordinal=last.ordinal,
                )
            )
        return {
            "pause_plan": {
                "pause_plan_id": plan_id,
                "project_id": request.project_id,
                "script_lock_id": request.selected_hook_id or "missing-lock",
                "voice_run_id": request.run_id,
                "prompt_version": PROMPT_VERSION_PAUSE_DIRECTION,
                "model_identifier": request.model_identifier,
                "gateway_version": request.gateway_version,
                "response_schema_version": RESPONSE_SCHEMA_PAUSE_DIRECTION,
                "provider": request.provider,
                "input_fingerprint": request.input_fingerprint,
                "global_notes": [
                    "FakeText plant Pause-Funktionen, keine Frames.",
                    f"scope={NARRATION_RUN_SCOPE_PAUSE}",
                ],
                "status": "completed",
                "created_at": _now(),
            },
            "directions": directions,
        }

    def _visual_edit_plan(self, request: TextGatewayRequest) -> dict:
        inputs = request.visual_edit_input
        timeline = inputs.get("narration_timeline", {})
        entries = timeline.get("entries", []) if isinstance(timeline, dict) else []
        entry_ids = [str(item.get("entry_id")) for item in entries if isinstance(item, dict)]
        sentences = sorted(request.sentences, key=lambda item: item.ordinal)
        sentence_ids = [sentence.sentence_id for sentence in sentences]
        beat_ids = [beat.visual_beat_id for beat in request.visual_beats]
        intent_ids = [intent.visual_intent_id for intent in request.visual_intents]
        raw_candidates = inputs.get("candidates", [])
        raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
        # Stable editorial order: asset_id then observation_id (equal ranks stay deterministic).
        candidates = sorted(
            [item for item in raw_candidates if isinstance(item, dict)],
            key=lambda item: (str(item.get("asset_id") or ""), str(item.get("observation_id") or "")),
        )
        next_plan_version = int(inputs.get("next_plan_version", 1))
        plan_id = _id(
            "visual-edit-plan",
            request.project_id,
            request.input_fingerprint,
            str(next_plan_version),
        )
        total = float(timeline.get("total_duration_seconds", 0.0)) if isinstance(timeline, dict) else 0.0
        if total <= 0:
            total = 12.0
        shot_count = 3 if len(candidates) <= 1 else (6 if len(sentence_ids) >= 3 else max(3, len(sentence_ids) + 2))
        weights = [1.0, 1.65, 1.2, 2.1, 1.35, 1.9][:shot_count]
        functions = ["hook", "detail", "bridge", "establish", "hold", "closing"][:shot_count]
        biases = ["beginning", "middle", "end", "middle", "beginning", "end"][:shot_count]

        def pick(values: list[str], start: int, width: int = 1) -> list[str]:
            if not values:
                return []
            return values[start : start + width] or values[-1:]

        def ranked_for_shot(shot_index: int) -> list[dict]:
            if not candidates:
                return []
            # Rotate so equal-rank fixtures yield Shot1→A … Shot6→F deterministically.
            start = shot_index % len(candidates)
            ordered = candidates[start:] + candidates[:start]
            refs = []
            for item in ordered:
                tech_shots = item.get("technical_shots", [])
                tech_shots = tech_shots if isinstance(tech_shots, list) else []
                first_tech = (
                    tech_shots[0].get("technical_shot_id")
                    if tech_shots and isinstance(tech_shots[0], dict)
                    else None
                )
                refs.append(
                    {
                        "asset_id": item.get("asset_id"),
                        "working_media_id": item.get("working_media_id"),
                        "observation_id": item.get("observation_id"),
                        "technical_shot_id": first_tech,
                    }
                )
            return refs

        shots = []
        for idx in range(shot_count):
            shot_sentence_ids: list[str]
            if idx == 0 and sentence_ids:
                shot_sentence_ids = sentence_ids[:1]
            elif idx == 1 and sentence_ids:
                shot_sentence_ids = sentence_ids[:1]
            elif idx == 2 and len(sentence_ids) >= 2:
                shot_sentence_ids = sentence_ids[:2]
            elif idx == 3 and len(sentence_ids) >= 3:
                shot_sentence_ids = sentence_ids[1:3]
            else:
                shot_sentence_ids = pick(sentence_ids, min(idx, max(0, len(sentence_ids) - 1)))
            shot_id = _id("visual-edit-shot", plan_id, str(idx))
            ranked = ranked_for_shot(idx)
            preferred = ranked[0] if ranked else {}
            preferred_full = next(
                (
                    item
                    for item in candidates
                    if item.get("asset_id") == preferred.get("asset_id")
                    and item.get("working_media_id") == preferred.get("working_media_id")
                    and item.get("observation_id") == preferred.get("observation_id")
                ),
                {},
            )
            media_kind = str(preferred_full.get("media_kind", "image"))
            strategy = (
                "local_video"
                if media_kind == "video" and preferred.get("technical_shot_id")
                else "local_photo"
            )
            shots.append(
                {
                    "shot_id": shot_id,
                    "ordinal": idx,
                    "shot_function": functions[idx],
                    "duration_weight": weights[idx],
                    "narration_entry_ids": pick(entry_ids, min(idx, max(0, len(entry_ids) - 1)), 2 if idx == 2 else 1),
                    "sentence_ids": shot_sentence_ids,
                    "visual_beat_ids": pick(beat_ids, idx % max(1, len(beat_ids))),
                    "visual_intent_ids": pick(intent_ids, idx % max(1, len(intent_ids))),
                    "media_strategy": strategy,
                    "candidate_asset_id": preferred.get("asset_id"),
                    "candidate_working_media_id": preferred.get("working_media_id"),
                    "candidate_technical_shot_id": (
                        preferred.get("technical_shot_id") if strategy == "local_video" else None
                    ),
                    "candidate_observation_id": preferred.get("observation_id"),
                    "ranked_candidates": ranked,
                    "source_range_intent": {
                        "start_bias": biases[idx],
                        "desired_duration_seconds": None,
                        "action_hint": "FakeText chooses motif intent only; Python resolves exact source range.",
                        "continuity_hint": "avoid source-group chapter formation",
                    },
                    "transition_intent": "cut" if idx % 3 else "soft continuity",
                    "continuity_intent": "bridge over narration shape, not one-shot-per-sentence",
                    "rhythm_intent": "varied",
                    "selection_rationale": (
                        "Structured fake local candidate ranking; Python enforces E3/E4."
                    ),
                    "uncertainty_notes": [
                        "FakeText is not a semantic editor.",
                        "Source group is ignored as chapter structure.",
                    ],
                    "priority": idx,
                }
            )
        transitions = []
        for left, right in zip(shots, shots[1:]):
            idx = len(transitions)
            transitions.append(
                {
                    "transition_id": _id("visual-edit-transition", left["shot_id"], right["shot_id"]),
                    "from_shot_id": left["shot_id"],
                    "to_shot_id": right["shot_id"],
                    "editorial_function": "rhythm_cut" if idx % 2 else "visual_bridge",
                    "technical_type": "cut" if idx % 2 else "dissolve",
                    "desired_duration_seconds": 0.0 if idx % 2 else 0.35,
                }
            )
        return {
            "plan_id": plan_id,
            "project_id": request.project_id,
            "script_lock_id": str(inputs.get("script_lock_id", "missing-lock")),
            "narration_timeline_id": str(inputs.get("narration_timeline_id", "missing-timeline")),
            "input_fingerprint": request.input_fingerprint,
            "plan_version": next_plan_version,
            "model_id": VISUAL_EDIT_MODEL_IDENTIFIER,
            "prompt_version": PROMPT_VERSION_VISUAL_EDIT_PLAN,
            "schema_version": RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
            "expected_visual_duration_seconds": total,
            "accepted_risks": [
                {"risk_id": str(risk), "category": "accepted_unresolved", "rationale": "Visible from script lock."}
                for risk in inputs.get("accepted_open_risks", [])
            ],
            "shots": shots,
            "transitions": transitions,
            "created_at": _now(),
        }

    def _humanity_review(self, request: TextGatewayRequest) -> dict:
        inputs = request.visual_edit_input
        plan = inputs.get("plan", {}) if isinstance(inputs.get("plan", {}), dict) else {}
        signals = inputs.get("deterministic_signals", {})
        signals = signals if isinstance(signals, dict) else {}
        plan_id = str(plan.get("plan_id", "missing-plan"))
        review_id = _id("humanity-review", plan_id, request.input_fingerprint)
        findings = []
        boundary_ratio = float(signals.get("sentence_boundary_cut_ratio", 0.0) or 0.0)
        generic_ratio = float(signals.get("generic_stock_ratio", 0.0) or 0.0)
        similar_run = int(signals.get("max_similar_motif_run", 0) or 0)
        if boundary_ratio > 0.65:
            findings.append(
                _humanity_finding(
                    review_id,
                    "sentence-boundary",
                    None,
                    "sentence_boundary_cut_risk",
                    "blocking" if boundary_ratio > 0.85 else "warning",
                    "Viele Schnitte liegen exakt auf Satzgrenzen; Nutzer soll Mechanik pruefen.",
                )
            )
        if generic_ratio >= 0.40:
            findings.append(
                _humanity_finding(
                    review_id,
                    "generic-stock",
                    None,
                    "generic_stock_risk",
                    "blocking" if generic_ratio >= 0.60 else "warning",
                    "Lokale Detailwirkung koennte zu generisch sein.",
                )
            )
        if similar_run >= 3:
            findings.append(
                _humanity_finding(
                    review_id,
                    "similar-motif",
                    None,
                    "similar_motif_sequence",
                    "blocking" if similar_run >= 4 else "warning",
                    "Mehrere aufeinanderfolgende Shots nutzen ein aehnliches Motiv.",
                )
            )
        if signals.get("duration_variance_warning"):
            findings.append(
                _humanity_finding(
                    review_id,
                    "duration-variance",
                    None,
                    "shot_duration_variance",
                    "warning",
                    "Shotdauern wirken zu gleichfoermig.",
                )
            )
        overall = "blocked" if any(item["severity"] == "blocking" for item in findings) else "pass_with_risks"
        return {
            "review_id": review_id,
            "visual_edit_plan_id": plan_id,
            "review_version": int(inputs.get("next_review_version", 1)),
            "input_fingerprint": request.input_fingerprint,
            "overall_judgment": overall,
            "deterministic_signals": signals,
            "findings": findings,
            "created_at": _now(),
        }

    def _editorial_repair_proposal(self, request: TextGatewayRequest) -> dict:
        inputs = request.visual_edit_input
        plan = inputs.get("plan", {}) if isinstance(inputs.get("plan", {}), dict) else {}
        plan_id = str(plan.get("plan_id", "missing-plan"))
        shots = inputs.get("shots", [])
        affected = []
        if isinstance(shots, list) and shots:
            first = shots[0]
            if isinstance(first, dict):
                affected = [str(first.get("shot_id"))]
        proposal = {
            "proposal_id": _id("repair-proposal", plan_id, request.input_fingerprint),
            "plan_id": plan_id,
            "humanity_review_id": inputs.get("humanity_review_id"),
            "feasibility_report_id": inputs.get("feasibility_report_id"),
            "source": "editorial_fake_llm",
            "repair_type": "vary_first_local_motif",
            "affected_ids": affected or [plan_id],
            "description": "Fake proposal: vary the first motif or transition after user selection.",
            "expected_effect": "Reduces mechanical rhythm without auto-export or OTIO.",
            "user_status": "proposed",
            "version": 1,
        }
        return {
            "plan_id": plan_id,
            "input_fingerprint": request.input_fingerprint,
            "proposals": [proposal],
        }


def _forced_error_from_request(request: TextGatewayRequest) -> str | None:
    fields = [
        request.prompt,
        request.project_brief.topic if request.project_brief else "",
        request.project_brief.user_notes if request.project_brief else "",
    ]
    text = " ".join(str(item or "").lower() for item in fields)
    mapping = {
        "fake_text_force_timeout": "timeout",
        "fake_text_force_rate_limit": "rate_limit",
        "fake_text_force_invalid_json": "invalid_json",
        "fake_text_force_extra_field": "extra_field",
        "fake_text_force_schema_error": "schema_error",
        "fake_text_force_bad_refs": "bad_refs",
        "fake_text_force_too_many_candidates": "too_many_candidates",
    }
    for needle, code in mapping.items():
        if needle in text:
            return code
    return None


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in text.replace("!", ".").replace("?", ".").split(".")]
    sentences = [part + "." for part in parts if part]
    return sentences or [text.strip() or "Leerer Nutzerentwurf."]


def _id(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "otio-discovery-v2-editorial:" + ":".join(parts)))


def _pause_direction_payload(
    plan_id: str,
    key: str,
    ordinal: int,
    position_kind: str,
    function: str,
    minimum: float,
    preferred: float,
    maximum: float,
    hardness: str,
    rationale: str,
    *,
    sentence_id: str | None,
    segment_id: str | None,
    anchor_ordinal: int | None,
) -> dict:
    return {
        "direction_id": _id("pause-direction", plan_id, key, function),
        "pause_plan_id": plan_id,
        "ordinal": ordinal,
        "position_kind": position_kind,
        "sentence_id": sentence_id,
        "segment_id": segment_id,
        "anchor_ordinal": anchor_ordinal,
        "function": function,
        "min_duration_intent_s": minimum,
        "preferred_duration_intent_s": preferred,
        "max_duration_intent_s": maximum,
        "hardness": hardness,
        "rationale": rationale,
        "uncertainty": PauseUncertainty.LOW.value,
    }


def _humanity_finding(
    review_id: str,
    key: str,
    shot_id: str | None,
    category: str,
    severity: str,
    rationale: str,
) -> dict:
    return {
        "finding_id": _id("humanity-finding", review_id, key),
        "review_id": review_id,
        "shot_id": shot_id,
        "plan_level": shot_id is None,
        "category": category,
        "severity": severity,
        "rationale": rationale,
        "evidence_refs": [key],
        "recommended_action": "Review or select a repair before Phase 13.",
        "user_status": "open",
    }


def _now() -> str:
    return datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()


__all__ = [
    "FakeTextAdapter",
    "FakeTextTransientError",
    "reset_fake_text_test_hook",
    "set_fake_text_test_hook",
]
