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
    ASSET_REUSE_MAX,
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    PROMPT_VERSION_HUMANITY_REVIEW,
    PROMPT_VERSION_VISUAL_EDIT_PLAN,
    REPAIR_EXPECTED_EFFECT_ASSET_REUSE_REDUCED,
    REPAIR_EXPECTED_EFFECT_SIMILAR_MOTIF_RUN_REDUCED,
    REPAIR_EXPECTED_EFFECT_SOURCE_RANGE_OVERLAP_REDUCED,
    REPAIR_OPERATION_SCHEMA_VERSION,
    REPAIR_PROPOSAL_OPS_SCHEMA_VERSION,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_HUMANITY_REVIEW,
    RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    TEXT_REQUEST_KIND_HUMANITY_REVIEW,
    TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    VISUAL_EDIT_MODEL_IDENTIFIER,
    compute_visual_edit_sha256,
    seconds_to_frame_nearest,
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
            # Structure runs must not preserve structure_pending once Fake emits
            # a complete structured payload (sentences/beats/intents).
            if request.request_kind == "structure":
                status = ScriptDraftStatus.REVIEW_REQUESTED.value
            else:
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
        return _build_editorial_repair_proposal_payload(request)


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


def _build_editorial_repair_proposal_payload(request: TextGatewayRequest) -> dict:
    inputs = request.visual_edit_input if isinstance(request.visual_edit_input, dict) else {}
    plan = inputs.get("plan", {}) if isinstance(inputs.get("plan", {}), dict) else {}
    plan_id = str(plan.get("plan_id", "missing-plan"))
    plan_fp = str(inputs.get("source_plan_fingerprint") or request.input_fingerprint)
    provider = str(inputs.get("provider") or request.provider or "fake")
    model = str(inputs.get("model_identifier") or request.model_identifier or VISUAL_EDIT_MODEL_IDENTIFIER)
    shots = [item for item in inputs.get("shots", []) if isinstance(item, dict)]
    assignments = [item for item in inputs.get("assignments", []) if isinstance(item, dict)]
    candidates = sorted(
        [item for item in inputs.get("candidates", []) if isinstance(item, dict)],
        key=lambda item: (str(item.get("asset_id") or ""), str(item.get("observation_id") or "")),
    )
    findings = [item for item in inputs.get("findings", []) if isinstance(item, dict)]
    issues = [item for item in inputs.get("feasibility_issues", []) if isinstance(item, dict)]
    fps = 25.0
    timeline = plan.get("narration_timeline") if isinstance(plan.get("narration_timeline"), dict) else {}
    if not timeline:
        # fps is only needed for frame rounding of proposed ranges
        fps = 25.0
    proposals: list[dict] = []
    artifacts: list[dict] = []

    motif_finding = next(
        (
            item
            for item in findings
            if item.get("category") == "similar_motif_sequence"
            and item.get("user_status", "open") == "open"
        ),
        None,
    )
    if motif_finding is not None:
        built = _fake_similar_motif_repair(
            request=request,
            plan_id=plan_id,
            plan_fp=plan_fp,
            provider=provider,
            model=model,
            shots=shots,
            assignments=assignments,
            candidates=candidates,
            finding=motif_finding,
            fps=fps,
        )
        if built is not None:
            proposals.append(built[0])
            artifacts.append(built[1])

    e3_issues = [
        item
        for item in issues
        if item.get("severity") == "blocking" and "E3" in str(item.get("technical_details") or "")
    ]
    if e3_issues:
        built = _fake_e3_repair(
            request=request,
            plan_id=plan_id,
            plan_fp=plan_fp,
            provider=provider,
            model=model,
            shots=shots,
            assignments=assignments,
            candidates=candidates,
            issues=e3_issues,
            fps=fps,
        )
        if built is not None:
            proposals.append(built[0])
            artifacts.append(built[1])

    e4_issues = [
        item
        for item in issues
        if item.get("severity") == "blocking" and "E4" in str(item.get("technical_details") or "")
    ]
    if e4_issues:
        built = _fake_e4_repair(
            request=request,
            plan_id=plan_id,
            plan_fp=plan_fp,
            provider=provider,
            model=model,
            assignments=assignments,
            candidates=candidates,
            issues=e4_issues,
            fps=fps,
        )
        if built is not None:
            proposals.append(built[0])
            artifacts.append(built[1])

    if not proposals:
        # Visible but non-executable generic proposal when no actionable blocker repair exists.
        affected = [str(shots[0]["shot_id"])] if shots and shots[0].get("shot_id") else [plan_id]
        proposal_id = _id("repair-proposal", plan_id, request.input_fingerprint, "generic")
        proposals.append(
            {
                "proposal_id": proposal_id,
                "plan_id": plan_id,
                "humanity_review_id": inputs.get("humanity_review_id"),
                "feasibility_report_id": inputs.get("feasibility_report_id"),
                "source": "editorial_fake_llm",
                "repair_type": "vary_first_local_motif",
                "affected_ids": affected,
                "description": "Generischer Hinweis ohne ausfuehrbare Operation.",
                "expected_effect": "Nicht anwendbar ohne konkrete Alternative.",
                "user_status": "proposed",
                "version": 1,
            }
        )
        artifacts.append(
            _ops_artifact(
                proposal_id=proposal_id,
                plan_id=plan_id,
                plan_fp=plan_fp,
                proposal_type="vary_first_local_motif",
                provider=provider,
                model=model,
                input_fingerprint=request.input_fingerprint,
                source_review_id=inputs.get("humanity_review_id"),
                source_report_id=inputs.get("feasibility_report_id"),
                source_issue_ids=[],
                operations=[],
            )
        )

    return {
        "plan_id": plan_id,
        "input_fingerprint": request.input_fingerprint,
        "proposals": proposals,
        "executable_artifacts": artifacts,
    }


def _fake_similar_motif_repair(
    *,
    request: TextGatewayRequest,
    plan_id: str,
    plan_fp: str,
    provider: str,
    model: str,
    shots: list[dict],
    assignments: list[dict],
    candidates: list[dict],
    finding: dict,
    fps: float,
) -> tuple[dict, dict] | None:
    candidate_by_obs = {
        str(item.get("observation_id")): item
        for item in candidates
        if item.get("observation_id")
    }
    ordered = _assignments_in_shot_order(shots, assignments)
    if not ordered:
        return None
    run_start = 0
    best = (0, 0)  # start, length
    previous = None
    current_start = 0
    current_len = 0
    for index, assignment in enumerate(ordered):
        obs = candidate_by_obs.get(str(assignment.get("visual_observation_id")))
        motif = None if obs is None else obs.get("motif_hash")
        if motif is not None and motif == previous:
            current_len += 1
        else:
            current_start = index
            current_len = 1
            previous = motif
        if current_len > best[1]:
            best = (current_start, current_len)
            run_start = current_start
    target_assignment = ordered[run_start]
    source_asset = str(target_assignment.get("asset_id") or "")
    source_motif = None
    source_obs = candidate_by_obs.get(str(target_assignment.get("visual_observation_id")))
    if source_obs is not None:
        source_motif = source_obs.get("motif_hash")
    reuse = _asset_reuse_counts(assignments)
    alt = _pick_alternate_candidate(
        candidates=candidates,
        source_asset_id=source_asset,
        source_motif=source_motif,
        reuse_counts=reuse,
        require_different_motif=True,
        desired_duration=_assignment_desired_duration(target_assignment, shots),
        occupied=_occupied_ranges_except(assignments, str(target_assignment.get("assignment_id"))),
        fps=fps,
    )
    proposal_id = _id(
        "repair-proposal",
        plan_id,
        request.input_fingerprint,
        "similar-motif",
        str(finding.get("finding_id")),
    )
    if alt is None:
        proposal = {
            "proposal_id": proposal_id,
            "plan_id": plan_id,
            "humanity_review_id": finding.get("review_id") or request.visual_edit_input.get("humanity_review_id"),
            "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
            "source": "editorial_fake_llm",
            "repair_type": "additional_coverage_required",
            "affected_ids": [
                str(target_assignment.get("shot_id") or ""),
                str(target_assignment.get("assignment_id") or ""),
            ],
            "description": "Keine gueltige Motivalternative in der Kandidatenmenge.",
            "expected_effect": "additional_coverage_required",
            "user_status": "proposed",
            "version": 1,
        }
        artifact = _ops_artifact(
            proposal_id=proposal_id,
            plan_id=plan_id,
            plan_fp=plan_fp,
            proposal_type="additional_coverage_required",
            provider=provider,
            model=model,
            input_fingerprint=request.input_fingerprint,
            source_review_id=finding.get("review_id") or request.visual_edit_input.get("humanity_review_id"),
            source_report_id=request.visual_edit_input.get("feasibility_report_id"),
            source_issue_ids=[str(finding.get("finding_id"))],
            operations=[],
        )
        return proposal, artifact
    alt_candidate, target_range = alt
    operation = _replace_asset_operation(
        plan_id=plan_id,
        plan_fp=plan_fp,
        assignment=target_assignment,
        target_candidate=alt_candidate,
        target_range=target_range,
        addressed_issue_ids=[str(finding.get("finding_id"))],
        expected_effects=[REPAIR_EXPECTED_EFFECT_SIMILAR_MOTIF_RUN_REDUCED],
    )
    proposal = {
        "proposal_id": proposal_id,
        "plan_id": plan_id,
        "humanity_review_id": finding.get("review_id") or request.visual_edit_input.get("humanity_review_id"),
        "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
        "source": "editorial_fake_llm",
        "repair_type": "replace_assignment_asset",
        "affected_ids": [
            str(target_assignment.get("assignment_id") or ""),
            str(target_assignment.get("shot_id") or ""),
            source_asset,
            str(alt_candidate.get("asset_id") or ""),
        ],
        "description": (
            f"Fake editorial: Shot {target_assignment.get('shot_id')} von "
            f"{source_asset} auf {alt_candidate.get('asset_id')} umstellen."
        ),
        "expected_effect": "aehnliche Motivfolge wird reduziert",
        "user_status": "proposed",
        "version": 1,
    }
    artifact = _ops_artifact(
        proposal_id=proposal_id,
        plan_id=plan_id,
        plan_fp=plan_fp,
        proposal_type="replace_assignment_asset",
        provider=provider,
        model=model,
        input_fingerprint=request.input_fingerprint,
        source_review_id=finding.get("review_id") or request.visual_edit_input.get("humanity_review_id"),
        source_report_id=request.visual_edit_input.get("feasibility_report_id"),
        source_issue_ids=[str(finding.get("finding_id"))],
        operations=[operation],
    )
    return proposal, artifact


def _fake_e3_repair(
    *,
    request: TextGatewayRequest,
    plan_id: str,
    plan_fp: str,
    provider: str,
    model: str,
    shots: list[dict],
    assignments: list[dict],
    candidates: list[dict],
    issues: list[dict],
    fps: float,
) -> tuple[dict, dict] | None:
    reuse = _asset_reuse_counts(assignments)
    overused = sorted(
        [asset_id for asset_id, count in reuse.items() if count > ASSET_REUSE_MAX],
        key=lambda item: item,
    )
    if not overused:
        return None
    asset_id = overused[0]
    excess = reuse[asset_id] - ASSET_REUSE_MAX
    ordered = [
        item
        for item in _assignments_in_shot_order(shots, assignments)
        if str(item.get("asset_id")) == asset_id
    ]
    # Keep earliest assignments; repair the latest excess ones.
    to_repair = list(reversed(ordered))[:excess]
    operations: list[dict] = []
    working_reuse = dict(reuse)
    working_assignments = [dict(item) for item in assignments]
    for assignment in to_repair:
        occupied = _occupied_ranges_except(
            working_assignments, str(assignment.get("assignment_id"))
        )
        alt = _pick_alternate_candidate(
            candidates=candidates,
            source_asset_id=asset_id,
            source_motif=None,
            reuse_counts=working_reuse,
            require_different_motif=False,
            desired_duration=_assignment_desired_duration(assignment, shots),
            occupied=occupied,
            fps=fps,
        )
        if alt is None:
            continue
        alt_candidate, target_range = alt
        operations.append(
            _replace_asset_operation(
                plan_id=plan_id,
                plan_fp=plan_fp,
                assignment=assignment,
                target_candidate=alt_candidate,
                target_range=target_range,
                addressed_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
                expected_effects=[REPAIR_EXPECTED_EFFECT_ASSET_REUSE_REDUCED],
            )
        )
        target_asset = str(alt_candidate.get("asset_id"))
        working_reuse[asset_id] = working_reuse.get(asset_id, 1) - 1
        working_reuse[target_asset] = working_reuse.get(target_asset, 0) + 1
        for row in working_assignments:
            if row.get("assignment_id") == assignment.get("assignment_id"):
                row["asset_id"] = target_asset
                row["working_media_id"] = alt_candidate.get("working_media_id")
                row["visual_observation_id"] = alt_candidate.get("observation_id")
                row["technical_shot_id"] = target_range.get("technical_shot_id")
                row["technical_source_in_seconds"] = target_range.get("in_seconds")
                row["technical_source_out_seconds"] = target_range.get("out_seconds")
                break
    proposal_id = _id("repair-proposal", plan_id, request.input_fingerprint, "e3", asset_id)
    if not operations:
        proposal = {
            "proposal_id": proposal_id,
            "plan_id": plan_id,
            "humanity_review_id": request.visual_edit_input.get("humanity_review_id"),
            "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
            "source": "editorial_fake_llm",
            "repair_type": "additional_coverage_required",
            "affected_ids": [asset_id],
            "description": "Keine E3-konforme Ersatzassets in der Kandidatenmenge.",
            "expected_effect": "additional_coverage_required",
            "user_status": "proposed",
            "version": 1,
        }
        artifact = _ops_artifact(
            proposal_id=proposal_id,
            plan_id=plan_id,
            plan_fp=plan_fp,
            proposal_type="additional_coverage_required",
            provider=provider,
            model=model,
            input_fingerprint=request.input_fingerprint,
            source_review_id=request.visual_edit_input.get("humanity_review_id"),
            source_report_id=request.visual_edit_input.get("feasibility_report_id"),
            source_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
            operations=[],
        )
        return proposal, artifact
    proposal = {
        "proposal_id": proposal_id,
        "plan_id": plan_id,
        "humanity_review_id": request.visual_edit_input.get("humanity_review_id"),
        "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
        "source": "editorial_fake_llm",
        "repair_type": "replace_assignment_asset",
        "affected_ids": [str(op["source_assignment_id"]) for op in operations] + [asset_id],
        "description": f"Fake editorial: E3-Reuse fuer {asset_id} durch Ersatzassets senken.",
        "expected_effect": "Asset-Wiederverwendung wird reduziert",
        "user_status": "proposed",
        "version": 1,
    }
    artifact = _ops_artifact(
        proposal_id=proposal_id,
        plan_id=plan_id,
        plan_fp=plan_fp,
        proposal_type="replace_assignment_asset",
        provider=provider,
        model=model,
        input_fingerprint=request.input_fingerprint,
        source_review_id=request.visual_edit_input.get("humanity_review_id"),
        source_report_id=request.visual_edit_input.get("feasibility_report_id"),
        source_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
        operations=operations,
    )
    return proposal, artifact


def _fake_e4_repair(
    *,
    request: TextGatewayRequest,
    plan_id: str,
    plan_fp: str,
    provider: str,
    model: str,
    assignments: list[dict],
    candidates: list[dict],
    issues: list[dict],
    fps: float,
) -> tuple[dict, dict] | None:
    issue = issues[0]
    assignment_id = str(issue.get("assignment_id") or "")
    assignment = next(
        (item for item in assignments if str(item.get("assignment_id")) == assignment_id),
        None,
    )
    if assignment is None:
        # Fall back to first overlapping pair member mentioned in details.
        for item in assignments:
            if str(item.get("asset_id") or "") in str(issue.get("technical_details") or ""):
                assignment = item
                break
    if assignment is None:
        return None
    candidate = next(
        (
            item
            for item in candidates
            if str(item.get("asset_id")) == str(assignment.get("asset_id"))
        ),
        None,
    )
    if candidate is None:
        return None
    occupied_map = _occupied_ranges_except(assignments, str(assignment.get("assignment_id")))
    desired = float(assignment.get("duration_seconds") or 1.0)
    occ_key = f"{assignment.get('asset_id')}:{assignment.get('working_media_id')}"
    alt_range = _find_e4_range(
        candidate,
        desired=desired,
        occupied=occupied_map.get(occ_key, []),
        fps=fps,
    )
    proposal_id = _id(
        "repair-proposal",
        plan_id,
        request.input_fingerprint,
        "e4",
        str(assignment.get("assignment_id")),
    )
    before = {
        "working_media_id": str(assignment.get("working_media_id")),
        "observation_id": str(assignment.get("visual_observation_id")),
        "technical_shot_id": assignment.get("technical_shot_id"),
        "in_seconds": assignment.get("technical_source_in_seconds"),
        "out_seconds": assignment.get("technical_source_out_seconds"),
        "in_frame": assignment.get("technical_source_in_frame"),
        "out_frame": assignment.get("technical_source_out_frame"),
    }
    if alt_range is not None:
        operation = {
            "operation_version": REPAIR_OPERATION_SCHEMA_VERSION,
            "operation_id": _id("repair-op", plan_id, str(assignment.get("assignment_id")), "range"),
            "operation_type": "replace_assignment_source_range",
            "source_plan_id": plan_id,
            "source_plan_fingerprint": plan_fp,
            "source_assignment_id": str(assignment.get("assignment_id")),
            "source_shot_id": str(assignment.get("shot_id")),
            "asset_id": str(assignment.get("asset_id")),
            "source_range_before": before,
            "source_range_after": alt_range,
            "addressed_issue_ids": [str(item.get("issue_id")) for item in issues if item.get("issue_id")],
            "expected_effects": [REPAIR_EXPECTED_EFFECT_SOURCE_RANGE_OVERLAP_REDUCED],
            "operation_fingerprint": "",
        }
        operation["operation_fingerprint"] = compute_visual_edit_sha256(
            {key: value for key, value in operation.items() if key != "operation_fingerprint"}
        )
        proposal = {
            "proposal_id": proposal_id,
            "plan_id": plan_id,
            "humanity_review_id": request.visual_edit_input.get("humanity_review_id"),
            "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
            "source": "editorial_fake_llm",
            "repair_type": "replace_assignment_source_range",
            "affected_ids": [
                str(assignment.get("assignment_id")),
                str(assignment.get("shot_id")),
            ],
            "description": "Fake editorial: alternative Source-Range unter E4-Grenze.",
            "expected_effect": "Source-Range-Overlap wird reduziert",
            "user_status": "proposed",
            "version": 1,
        }
        artifact = _ops_artifact(
            proposal_id=proposal_id,
            plan_id=plan_id,
            plan_fp=plan_fp,
            proposal_type="replace_assignment_source_range",
            provider=provider,
            model=model,
            input_fingerprint=request.input_fingerprint,
            source_review_id=request.visual_edit_input.get("humanity_review_id"),
            source_report_id=request.visual_edit_input.get("feasibility_report_id"),
            source_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
            operations=[operation],
        )
        return proposal, artifact
    # No alternate range → try asset replacement.
    reuse = _asset_reuse_counts(assignments)
    alt = _pick_alternate_candidate(
        candidates=candidates,
        source_asset_id=str(assignment.get("asset_id")),
        source_motif=None,
        reuse_counts=reuse,
        require_different_motif=False,
        desired_duration=desired,
        occupied=_occupied_ranges_except(assignments, str(assignment.get("assignment_id"))),
        fps=fps,
    )
    if alt is None:
        return None
    alt_candidate, target_range = alt
    operation = _replace_asset_operation(
        plan_id=plan_id,
        plan_fp=plan_fp,
        assignment=assignment,
        target_candidate=alt_candidate,
        target_range=target_range,
        addressed_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
        expected_effects=[REPAIR_EXPECTED_EFFECT_SOURCE_RANGE_OVERLAP_REDUCED],
    )
    proposal = {
        "proposal_id": proposal_id,
        "plan_id": plan_id,
        "humanity_review_id": request.visual_edit_input.get("humanity_review_id"),
        "feasibility_report_id": request.visual_edit_input.get("feasibility_report_id"),
        "source": "editorial_fake_llm",
        "repair_type": "replace_assignment_asset",
        "affected_ids": [
            str(assignment.get("assignment_id")),
            str(assignment.get("shot_id")),
            str(alt_candidate.get("asset_id")),
        ],
        "description": "Fake editorial: E4 ohne freie Range → Asset-Ersatz.",
        "expected_effect": "Source-Range-Overlap wird reduziert",
        "user_status": "proposed",
        "version": 1,
    }
    artifact = _ops_artifact(
        proposal_id=proposal_id,
        plan_id=plan_id,
        plan_fp=plan_fp,
        proposal_type="replace_assignment_asset",
        provider=provider,
        model=model,
        input_fingerprint=request.input_fingerprint,
        source_review_id=request.visual_edit_input.get("humanity_review_id"),
        source_report_id=request.visual_edit_input.get("feasibility_report_id"),
        source_issue_ids=[str(item.get("issue_id")) for item in issues if item.get("issue_id")],
        operations=[operation],
    )
    return proposal, artifact


def _ops_artifact(
    *,
    proposal_id: str,
    plan_id: str,
    plan_fp: str,
    proposal_type: str,
    provider: str,
    model: str,
    input_fingerprint: str,
    source_review_id: object,
    source_report_id: object,
    source_issue_ids: list[str],
    operations: list[dict],
) -> dict:
    body = {
        "schema_version": REPAIR_PROPOSAL_OPS_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "source_plan_id": plan_id,
        "source_plan_fingerprint": plan_fp,
        "source_review_id": source_review_id,
        "source_report_id": source_report_id,
        "source_issue_ids": source_issue_ids,
        "proposal_type": proposal_type,
        "provider": provider,
        "model": model,
        "input_fingerprint": input_fingerprint,
        "created_at": _now(),
        "operations": operations,
        "artifact_fingerprint": "",
    }
    body["artifact_fingerprint"] = compute_visual_edit_sha256(
        {key: value for key, value in body.items() if key != "artifact_fingerprint"}
    )
    return body


def _replace_asset_operation(
    *,
    plan_id: str,
    plan_fp: str,
    assignment: dict,
    target_candidate: dict,
    target_range: dict,
    addressed_issue_ids: list[str],
    expected_effects: list[str],
) -> dict:
    operation = {
        "operation_version": REPAIR_OPERATION_SCHEMA_VERSION,
        "operation_id": _id(
            "repair-op",
            plan_id,
            str(assignment.get("assignment_id")),
            str(target_candidate.get("asset_id")),
        ),
        "operation_type": "replace_assignment_asset",
        "source_plan_id": plan_id,
        "source_plan_fingerprint": plan_fp,
        "source_assignment_id": str(assignment.get("assignment_id")),
        "source_shot_id": str(assignment.get("shot_id")),
        "source_asset_id": str(assignment.get("asset_id")),
        "target_asset_id": str(target_candidate.get("asset_id")),
        "target_source_range": target_range,
        "addressed_issue_ids": addressed_issue_ids,
        "expected_effects": expected_effects,
        "operation_fingerprint": "",
    }
    operation["operation_fingerprint"] = compute_visual_edit_sha256(
        {key: value for key, value in operation.items() if key != "operation_fingerprint"}
    )
    return operation


def _pick_alternate_candidate(
    *,
    candidates: list[dict],
    source_asset_id: str,
    source_motif: object,
    reuse_counts: dict[str, int],
    require_different_motif: bool,
    desired_duration: float,
    occupied: dict[str, list[tuple[float, float]]],
    fps: float,
) -> tuple[dict, dict] | None:
    for candidate in candidates:
        asset_id = str(candidate.get("asset_id") or "")
        if not asset_id or asset_id == source_asset_id:
            continue
        if reuse_counts.get(asset_id, 0) >= ASSET_REUSE_MAX:
            continue
        if require_different_motif and source_motif is not None:
            if candidate.get("motif_hash") == source_motif:
                continue
        occ_key = f"{asset_id}:{candidate.get('working_media_id')}"
        target_range = _find_e4_range(
            candidate,
            desired=desired_duration,
            occupied=occupied.get(occ_key, []),
            fps=fps,
        )
        if target_range is None and str(candidate.get("media_kind")) == "image":
            target_range = {
                "working_media_id": str(candidate.get("working_media_id")),
                "observation_id": str(candidate.get("observation_id")),
                "technical_shot_id": None,
                "in_seconds": None,
                "out_seconds": None,
                "in_frame": None,
                "out_frame": None,
            }
        if target_range is None:
            continue
        return candidate, target_range
    return None


def _find_e4_range(
    candidate: dict,
    *,
    desired: float,
    occupied: list[tuple[float, float]],
    fps: float,
) -> dict | None:
    if str(candidate.get("media_kind")) == "image":
        return {
            "working_media_id": str(candidate.get("working_media_id")),
            "observation_id": str(candidate.get("observation_id")),
            "technical_shot_id": None,
            "in_seconds": None,
            "out_seconds": None,
            "in_frame": None,
            "out_frame": None,
        }
    tech_shots = candidate.get("technical_shots", [])
    tech_shots = [item for item in tech_shots if isinstance(item, dict)] if isinstance(tech_shots, list) else []
    for tech in tech_shots:
        try:
            start = float(tech.get("start_seconds") or 0.0)
            end = float(tech.get("end_seconds") or 0.0)
            duration_desired = float(desired)
        except (TypeError, ValueError):
            continue
        available = end - start
        if available <= 0:
            continue
        duration = min(max(0.05, duration_desired), available)
        step = max(0.05, duration * (1.0 - SOURCE_RANGE_OVERLAP_RATIO_MAX))
        cursor = start
        while cursor + duration <= end + 1e-9:
            source_in = round(cursor, 6)
            source_out = round(min(end, source_in + duration), 6)
            in_frame = seconds_to_frame_nearest(source_in, fps)
            out_frame = max(in_frame + 1, seconds_to_frame_nearest(source_out, fps))
            rounded_in = in_frame / fps
            rounded_out = out_frame / fps
            if rounded_in < start - 1e-6 or rounded_out > end + 1e-6:
                cursor += step
                continue
            if _range_ok((rounded_in, rounded_out), occupied):
                return {
                    "working_media_id": str(candidate.get("working_media_id")),
                    "observation_id": str(candidate.get("observation_id")),
                    "technical_shot_id": str(tech.get("technical_shot_id")),
                    "in_seconds": round(rounded_in, 6),
                    "out_seconds": round(rounded_out, 6),
                    "in_frame": in_frame,
                    "out_frame": out_frame,
                }
            cursor += step
    return None


def _range_ok(candidate: tuple[float, float], occupied: list[tuple[float, float]]) -> bool:
    for existing in occupied:
        try:
            existing_range = (float(existing[0]), float(existing[1]))
        except (TypeError, ValueError, IndexError):
            continue
        overlap = max(
            0.0,
            min(candidate[1], existing_range[1]) - max(candidate[0], existing_range[0]),
        )
        shortest = min(candidate[1] - candidate[0], existing_range[1] - existing_range[0])
        ratio = 0.0 if shortest <= 0 else overlap / shortest
        if ratio >= SOURCE_RANGE_OVERLAP_RATIO_MAX:
            return False
    return True


def _assignments_in_shot_order(shots: list[dict], assignments: list[dict]) -> list[dict]:
    ordinal = {
        str(shot.get("shot_id")): int(shot.get("ordinal") or 0)
        for shot in shots
        if shot.get("shot_id")
    }
    return sorted(
        assignments,
        key=lambda item: (
            ordinal.get(str(item.get("shot_id")), 10**9),
            str(item.get("assignment_id") or ""),
        ),
    )


def _asset_reuse_counts(assignments: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in assignments:
        asset_id = str(item.get("asset_id") or "")
        if not asset_id:
            continue
        counts[asset_id] = counts.get(asset_id, 0) + 1
    return counts


def _occupied_ranges_except(
    assignments: list[dict],
    assignment_id: str,
) -> dict[str, list[tuple[float, float]]]:
    occupied: dict[str, list[tuple[float, float]]] = {}
    for item in assignments:
        if str(item.get("assignment_id")) == assignment_id:
            continue
        if item.get("technical_source_in_seconds") is None or item.get("technical_source_out_seconds") is None:
            continue
        key = f"{item.get('asset_id')}:{item.get('working_media_id')}"
        occupied.setdefault(key, []).append(
            (
                float(item["technical_source_in_seconds"]),
                float(item["technical_source_out_seconds"]),
            )
        )
    return occupied


def _assignment_desired_duration(assignment: dict, shots: list[dict]) -> float:
    shot = next(
        (item for item in shots if str(item.get("shot_id")) == str(assignment.get("shot_id"))),
        None,
    )
    if shot is not None and shot.get("duration_seconds") is not None:
        return float(shot["duration_seconds"])
    if assignment.get("duration_seconds") is not None:
        return float(assignment["duration_seconds"])
    return 1.0


def _now() -> str:
    return datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()


__all__ = [
    "FakeTextAdapter",
    "FakeTextTransientError",
    "reset_fake_text_test_hook",
    "set_fake_text_test_hook",
]
