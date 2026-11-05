from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.complaint_intelligence.config import get_complaint_intelligence_config
from app.complaint_intelligence.duplicate_merger.service import DuplicateMergeConflict
from app.complaint_intelligence.repository import InMemoryComplaintIntelligenceRepository
from app.complaint_intelligence.schemas import ComplaintIntelligenceEvent
from app.complaint_intelligence.service import ComplaintIntelligenceService
from app.complaint_intelligence.sqlite_repository import SQLiteComplaintIntelligenceRepository


BASE_TIME = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)


def _events() -> list[ComplaintIntelligenceEvent]:
    return [
        ComplaintIntelligenceEvent.model_validate({
            "id": f"review-noise-{index}",
            "received_at": (BASE_TIME + timedelta(minutes=index)).isoformat(),
            "body": "행복아파트 정문 공사 소음 신고입니다.",
            "region": "행복아파트",
            "final_department": "환경관리과",
            "civil_category": "생활불편",
            "entity_texts": ["행복아파트", "공사장", "소음"],
            "request_segments": ["공사 소음 저감 조치 요청"],
            "structured_elements": {
                "observation": "행복아파트 정문 공사 소음이 반복됩니다.",
                "result": "야간 휴식에 불편이 발생합니다.",
                "request": "공사 소음 저감 조치 요청",
                "context": "같은 아파트 정문 주변 반복 민원입니다.",
            },
        })
        for index in range(2)
    ]


def _service(db_path) -> ComplaintIntelligenceService:
    config = replace(
        get_complaint_intelligence_config(),
        db_path=str(db_path),
        public_insight_llm_enabled=False,
        scheduler_enabled=False,
    )
    return ComplaintIntelligenceService(
        repository=SQLiteComplaintIntelligenceRepository(db_path), config=config,
    )


def _decide(service, merge_id, status):
    action = {"confirmed": "confirm", "split": "split", "rejected": "reject"}[status]
    return getattr(service, f"{action}_duplicate_group")(merge_id)


@pytest.mark.parametrize("status", ["confirmed", "split", "rejected"])
@pytest.mark.parametrize("restart", [False, True], ids=["same-service", "new-service"])
def test_reanalysis_preserves_reviewed_sqlite_record_and_actions(tmp_path, status, restart):
    db_path = tmp_path / "review.db"
    service = _service(db_path)
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = _decide(service, group.merge_id, status)
    if restart:
        service = _service(db_path)

    returned = service.run_duplicate_analysis(events)[0]

    assert returned == reviewed
    assert service.get_duplicate_group(group.merge_id) == reviewed
    if status == "confirmed":
        assert "draft_reply" in returned.allowed_actions
        assert service.build_duplicate_draft_reply_payload(group.merge_id).merge_id == group.merge_id
        # 명시적인 확정 → 분리 전이는 저장 경계에서도 계속 허용한다.
        assert service.split_duplicate_group(group.merge_id).status == "split"
    else:
        assert "draft_reply" in returned.blocked_actions
        with pytest.raises(DuplicateMergeConflict) as exc_info:
            service.build_duplicate_draft_reply_payload(group.merge_id)
        assert exc_info.value.code == "DUPLICATE_GROUP_NOT_CONFIRMED"


@pytest.mark.parametrize("repository_type", ["memory", "sqlite"])
@pytest.mark.parametrize("status", ["confirmed", "split", "rejected"])
def test_repository_rejects_candidate_overwrite_of_reviewed_record(tmp_path, repository_type, status):
    repository = (
        InMemoryComplaintIntelligenceRepository() if repository_type == "memory"
        else SQLiteComplaintIntelligenceRepository(tmp_path / "boundary.db")
    )
    service = ComplaintIntelligenceService(repository=repository)
    candidate = service.run_duplicate_analysis(_events())[0]
    reviewed = _decide(service, candidate.merge_id, status)

    repository.save_duplicate_groups([candidate.model_copy(update={"confidence": 0.1})])

    assert repository.get_duplicate_group(candidate.merge_id) == reviewed


def test_candidate_refresh_keeps_new_risk_gate(tmp_path):
    service = _service(tmp_path / "candidate.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    assert "confirm" in group.allowed_actions
    events[1] = events[1].model_copy(update={"final_department": "도로관리과"})

    updated = service.run_duplicate_analysis(events)[0]

    assert updated.merge_id == group.merge_id
    assert updated.status == "candidate"
    assert any(flag.code == "DEPARTMENT_MISMATCH" for flag in updated.risk_flags)
    assert "confirm" in updated.blocked_actions
    assert service.get_duplicate_group(group.merge_id) == updated
    with pytest.raises(DuplicateMergeConflict) as exc_info:
        service.confirm_duplicate_group(group.merge_id)
    assert exc_info.value.code == "DUPLICATE_GROUP_BLOCKED_BY_RISK"


@pytest.mark.parametrize("status", ["confirmed", "split", "rejected"])
@pytest.mark.parametrize("analysis", ["run_duplicate_analysis", "run_analysis"])
def test_changed_reviewed_inputs_fail_before_overwriting_judgment_or_events(tmp_path, status, analysis):
    db_path = tmp_path / "changed.db"
    service = _service(db_path)
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = _decide(service, group.merge_id, status)
    original_events = service.repository.get_events()
    original_payload = (
        service.build_duplicate_draft_reply_payload(group.merge_id) if status == "confirmed" else None
    )
    service = _service(db_path)
    events[1] = events[1].model_copy(update={"final_department": "도로관리과"})

    with pytest.raises(DuplicateMergeConflict) as exc_info:
        getattr(service, analysis)(events)

    assert exc_info.value.code == "DUPLICATE_GROUP_REVIEW_REQUIRED"
    assert exc_info.value.details["merge_ids"] == [group.merge_id]
    assert service.get_duplicate_group(group.merge_id) == reviewed
    assert service.repository.get_events() == original_events
    if original_payload:
        assert service.build_duplicate_draft_reply_payload(group.merge_id) == original_payload


def test_added_member_gets_separate_candidate_without_resetting_prior_review(tmp_path):
    service = _service(tmp_path / "membership.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = service.confirm_duplicate_group(group.merge_id)
    new_member = events[-1].model_copy(update={"id": "review-noise-new"})

    expanded = service.run_duplicate_analysis([*events, new_member])[0]

    assert expanded.merge_id != reviewed.merge_id
    assert expanded.status == "candidate"
    assert "draft_reply" in expanded.blocked_actions
    assert service.get_duplicate_group(group.merge_id) == reviewed


@pytest.mark.parametrize("analysis", ["run_duplicate_analysis", "run_analysis"])
def test_replay_metadata_change_does_not_require_duplicate_review(tmp_path, analysis):
    service = _service(tmp_path / "metadata.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = service.confirm_duplicate_group(group.merge_id)
    events[0] = events[0].model_copy(update={
        "pipeline_version": "replay-v2", "status": "completed", "embedding": [0.0, 1.0],
    })
    events[0].structured_elements.request.confidence = 0.7

    result = getattr(service, analysis)(events, source_name="replay-v2")

    if analysis == "run_duplicate_analysis":
        assert result[0] == reviewed
    assert service.get_duplicate_group(group.merge_id) == reviewed
    assert service.repository.get_events([events[0].id])[0].pipeline_version == "replay-v2"


@pytest.mark.parametrize("endpoint", [
    "run-analysis", "duplicate-groups/run-analysis", "dashboard/run-analysis", "public-insights/run-analysis",
])
def test_changed_reviewed_inputs_return_api_conflict(tmp_path, monkeypatch, endpoint):
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.api.routers import complaint_intelligence

    service = _service(tmp_path / "api-review.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = service.confirm_duplicate_group(group.merge_id)
    monkeypatch.setattr(complaint_intelligence, "get_complaint_intelligence_service", lambda: service)
    events[1] = events[1].model_copy(update={"region": "다른아파트"})

    response = TestClient(app).post(
        f"/complaint-intelligence/{endpoint}",
        json={"events": [event.model_dump(mode="json") for event in events]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_GROUP_REVIEW_REQUIRED"
    assert service.get_duplicate_group(group.merge_id) == reviewed


def _changed_scheduler(service, events, *, below_minimum=False):
    from app.complaint_intelligence.collector import CollectorBatch
    from app.complaint_intelligence.scheduler import ComplaintIntelligenceScheduler

    class Collector:
        def collect_since(self, watermark, limit):
            changed = events[1].model_copy(update={"final_department": "도로관리과"})
            return CollectorBatch(
                source_name="changed-review", mode="replay", events=[events[0], changed],
                watermark=changed.received_at,
            )

    config = replace(service.config, scheduler_min_events=3 if below_minimum else 1)
    return ComplaintIntelligenceScheduler(service, config=config, collector=Collector())


@pytest.mark.parametrize("status", ["confirmed", "split", "rejected"])
@pytest.mark.parametrize("entrypoint", ["poll_once", "run_once", "below-minimum"])
def test_collector_does_not_overwrite_reviewed_inputs_or_advance_checkpoint(tmp_path, status, entrypoint):
    service = _service(tmp_path / "collector-review.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    reviewed = _decide(service, group.merge_id, status)
    stored_events = service.repository.get_events()
    scheduler = _changed_scheduler(service, events, below_minimum=entrypoint == "below-minimum")

    if entrypoint == "poll_once":
        with pytest.raises(DuplicateMergeConflict) as exc_info:
            scheduler.poll_once()
        assert exc_info.value.code == "DUPLICATE_GROUP_REVIEW_REQUIRED"
    else:
        result = scheduler.run_once()
        assert result.ran is False
        assert result.reason == "DUPLICATE_GROUP_REVIEW_REQUIRED"

    assert service.get_duplicate_group(group.merge_id) == reviewed
    assert service.repository.get_events() == stored_events
    assert service.repository.list_collector_checkpoints() == []
    assert scheduler.status()["last_watermark"] is None
    assert scheduler.status()["last_poll_watermark"] is None


@pytest.mark.parametrize("endpoint", ["collector/poll-once", "scheduler/run-once"])
def test_collector_api_exposes_review_required_without_mutating_events(tmp_path, monkeypatch, endpoint):
    from fastapi.testclient import TestClient
    from app.api.main import app
    from app.api.routers import complaint_intelligence

    service = _service(tmp_path / "collector-api.db")
    events = _events()
    group = service.run_duplicate_analysis(events)[0]
    service.confirm_duplicate_group(group.merge_id)
    stored_events = service.repository.get_events()
    scheduler = _changed_scheduler(service, events)
    monkeypatch.setattr(complaint_intelligence, "get_complaint_intelligence_scheduler", lambda: scheduler)

    response = TestClient(app).post(f"/complaint-intelligence/{endpoint}")

    if endpoint.startswith("collector"):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DUPLICATE_GROUP_REVIEW_REQUIRED"
    else:
        assert response.status_code == 200
        assert response.json()["data"]["ran"] is False
        assert response.json()["data"]["reason"] == "DUPLICATE_GROUP_REVIEW_REQUIRED"
    assert service.repository.get_events() == stored_events
