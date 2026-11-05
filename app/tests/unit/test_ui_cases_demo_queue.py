from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app


def test_ui_cases_return_pending_demo_complaints_without_answers():
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    response = client.get("/api/v1/ui/cases")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    cases = body["data"]["cases"]
    assert len(cases) == 8
    assert all(str(item["case_id"]).startswith("NEW-2026-") for item in cases)
    assert all("상담원:" not in item["raw_text"] for item in cases)
    assert all("A :" not in item["raw_text"] for item in cases)
    assert all(item["status"] in {"미처리", "검토중"} for item in cases)
