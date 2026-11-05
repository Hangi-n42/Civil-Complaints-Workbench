from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routers import complaint_intelligence as router
from app.complaint_intelligence.config import get_complaint_intelligence_config
from app.complaint_intelligence.provenance import run_provenance, seed_source_provenance
from app.complaint_intelligence.repository import AnalysisRunRecord
from app.complaint_intelligence.service import ComplaintIntelligenceService
from app.complaint_intelligence.sqlite_repository import SQLiteComplaintIntelligenceRepository


NOW = datetime(2026, 6, 20, 9, tzinfo=timezone.utc)


def _run(metadata):
    return AnalysisRunRecord(run_id="run-test", mode="replay", status="completed", source_name="provided-name",
                             started_at=NOW, completed_at=NOW, as_of=NOW, metadata=metadata)


def test_seed_provenance_preserves_policy_without_copying_raw_text_or_guessing_coordinates():
    seed = {"scenarios": [{"source_policy": "real_text_excerpt_replayed_time", "source_records": [
        {"source_dataset": "/private/source/fixture.json", "masked_excerpt": "합성 발췌", "source_id": "test-1"},
    ]}]}
    result = seed_source_provenance(seed)
    assert result["source_policies"] == ["real_text_excerpt_replayed_time"]
    assert result["source_datasets"] == ["fixture.json"]
    assert result["time_basis"] == "scenario_replay"
    assert result["location_basis"] == "unknown"
    assert "masked_excerpt" not in str(result)
    assert seed_source_provenance(seed, built_scenarios=True)["location_basis"] == "scenario_assigned"


@pytest.mark.parametrize("provider_class, success, fallback, expected", [
    ("FakePublicInsightLLMProvider", 1, 0, ["simulated"]),
    ("LocalLLMProvider", 1, 0, ["local_model"]),
    ("LocalLLMProvider", 1, 1, ["local_model", "template"]),
    ("DisabledLLMProvider", 0, 1, ["template"]),
    ("UnrecognizedProvider", 1, 0, ["unknown"]),
])
def test_generation_labels_use_recorded_execution_class_and_counts(provider_class, success, fallback, expected):
    result = run_provenance(_run({"llm_metrics": {"llm_provider": "local", "llm_provider_class": provider_class,
                                                "llm_success_count": success, "llm_attempt_count": success,
                                                "fallback_count": fallback}}))
    assert result.generation_methods == expected


def test_legacy_metadata_does_not_infer_origin_from_source_name_or_current_files():
    run = _run({"seed_file": "/not-opened/current-seed.json", "trigger": "demo_seed"})
    run.source_name = "real_replay_local_model"
    result = run_provenance(run)
    assert result.source.source_policies == []
    assert result.source.location_basis == "unknown"
    assert result.generation_methods == ["unknown"]
    assert result.provider is None


def test_malformed_optional_metadata_stays_unknown_without_breaking_stored_reads():
    result = run_provenance(_run({"analysis_type": [], "source_provenance": {"time_basis": "unrecorded"},
                                  "llm_metrics": {"llm_provider_class": [], "llm_provider": {}, "llm_model": 3,
                                                  "llm_success_count": 1, "llm_attempt_count": -1, "fallback_count": True}}))
    assert result.generation_methods == ["unknown"]
    assert result.source.time_basis == "unknown"
    assert result.provider_class is None and result.provider is None and result.model is None
    assert result.analysis_type is None and result.llm_attempt_count is None and result.fallback_count is None
    assert run_provenance(_run({"llm_metrics": {"llm_attempt_count": 0}})).generation_methods == ["not_run"]


def _service(path):
    config = replace(get_complaint_intelligence_config(), public_insight_llm_provider="fake",
                     public_insight_enabled=True, public_insight_llm_enabled=True, scheduler_enabled=False,
                     min_recent_count=2, min_affected_count=2, public_insight_min_candidate_complaint_count=2)
    return ComplaintIntelligenceService(repository=SQLiteComplaintIntelligenceRepository(path), config=config)


def _client(monkeypatch, service):
    monkeypatch.setattr(router, "get_complaint_intelligence_service", lambda: service)
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_new_analysis_then_restarted_stored_read_preserves_declared_source_and_actual_fake_metrics(tmp_path, monkeypatch):
    path = tmp_path / "provenance.db"
    service = _service(path)
    payload = {"mode": "replay", "source_name": "synthetic-provenance-test", "as_of": NOW.isoformat(),
               "source_provenance": {"source_policies": ["synthetic_scenario"], "source_datasets": ["synthetic-fixture"],
                                     "time_basis": "scenario_replay", "location_basis": "scenario_assigned"},
               "events": [{"id": f"test-{i}", "received_at": (NOW - timedelta(minutes=i)).isoformat(),
                           "body": "합성동 도로 싱크홀 침하 구멍이 생겨 위험합니다.", "region": "합성동", "final_department": "도로관리과"}
                          for i in range(6)]}
    response = _client(monkeypatch, service).post("/complaint-intelligence/dashboard/run-analysis", json=payload)
    assert response.status_code == 200
    created = response.json()["data"]
    assert created["operation"] == "new_analysis"
    provenance = created["provenance_runs"][0]
    assert provenance["source"]["location_basis"] == "scenario_assigned"
    assert provenance["provider_class"] == "FakePublicInsightLLMProvider"
    assert provenance["generation_methods"] == ["simulated"]
    before = path.read_bytes()
    restarted = _service(path)
    client = _client(monkeypatch, restarted)
    stored = client.get("/complaint-intelligence/dashboard").json()["data"]
    assert stored["operation"] == "stored_read"
    assert stored["provenance_runs"] == created["provenance_runs"]
    assert stored["issue_alerts"] == created["issue_alerts"]
    assert stored["public_insights"] == created["public_insights"]
    runs = client.get("/complaint-intelligence/analysis-runs").json()["data"]["analysis_runs"]
    assert runs[0]["provenance"] == provenance
    assert len(runs) == 1
    assert path.read_bytes() == before


def test_stored_local_generation_is_not_relabelled_using_current_fake_provider(tmp_path, monkeypatch):
    service = _service(tmp_path / "stored-local.db")
    repository = service.repository
    repository.save_analysis_run(run_id="stored-local", mode="manual", source_name="provided-source", event_count=0,
                                 started_at=NOW, as_of=NOW)
    repository.complete_analysis_run("stored-local", status="completed", completed_at=NOW, metadata={"llm_metrics": {
        "llm_provider": "local", "llm_provider_class": "LocalLLMProvider", "llm_model": "recorded-model",
        "llm_success_count": 1, "llm_attempt_count": 1, "fallback_count": 0,
    }})
    response = _client(monkeypatch, service).get("/complaint-intelligence/dashboard")
    run = response.json()["data"]["provenance_runs"][0]
    assert run["generation_methods"] == ["local_model"]
    assert run["model"] == "recorded-model"
    assert run["source"]["source_policies"] == []


@pytest.mark.parametrize("endpoint", ["/run-analysis", "/public-insights/run-analysis"])
def test_direct_analysis_api_and_alias_return_recorded_provenance(tmp_path, monkeypatch, endpoint):
    service = _service(tmp_path / "direct.db")
    response = _client(monkeypatch, service).post("/complaint-intelligence" + endpoint, json={
        "events": [], "mode": "manual", "as_of": NOW.isoformat(),
        "source_provenance": {"source_policies": ["synthetic_scenario"]},
    })
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provenance"]["run_id"] == data["run_id"]
    assert data["provenance"]["source"]["source_policies"] == ["synthetic_scenario"]
    assert data["provenance"]["source"]["location_basis"] == "unknown"
    assert data["provenance"]["generation_methods"] == ["not_run"]


@pytest.mark.parametrize("entry", ["demo", "prepare_build", "prepare_skip", "prepare_fallback"])
def test_seed_entrypoints_persist_declarations_without_guessing_reused_coordinates(tmp_path, monkeypatch, entry):
    from scripts import prepare_complaint_intelligence_real_replay as prepare
    from scripts import seed_complaint_intelligence_demo as demo

    seed = {"mode": "replay", "source_name": "synthetic-test", "as_of": NOW.isoformat(), "scenarios": [{
        "id": "synthetic-road", "source_policy": "real_text_excerpt_replayed_time",
        "source_records": [{"source_dataset": "synthetic-fixture.json", "masked_excerpt": "합성 검사 발췌"}],
        "events": [{"id": f"synthetic-{i}", "received_at": (NOW - timedelta(minutes=i)).isoformat(),
                    "body": "합성동 도로 싱크홀 침하 구멍이 생겨 위험합니다.", "region": "합성동", "final_department": "도로관리과"}
                   for i in range(6)],
    }]}
    seed_path = tmp_path / "synthetic-seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    db_path = tmp_path / "synthetic.db"
    services = []

    def build_service(path, **kwargs):
        service = _service(path)
        services.append(service)
        return service

    if entry == "demo":
        monkeypatch.setattr(demo, "build_service", build_service)
        monkeypatch.setattr(sys, "argv", ["seed", "--seed", str(seed_path), "--db-path", str(db_path),
                                          "--output", str(tmp_path / "run.json")])
        assert demo.main() == 0
    else:
        monkeypatch.setattr(prepare, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(prepare, "build_service", build_service)
        monkeypatch.setattr(prepare, "build_demo_seed", lambda **kwargs: (
            {"scenarios": []} if entry == "prepare_fallback" else seed, {}))
        monkeypatch.setattr(sys, "argv", ["prepare", "--seed", str(seed_path), "--db-path", str(db_path),
                                          "--input", str(tmp_path / "unused-synthetic-input"),
                                          "--build-report", str(tmp_path / "build.json"),
                                          "--run-report", str(tmp_path / "run.json"),
                                          *(["--skip-build"] if entry == "prepare_skip" else [])])
        assert prepare.main() == 0

    saved = next(run for run in services[0].list_analysis_runs() if run.metadata["analysis_type"] == "issue_and_public_insight")
    source = run_provenance(saved).source
    assert source.source_datasets == ["synthetic-fixture.json"]
    assert source.source_policies == ["real_text_excerpt_replayed_time"]
    assert source.time_basis == "scenario_replay"
    assert source.location_basis == ("scenario_assigned" if entry == "prepare_build" else "unknown")
    assert source.structured_fields_basis == ("scenario_template" if entry == "prepare_build" else "unknown")
    assert "masked_excerpt" not in saved.metadata["source_provenance"]
    assert json.loads(seed_path.read_text(encoding="utf-8")) == seed


def test_duplicate_demo_labels_synthetic_input_without_modifying_confirmed_group(tmp_path, monkeypatch):
    from scripts import seed_duplicate_merge_demo as demo

    service = _service(tmp_path / "duplicate.db")
    monkeypatch.setattr(demo, "build_service", lambda *args, **kwargs: service)
    report = demo.run_duplicate_merge_demo(seed_path=tmp_path / "seed.json", db_path=tmp_path / "duplicate.db",
                                          report_path=tmp_path / "report.json")
    run = next(run for run in service.list_analysis_runs() if run.run_id == report["run_id"])
    source = run_provenance(run).source
    assert source.source_policies == ["synthetic_scenario"]
    assert source.location_basis == "scenario_assigned"
    assert any(str(group.status) == "confirmed" for group in service.list_duplicate_groups())
