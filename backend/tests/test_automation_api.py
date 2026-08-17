import json
from types import SimpleNamespace

from app.automation_api import build_automation_router


def test_router_exposes_required_paths():
    router = build_automation_router(
        current_user=lambda: SimpleNamespace(),
        admin_user=lambda: SimpleNamespace(),
    )
    paths = {route.path for route in router.routes}
    assert "/api/automation/overview" in paths
    assert "/api/automation/lanes" in paths
    assert "/api/automation/historical-audit" in paths
    assert "/api/automation/candidates" in paths
    assert "/api/automation/candidates/{candidate_id}/decision" in paths


def test_candidate_decision_payload_rejects_publish_side_effects():
    from app.automation_api import CandidateDecisionRequest

    payload = CandidateDecisionRequest(decision="approved", note="人工采用")
    assert payload.model_dump() == {"decision": "approved", "note": "人工采用"}
