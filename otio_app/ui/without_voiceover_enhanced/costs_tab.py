"""Enhanced-Seite: echte LLM-Tokenkosten (interne Preisliste, Anzeige in €)."""

from __future__ import annotations

import streamlit as st

from otio_app.services.voiceover_generation.llm_cost_ledger import (
    COST_STAGE_ORDER,
    format_eur,
    iter_recent_cost_events,
    load_llm_costs_summary,
    stage_label_de,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_costs_page() -> None:
    st.header("Kosten")
    st.caption(
        "Interne Näherung aus abgerechneten Tokens × Preisliste — "
        "keine Anbieter-Rechnung. TTS, ElevenLabs Audio, Stock und ffmpeg "
        "sind nicht enthalten. Läufe vor diesem Stand fehlen."
    )
    project = get_enhanced_project()
    if project is None:
        return

    summary = load_llm_costs_summary(project)
    language = normalize_brief_language(getattr(project, "language", "") or "") or "?"
    call_count = int(summary.get("call_count") or 0)
    total_usd = float(summary.get("cost_usd") or 0.0)
    input_tokens = int(summary.get("input_tokens") or 0)
    output_tokens = int(summary.get("output_tokens") or 0)

    col_lang, col_total, col_calls, col_tokens = st.columns(4)
    col_lang.metric("Sprache", language)
    col_total.metric("Summe", format_eur(total_usd))
    col_calls.metric("LLM-Calls", f"{call_count}")
    col_tokens.metric("Tokens", f"{input_tokens + output_tokens:,}".replace(",", "."))

    if call_count <= 0 and total_usd <= 0:
        st.info(
            "Noch keine Tokenkosten für diese Sprache. "
            "Nach Brief, Skript, Cut, Funnel oder YouTube erscheinen hier die "
            "echten Call-Kosten."
        )
        return

    by_stage = summary.get("by_stage") if isinstance(summary.get("by_stage"), dict) else {}
    extra_stages = [
        key for key in by_stage if str(key) not in COST_STAGE_ORDER
    ]
    rows: list[dict[str, str | int]] = []
    for stage in list(COST_STAGE_ORDER) + extra_stages:
        bucket = by_stage.get(stage)
        if not isinstance(bucket, dict):
            continue
        stage_calls = int(bucket.get("call_count") or 0)
        stage_cost = float(bucket.get("cost_usd") or 0.0)
        if stage_calls <= 0 and stage_cost <= 0:
            continue
        rows.append(
            {
                "Schritt": stage_label_de(str(stage)),
                "Calls": stage_calls,
                "Input-Tokens": int(bucket.get("input_tokens") or 0),
                "Output-Tokens": int(bucket.get("output_tokens") or 0),
                "Kosten": format_eur(stage_cost),
            }
        )
    if rows:
        st.subheader("Nach Schritt")
        st.dataframe(rows, hide_index=True, use_container_width=True)

    events = iter_recent_cost_events(project, limit=40)
    if not events:
        return
    with st.expander("Letzte Calls", expanded=False):
        event_rows = []
        for event in events:
            event_rows.append(
                {
                    "Zeit": str(event.get("ts") or "")[:19].replace("T", " "),
                    "Schritt": stage_label_de(str(event.get("stage") or "")),
                    "Modell": str(event.get("model") or ""),
                    "Status": str(event.get("status") or ""),
                    "In": int(event.get("input_tokens") or 0),
                    "Out": int(event.get("output_tokens") or 0),
                    "Kosten": format_eur(float(event.get("cost_usd") or 0.0)),
                    "Ordner": str(event.get("folder_name") or ""),
                }
            )
        st.dataframe(event_rows, hide_index=True, use_container_width=True)
        unknown = sum(1 for event in events if event.get("price_unknown"))
        if unknown:
            st.caption(
                f"{unknown} Call(s) ohne bekannten Listenpreis — "
                "Standard-Näherung $3/$15 pro 1M Tokens."
            )
