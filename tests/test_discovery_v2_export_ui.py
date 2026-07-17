from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from otio_app.discovery_v2.ui import review_export_page


def test_smoke_h_ui_double_render_starts_no_approval_validation_or_export(monkeypatch) -> None:
    st = MagicMock()
    st.checkbox.return_value = False
    st.text_area.return_value = ""
    st.button.return_value = False
    monkeypatch.setattr(review_export_page, "st", st)
    monkeypatch.setattr(review_export_page, "active_discovery_project", lambda: SimpleNamespace(id="project-1"))
    view = SimpleNamespace(
        ok=True,
        message=None,
        preview=SimpleNamespace(
            ok=True,
            fingerprint="fp",
            blockers=[],
            context=SimpleNamespace(
                visual_bundle=SimpleNamespace(
                    plan=SimpleNamespace(plan_id="plan-1"),
                    shots=[],
                ),
                narration_timeline=SimpleNamespace(timeline_id="timeline-1"),
                humanity_bundle=SimpleNamespace(review=SimpleNamespace(review_id="humanity-1")),
                feasibility_bundle=SimpleNamespace(report=SimpleNamespace(report_id="feas-1")),
                visible_risks=[],
            ),
        ),
        current_approval=None,
        validation_report=None,
        export_run=None,
        artifact=None,
        reparse_report=None,
        active_export_run=None,
        can_approve=True,
        can_validate=False,
        can_export=False,
    )
    monkeypatch.setattr(review_export_page, "get_review_export_view", lambda project: view)
    create = MagicMock()
    validate = MagicMock()
    export = MagicMock()
    monkeypatch.setattr(review_export_page, "create_editorial_approval", create)
    monkeypatch.setattr(review_export_page, "start_export_validation_run", validate)
    monkeypatch.setattr(review_export_page, "start_otio_export_run", export)
    review_export_page.render_discovery_review_export_page()
    review_export_page.render_discovery_review_export_page()
    create.assert_not_called()
    validate.assert_not_called()
    export.assert_not_called()
