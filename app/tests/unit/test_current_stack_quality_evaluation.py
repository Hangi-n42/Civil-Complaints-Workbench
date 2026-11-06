from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import base64
import json
import socket
from types import SimpleNamespace

import pytest

from scripts import evaluate_current_stack_quality as quality


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PROMETHEUS_REVISION_MODEL", quality.MODELS["generation"])
    monkeypatch.setattr(quality, "memory_pressure_level", lambda: 1)
    return quality.prepare(tmp_path / "manifest.json")


def metadata(text="합성 응답", **overrides):
    return {"text": text, "done": True, "done_reason": "stop", "prompt_eval_count": 100,
            "eval_count": 10, "total_duration": 1000, **overrides}


def rehash(manifest):
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = quality.sha(manifest)


def test_manifest_freezes_requests_settings_hashes_and_retained_failures(manifest):
    quality.validate_manifest(manifest)
    assert manifest["call_budget"]["per_case"] == 14
    assert manifest["call_budget"]["total"] == 28
    assert manifest["human_review"]["status"] == "unmeasured"
    assert manifest["retained_failures"][0]["status"] == "failed"
    assert manifest["retained_failures"][0]["value"] < manifest["retained_failures"][0]["minimum"]
    for case in quality.read(quality.CASES)["cases"]:
        assert case["request"]["use_search_results"] is True
        assert set(case["request"]).isdisjoint({"expected", "generated_answer", "human_review_checks", "rubric"})


@pytest.mark.asyncio
async def test_live_without_approval_stops_before_output_import_or_network(manifest, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("승인 전 소켓 연결")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with pytest.raises(quality.EvaluationHalt, match="not_approved"):
        await quality.run_evaluation(manifest, tmp_path / "never-created", mode="live_inference")
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize("change", ["expiry", "hash", "calls"])
def test_wrong_or_expired_approval_is_rejected(manifest, tmp_path, change):
    entry = {"approved": True, "manifest_sha256": manifest["manifest_sha256"], "max_model_calls": 28,
             "output_directory": str((tmp_path / "run").resolve()),
             "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    if change == "expiry":
        entry["expires_at"] = "2020-01-01T00:00:00+00:00"
    if change == "hash":
        entry["manifest_sha256"] = "different"
    if change == "calls":
        entry["max_model_calls"] = 29
    path = tmp_path / "approval.json"
    quality.write(path, entry)
    with pytest.raises(quality.EvaluationHalt):
        quality.validate_approval(manifest, path, tmp_path / "run")


@pytest.mark.asyncio
async def test_approval_exact_run_path_and_single_use_survive_zero_call_stop(manifest, tmp_path, monkeypatch):
    from app.core.config import settings
    output, approval = tmp_path / "run", tmp_path / "approval.json"
    quality.write(approval, {"approved": True, "manifest_sha256": manifest["manifest_sha256"],
        "max_model_calls": 28, "output_directory": str(output.resolve()),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    with pytest.raises(quality.EvaluationHalt, match="approval_invalid"):
        await quality.run_evaluation(manifest, tmp_path / "new-name", mode="live_inference", approval=approval)
    assert not (tmp_path / "new-name").exists()
    monkeypatch.setattr(settings, "PROMETHEUS_REVISION_MODEL", "")
    run = await quality.run_evaluation(manifest, output, mode="live_inference", approval=approval)
    assert run["stop_reason"] == "runtime_settings_changed"
    assert run["new_model_calls"] == run["processed_calls"] == 0
    claim_path = tmp_path / "approval.json.claim.json"
    claim = quality.read(claim_path)
    assert claim["manifest_sha256"] == manifest["manifest_sha256"]
    assert claim["output_directory"] == str(output.resolve())
    with pytest.raises(quality.EvaluationHalt, match="already_consumed"):
        await quality.run_evaluation(manifest, output, mode="live_inference", approval=approval)
    assert quality.read(claim_path) == claim


def test_input_or_code_tampering_invalidates_manifest(manifest):
    manifest["source_hashes"]["app/generation/service.py"] = "different"
    rehash(manifest)
    with pytest.raises(quality.EvaluationHalt, match="code_changed"):
        quality.validate_manifest(manifest)


@pytest.mark.parametrize("field", ["product_python_tree_sha256", "runtime_packages"])
def test_indirect_code_and_runtime_identity_are_bound(manifest, field):
    manifest[field] = "changed"
    rehash(manifest)
    with pytest.raises(quality.EvaluationHalt, match="code_changed"):
        quality.validate_manifest(manifest)


def test_offline_and_loopback_connection_guards(monkeypatch):
    seen = []
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: seen.append(address))
    with quality.isolated_runtime():
        with socket.socket() as sock, pytest.raises(quality.EvaluationHalt):
            sock.connect(("127.0.0.1", 11434))
    with quality.isolated_runtime(endpoint="http://127.0.0.1:11434"):
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", 11434))
            with pytest.raises(quality.EvaluationHalt):
                sock.connect(("example.com", 443))
            with pytest.raises(quality.EvaluationHalt):
                sock.connect(("127.0.0.1", 8001))
    assert seen == [("127.0.0.1", 11434)]


@pytest.mark.asyncio
async def test_case_and_total_limits_stop_before_fifteenth_or_twenty_ninth_call(manifest, tmp_path):
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    calls = []
    async def native(*args, **kwargs):
        calls.append(kwargs)
        return metadata()
    for case_id in ("A", "B"):
        ledger.case_id, ledger.case_calls = case_id, 0
        for _ in range(14):
            await ledger.call(native, "합성 입력")
        with pytest.raises(quality.EvaluationHalt, match="budget_exhausted"):
            await ledger.call(native, "추가 입력")
    assert len(calls) == len(ledger.records) == 28
    assert all(row["sent_to_model"] is False for row in ledger.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"num_ctx": 16384}, {"model": "new-model"}, {"think": True}])
async def test_changed_model_or_options_never_reach_native(manifest, tmp_path, options):
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    async def forbidden(*args, **kwargs):
        pytest.fail("예산 검사 전 native 호출")
    with pytest.raises(quality.EvaluationHalt):
        await ledger.call(forbidden, "합성 입력", **options)


@pytest.mark.asyncio
async def test_oversized_prompt_never_reaches_native(manifest, tmp_path):
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    async def forbidden(*args, **kwargs):
        pytest.fail("긴 입력을 절단하거나 모델 호출하면 안 됨")
    with pytest.raises(quality.EvaluationHalt, match="prompt_budget"):
        await ledger.call(forbidden, "가" * 11265)


@pytest.mark.asyncio
@pytest.mark.parametrize("response,reason", [(metadata(done_reason="length"), "truncated"),
    (metadata(prompt_eval_count=None), "usage_unavailable"),
    (metadata(prompt_eval_count=8000), "context_budget"), (metadata(done=False), "incomplete"),
    (metadata(prompt_eval_count=True), "usage_unavailable"), (metadata(eval_count=-1), "usage_unavailable"),
    (metadata(eval_count=1600), "output_budget")])
async def test_failed_usage_or_length_keeps_raw_response(manifest, tmp_path, response, reason):
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    async def native(*args, **kwargs):
        return response
    with pytest.raises(quality.EvaluationHalt, match=reason):
        await ledger.call(native, "합성 입력")
    assert ledger.records[0]["response"] == response
    assert json.loads((tmp_path / "calls.jsonl").read_text())["response"] == response


@pytest.mark.asyncio
async def test_replay_requires_verified_identity_exact_input_and_live_provenance(manifest, tmp_path):
    manifest["inventory_evidence"]["models"] = [{"name": name, "digest": "synthetic-digest"} for name in quality.MODELS.values()]
    rehash(manifest)
    original = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    original.case_id = "A"
    async def native(*args, **kwargs):
        return metadata()
    await original.call(native, "합성 입력")
    saved = {**original.records[0], "mode": "live_inference",  # synthetic test fixture, not an inference receipt
             "wire_request": original.records[0]["request"]["wire"],
             "http_response": {"status": 200, "body_base64": base64.b64encode(json.dumps({
                 "response": "합성 응답", **{key: value for key, value in metadata().items() if key != "text"}}).encode()).decode()}}
    async def forbidden(*args, **kwargs):
        pytest.fail("재생에서 native 호출")
    replay = quality.CallLedger(manifest, tmp_path, "stored_replay", [saved])
    replay.case_id = "A"
    assert await replay.call(forbidden, "합성 입력") == "합성 응답"
    with pytest.raises(quality.EvaluationHalt, match="replay_evidence_exhausted"):
        await replay.call(forbidden, "후속 평가 입력")
    assert replay.records[-1]["sent_to_model"] is False
    assert replay.records[-1]["raw_response_available"] is False
    replay = quality.CallLedger(manifest, tmp_path, "stored_replay", [saved])
    replay.case_id = "A"
    with pytest.raises(quality.EvaluationHalt, match="mismatch"):
        await replay.call(forbidden, "변경된 입력")
    assert replay.records[0]["sent_to_model"] is False
    altered = {**saved, "response": {**saved["response"], "prompt_eval_count": 1}}
    replay = quality.CallLedger(manifest, tmp_path, "stored_replay", [altered])
    replay.case_id = "A"
    with pytest.raises(quality.EvaluationHalt, match="raw_response_mismatch"):
        await replay.call(forbidden, "합성 입력")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(200, b'{"response":"  raw text  ","done":true,"done_reason":"stop","prompt_eval_count":100,"eval_count":2,"extra":"retained"}'),
    (200, b"not-json-at-all"), (503, b"synthetic OOM body"), (400, b"synthetic context length exceeded")])
async def test_native_http_tee_keeps_exact_bytes_on_success_parse_or_http_failure(manifest, tmp_path, monkeypatch, status, body):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    requests = []
    async def transport(request):
        requests.append(request)
        return httpx.Response(status, content=body)
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    ledger.base_url = manifest["ollama_endpoint"]
    native = GenerationService().call_ollama
    if status == 200 and body.startswith(b'{'):
        assert await ledger.call(native, "합성 HTTP 입력", temperature=0.2) == "raw text"
    else:
        with pytest.raises(quality.EvaluationHalt, match="model_error"):
            await ledger.call(native, "합성 HTTP 입력", temperature=0.2)
    row = ledger.records[0]
    assert base64.b64decode(row["wire_request"]["body_base64"]) == requests[0].content
    assert row["wire_request"] == row["request"]["wire"]
    assert base64.b64decode(row["http_response"]["body_base64"]) == body
    assert row["http_response"]["status"] == status
    assert row["raw_response_available"] is True
    assert row["sent_to_model"] is False
    if status == 503:
        assert row["upstream_reported_category"] == "oom"
    if status == 400:
        assert row["upstream_reported_category"] == "context_limit"


@pytest.mark.asyncio
async def test_response_absent_and_wire_mismatch_are_preserved_without_new_endpoint_call(manifest, tmp_path, monkeypatch):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    async def fail_transport(request):
        raise httpx.ReadTimeout("synthetic response missing", request=request)
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(fail_transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    ledger.base_url = manifest["ollama_endpoint"]
    with pytest.raises(quality.EvaluationHalt, match="model_error"):
        await ledger.call(GenerationService().call_ollama, "합성 입력")
    assert ledger.records[0]["raw_response_available"] is False
    assert ledger.records[0]["transport_error_type"] == "ReadTimeout"
    async def wrong_native(*args, **kwargs):
        async with httpx.AsyncClient() as client:
            await client.post(manifest["ollama_endpoint"] + "/api/pull", json={"name": "new-model"})
    with pytest.raises(quality.EvaluationHalt, match="wire_request_mismatch"):
        await ledger.call(wrong_native, "합성 입력")
    assert ledger.records[-1]["sent_to_model"] is False


@pytest.mark.asyncio
async def test_actual_qa_route_reaches_observer_without_database_or_network(manifest, tmp_path, monkeypatch):
    from app.generation.service import GenerationService
    seen = []
    async def synthetic_stop(self, prompt, *args, **kwargs):
        assert quality.read(tmp_path / "run" / "run.json")["status"] == "incomplete"
        seen.append(prompt)
        raise quality.EvaluationHalt("synthetic_transport_stop")
    monkeypatch.setattr(GenerationService, "call_ollama", synthetic_stop)
    run = await quality.run_evaluation(manifest, tmp_path / "run", mode="synthetic_test")
    assert run["stop_reason"] == "synthetic_transport_stop"
    assert len(seen) == 1
    assert run["new_model_calls"] == 0
    assert "학교 앞 불법 주정차" in seen[0]
    assert "현재 도로 보수 일정은 확정되어 내일" not in seen[0]
    assert quality.report(manifest, tmp_path / "run")["human_review"]["status"] == "unmeasured"


@pytest.mark.asyncio
async def test_http_outside_observed_native_call_cannot_reach_transport(manifest, tmp_path, monkeypatch):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    async def forbidden_transport(request):
        pytest.fail("관찰층 밖 요청의 HTTP 운송 실행")
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(forbidden_transport))
            super().__init__(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    async def unobserved_request(self, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            await client.post(manifest["ollama_endpoint"] + "/api/generate", json={"model": "unapproved"})
    monkeypatch.setattr(GenerationService, "build_rag_prompt", unobserved_request)
    run = await quality.run_evaluation(manifest, tmp_path / "run", mode="synthetic_test")
    assert run["stop_reason"] == "unobserved_http_request_out_of_scope"
    assert run["processed_calls"] == run["new_model_calls"] == 0


@pytest.mark.asyncio
async def test_literal_runtime_profile_mismatch_stops_before_api(manifest, tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PROMETHEUS_REVISION_MODEL", "")
    run = await quality.run_evaluation(manifest, tmp_path / "revision", mode="synthetic_test")
    assert run["stop_reason"] == "runtime_settings_changed"
    monkeypatch.setattr(settings, "PROMETHEUS_REVISION_MODEL", quality.MODELS["generation"])
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", manifest["ollama_endpoint"])
    run = await quality.run_evaluation(manifest, tmp_path / "url", mode="synthetic_test")
    assert run["stop_reason"] == "ollama_endpoint_changed"
    assert run["processed_calls"] == run["new_model_calls"] == 0


@pytest.mark.asyncio
async def test_cancelled_inflight_request_keeps_unknown_completion_receipt(manifest, tmp_path, monkeypatch):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    original_fsync, synced = quality.os.fsync, []
    def observed_fsync(fd):
        original_fsync(fd)
        synced.append(fd)
    monkeypatch.setattr(quality.os, "fsync", observed_fsync)
    async def pending_transport(request):
        assert synced, "POST 운송 전에 reservation fsync가 필요하다."
        reserved = json.loads((tmp_path / "reservations.jsonl").read_text())
        assert reserved["state"] == "reserved_before_transport"
        assert reserved["server_completion_unknown"] is True
        assert reserved["call_id"] == "call-0001"
        await asyncio.Future()
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(pending_transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    monkeypatch.setattr(quality, "available_memory", lambda: 16 * 1024**3)
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic transport only
    ledger.base_url = manifest["ollama_endpoint"]
    async def empty_residents():
        return []
    monkeypatch.setattr(ledger, "residents", empty_residents)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ledger.call(GenerationService().call_ollama, "합성 대기 입력"), timeout=0.01)
    row = json.loads((tmp_path / "calls.jsonl").read_text())
    assert row["sent_to_model"] is True
    assert row["server_completion_unknown"] is True
    assert row["raw_response_available"] is False


def test_report_matches_reservations_to_final_receipts_without_double_counting(manifest, tmp_path):
    quality.write(tmp_path / "run.json", {"mode": "live_inference", "manifest_sha256": manifest["manifest_sha256"],
        "status": "incomplete", "case_results": [], "new_model_calls": 0})
    final = {"call_id": "call-0001", "mode": "live_inference", "sent_to_model": True,
             "elapsed_seconds": 1, "request": {"model": quality.MODELS["generation"]}}
    quality.append_synced(tmp_path / "calls.jsonl", final)
    for call_id in ("call-0001", "call-0002"):
        quality.append_synced(tmp_path / "reservations.jsonl", {"call_id": call_id, "mode": "live_inference"})
    journal = quality.report(manifest, tmp_path)["reservation_journal"]
    assert journal["live_reserved_calls"] == journal["live_attempts_including_unresolved"] == 2
    assert journal["unresolved_call_ids"] == ["call-0002"]
    assert journal["server_completion_unknown"] is True
    with (tmp_path / "calls.jsonl").open("ab") as handle:
        handle.write(b'{"call_id":"call-0002",')
    journal = quality.report(manifest, tmp_path)["reservation_journal"]
    assert journal["incomplete_final_call_tail"] is True
    assert journal["unresolved_call_ids"] == ["call-0002"]


@pytest.mark.asyncio
async def test_product_flow_parse_retry_and_uncached_q2_fit_twenty_eight_synthetic_calls(manifest, tmp_path, monkeypatch):
    from app.generation.service import GenerationService
    cases = quality.read(quality.CASES)["cases"]
    failures = set()
    async def synthetic(self, prompt, temperature=0.7, response_schema=None, **kwargs):
        case = next(item for item in cases if item["request"]["query"] in prompt)
        case_id = case["case_id"]
        fields = response_schema["properties"]
        if "answer" in fields:
            revision = prompt.startswith("[PROMETHEUS REVISION TASK]")
            if not revision and (case_id, "generation") not in failures:
                failures.add((case_id, "generation"))
                return metadata("synthetic malformed generation")
            ref = case["request"]["search_results"][0]
            payload = {
                "answer": "제공된 자료는 현재 민원의 처리 결정이 아닙니다. 주정차 단속, 과속방지턱 설치, 가로등 수리의 현재 결정과 담당 부서·접수 방법은 확인이 필요합니다. 보수 일정도 확정되지 않았으므로 완료 시점을 보장하지 않습니다.",
                "citations": [{"chunk_id": ref["chunk_id"], "case_id": ref["case_id"],
                               "snippet": ref["snippet"][:320], "relevance_score": 0.9}],
                "limitations": ["합성 운송 응답으로 runner 경계만 확인합니다."],
                "structured_output": {"summary": case["request"]["query"], "action_items": ["현재 결정 확인 필요", "담당 부서 확인 필요"],
                                      "request_segments": case["request"]["routing_trace"]["request_segments"], "segment_answers": []},
            }
        elif kwargs.get("model") == quality.MODELS["feedback"]:
            payload = {"feedback": "합성 피드백", "revision_hint": "근거 범위를 유지하세요.",
                       "weaknesses": ["합성 검사"], "strengths": [], "risk_flags": []}
        elif "choice" in fields:
            if "[생성 답변]" not in prompt and (case_id, "q2") not in failures:
                failures.add((case_id, "q2"))
                return metadata("synthetic malformed rubric")
            payload = {"choice": 1, "confidence": 0.8, "reason": "합성 검사"}
        else:
            payload = {qid: {"choice": 1, "confidence": 0.8, "reason": "합성 검사", "issues": [], "support": "supported"} for qid in fields}
        return metadata(json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr(GenerationService, "call_ollama", synthetic)
    output = tmp_path / "synthetic-full-flow"
    run = await quality.run_evaluation(manifest, output, mode="synthetic_test")
    assert run["status"] == "completed_fixed_evidence_api", run
    assert run["processed_calls"] == 28
    assert run["new_model_calls"] == 0
    assert len(run["case_results"]) == 2
    result = quality.report(manifest, output)
    assert result["latency"]["observed_client_call_wall_seconds"] is None
    assert result["human_review"]["status"] == "unmeasured"
    assert result["retained_failures"][0]["status"] == "failed"


def test_manual_review_is_bound_to_response_and_synthetic_time_is_not_human_measurement(manifest, tmp_path):
    body = {"answer": "합성 응답"}
    quality.write(tmp_path / "run.json", {"mode": "synthetic_test", "manifest_sha256": manifest["manifest_sha256"],
        "case_results": [{"case_id": "Q16-MULTI", "response_sha256": quality.sha(body), "response": body}]})
    entry = {"case_id": "Q16-MULTI", "response_sha256": quality.sha(body), "reviewer_kind": "human",
             "source": "user_submission", "reviewer_id": "test-fixture", "elapsed_seconds": 5,
             "time_kind": "reviewer_measured_elapsed", "decision": "edit"}
    with pytest.raises(ValueError):
        quality.collect_review(tmp_path, {**entry, "response_sha256": "wrong"})
    quality.collect_review(tmp_path, entry)
    result = quality.report(manifest, tmp_path)
    assert result["human_review"]["status"] == "unmeasured"
    assert result["automatic_metrics_are_human_burden"] is False
    assert result["issue_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["url", "model", "timeout", "think", "format", "options"])
async def test_observer_rejects_changed_native_wire_before_transport(manifest, tmp_path, tamper):
    import httpx
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    async def altered_native(*args, **kwargs):
        payload = json.loads(base64.b64decode(ledger.records[-1]["request"]["wire"]["body_base64"]))
        url = manifest["ollama_endpoint"] + ("/api/pull" if tamper == "url" else "/api/generate")
        if tamper == "model":
            payload["model"] = "new-model"
        if tamper == "think":
            payload["think"] = True
        if tamper == "format":
            payload["format"] = "changed"
        if tamper == "options":
            payload["options"]["num_predict"] = 1537
        async with httpx.AsyncClient(timeout=179 if tamper == "timeout" else 180) as client:
            await client.post(url, json=payload)
    with pytest.raises(quality.EvaluationHalt, match="wire_request_mismatch"):
        await ledger.call(altered_native, "합성 입력")
    assert ledger.records[-1]["sent_to_model"] is False


@pytest.mark.asyncio
async def test_second_http_dispatch_in_one_native_call_stops_before_transport(manifest, tmp_path, monkeypatch):
    import httpx
    original_client = httpx.AsyncClient
    seen = []
    async def transport(request):
        seen.append(request)
        return httpx.Response(200, json={"response": "합성 응답"})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    async def duplicate_native(*args, **kwargs):
        wire = ledger.records[-1]["request"]["wire"]
        payload = json.loads(base64.b64decode(wire["body_base64"]))
        async with httpx.AsyncClient(timeout=180) as client:
            await client.post(wire["url"], json=payload)
            await client.post(wire["url"], json=payload)
    with pytest.raises(quality.EvaluationHalt, match="unbudgeted_native_http_retry"):
        await ledger.call(duplicate_native, "합성 입력")
    assert len(seen) == 1
    assert ledger.records[0]["raw_response_available"] is True


@pytest.mark.asyncio
async def test_own_model_transition_uses_native_eviction_without_added_wait_or_keepalive(manifest, tmp_path, monkeypatch):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    resident = [quality.MODELS["generation"]]
    transmitted = []
    async def transport(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": name} for name in resident]})
        payload = json.loads(request.content)
        transmitted.append(payload)
        resident[:] = [payload["model"]]
        return httpx.Response(200, json={"response": "합성 전환 응답", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 10})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    monkeypatch.setattr(quality, "available_memory", lambda: 16 * 1024**3)
    async def forbidden_wait(*args, **kwargs):
        pytest.fail("모델 자연 만료를 기다리면 안 됨")
    monkeypatch.setattr(quality.asyncio, "sleep", forbidden_wait)
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic HTTP control-branch test only
    ledger.base_url = manifest["ollama_endpoint"]
    ledger.owned_models.add(quality.MODELS["generation"])
    await ledger.call(GenerationService().call_ollama, "합성 전환 입력", model=quality.MODELS["feedback"])
    assert transmitted[0]["model"] == quality.MODELS["feedback"]
    assert "keep_alive" not in transmitted[0]
    assert ledger.records[0]["resident_before"] == [quality.MODELS["generation"]]
    assert ledger.records[0]["resident_after"] == [quality.MODELS["feedback"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("residents", [["foreign-model"], list(quality.MODELS.values())])
async def test_foreign_or_multiple_residents_stop_before_native(manifest, tmp_path, monkeypatch, residents):
    import httpx
    original_client = httpx.AsyncClient
    async def transport(request):
        return httpx.Response(200, json={"models": [{"name": name} for name in residents]})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic HTTP control-branch test only
    ledger.base_url = manifest["ollama_endpoint"]
    ledger.owned_models.update(quality.MODELS.values())
    async def forbidden(*args, **kwargs):
        pytest.fail("다른 모델 점유 중 native 호출")
    with pytest.raises(quality.EvaluationHalt, match="resident_models"):
        await ledger.call(forbidden, "합성 입력")
    assert ledger.records[0]["sent_to_model"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", ["before", "after"])
async def test_memavailable_fallback_gate_preserves_completed_raw_response(manifest, tmp_path, monkeypatch, checkpoint):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    posts = []
    async def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": []})
        posts.append(request)
        return httpx.Response(200, json={"response": "합성 응답", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 10})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    monkeypatch.setattr(quality, "memory_pressure_level", lambda: None)
    values = iter([7 * 1024**3] if checkpoint == "before" else [16 * 1024**3, 7 * 1024**3])
    monkeypatch.setattr(quality, "available_memory", lambda: next(values))
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic HTTP control-branch test only
    ledger.base_url = manifest["ollama_endpoint"]
    with pytest.raises(quality.EvaluationHalt, match="memory_headroom"):
        await ledger.call(GenerationService().call_ollama, "합성 입력")
    assert len(posts) == (1 if checkpoint == "after" else 0)
    assert ledger.records[0]["raw_response_available"] is (checkpoint == "after")


@pytest.mark.asyncio
async def test_loaded_model_low_page_estimate_with_normal_pressure_can_continue(manifest, tmp_path, monkeypatch):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    posts = []
    resident = []
    async def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": model} for model in resident]})
        posts.append(request)
        resident[:] = [quality.MODELS["generation"]]
        return httpx.Response(200, json={"response": "합성 응답", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 10})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    values = iter([16 * 1024**3, 2964537344, 2964537344, 2964537344])
    monkeypatch.setattr(quality, "available_memory", lambda: next(values))
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic HTTP only
    ledger.base_url = manifest["ollama_endpoint"]
    for _ in range(2):
        assert await ledger.call(GenerationService().call_ollama, "합성 입력") == "합성 응답"
    assert len(posts) == 2
    assert ledger.records[0]["memory_pressure_after"] == 1
    assert ledger.records[1]["available_memory_before"] == 2964537344


def test_initial_headroom_and_pressure_are_separate_gates(manifest, tmp_path, monkeypatch):
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")
    ledger.records.append({})
    monkeypatch.setattr(quality, "available_memory", lambda: 7 * 1024**3)
    with pytest.raises(quality.EvaluationHalt, match="memory_headroom"):
        ledger.memory_checkpoint({"resident_before": []}, "before")


def test_expired_model_requires_cold_headroom_again(manifest, tmp_path, monkeypatch):
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")
    ledger.records.extend([{}, {}])
    monkeypatch.setattr(quality, "available_memory", lambda: 2964537344)
    ledger.memory_checkpoint({"resident_before": [quality.MODELS["generation"]]}, "before")
    with pytest.raises(quality.EvaluationHalt, match="memory_headroom"):
        ledger.memory_checkpoint({"resident_before": []}, "before")


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", ["before", "after"])
@pytest.mark.parametrize("pressure", [2, 4])
async def test_pressure_gate_stops_even_with_high_page_estimate(manifest, tmp_path, monkeypatch, checkpoint, pressure):
    import httpx
    from app.generation.service import GenerationService
    original_client = httpx.AsyncClient
    posts = []
    async def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": []})
        posts.append(request)
        return httpx.Response(200, json={"response": "합성 응답", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 10})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    monkeypatch.setattr(quality, "available_memory", lambda: 16 * 1024**3)
    values = iter([pressure] if checkpoint == "before" else [1, pressure])
    monkeypatch.setattr(quality, "memory_pressure_level", lambda: next(values))
    ledger = quality.CallLedger(manifest, tmp_path, "live_inference")  # synthetic HTTP only
    ledger.base_url = manifest["ollama_endpoint"]
    with pytest.raises(quality.EvaluationHalt, match="memory_pressure_warning_or_critical"):
        await ledger.call(GenerationService().call_ollama, "합성 입력")
    assert len(posts) == (1 if checkpoint == "after" else 0)
    assert ledger.records[0]["raw_response_available"] is (checkpoint == "after")
    assert ledger.records[0][f"memory_pressure_{checkpoint}"] == pressure


@pytest.mark.parametrize("raw,expected", [("1\n", 1), ("2\n", 2), ("4\n", 4), ("0", None), ("3", None), ("", None)])
def test_pressure_measurement_is_readonly_and_unknown_values_fail_closed(monkeypatch, raw, expected):
    monkeypatch.setattr(quality.sys, "platform", "darwin")
    def observe(command, **kwargs):
        assert command == ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]
        assert kwargs["check"] is True and kwargs["timeout"] == 5
        return SimpleNamespace(stdout=raw)
    monkeypatch.setattr(quality.subprocess, "run", observe)
    if expected is None:
        with pytest.raises(quality.EvaluationHalt, match="memory_pressure_measurement_unavailable"):
            quality.memory_pressure_level()
    else:
        assert quality.memory_pressure_level() == expected


def test_pressure_measurement_command_failure_stops(monkeypatch):
    monkeypatch.setattr(quality.sys, "platform", "darwin")
    def failed(*args, **kwargs):
        raise OSError("synthetic sysctl failure")
    monkeypatch.setattr(quality.subprocess, "run", failed)
    with pytest.raises(quality.EvaluationHalt, match="memory_pressure_measurement_unavailable"):
        quality.memory_pressure_level()


def test_review_packets_leave_human_values_empty_and_automatic_records_do_not_count(manifest, tmp_path):
    body = {"data": {"answer": "합성 응답"}}
    run = {"mode": "stored_replay", "manifest_sha256": manifest["manifest_sha256"],
           "case_results": [{"case_id": "Q16-MULTI", "response_sha256": quality.sha(body), "response": body}]}
    quality.write(tmp_path / "run.json", run)
    packets = tmp_path / "packets"
    quality.review_templates(tmp_path, packets)
    entry = quality.read(packets / "Q16-MULTI.json")
    assert entry["reviewer_id"] == ""
    assert entry["elapsed_seconds"] is None
    assert entry["decision"] is None
    assert entry["role_observations"]["qwen_feedback"] is None
    assert entry["observed_model_calls"] == []
    with pytest.raises(ValueError):
        quality.collect_review(tmp_path, entry)
    automatic = {**entry, "run_mode": "stored_replay", "reviewer_kind": "ai", "elapsed_seconds": 1,
                 "decision": "accept", "reviewer_id": "test-fixture"}
    (tmp_path / "human_reviews.jsonl").write_text(json.dumps(automatic) + "\n")
    result = quality.report(manifest, tmp_path)
    assert result["human_review"]["status"] == "unmeasured"
    assert result["human_review"]["invalid_or_synthetic_records"] == 1


@pytest.mark.asyncio
async def test_model_inventory_digest_change_stops_without_generation(manifest, tmp_path, monkeypatch):
    import httpx
    model = quality.MODELS["generation"]
    manifest["inventory_evidence"]["models"] = [{"name": model, "size": 10, "digest": "pinned"}]
    original_client = httpx.AsyncClient
    async def transport(request):
        assert request.method == "GET" and request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": model, "size": 10, "digest": "changed"}]})
    class SyntheticClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticClient)
    ledger = quality.CallLedger(manifest, tmp_path, "synthetic_test")
    ledger.base_url = manifest["ollama_endpoint"]
    with pytest.raises(quality.EvaluationHalt, match="digest_changed"):
        await ledger.inventory()
    assert ledger.records == []
