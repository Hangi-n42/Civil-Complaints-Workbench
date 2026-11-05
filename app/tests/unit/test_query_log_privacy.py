from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routers import generation, retrieval
from app.core.config import settings
from app.core.exceptions import GenerationError, RetrievalError
from app.core.logging import setup_logger
from app.generation.prompts import prompt_factory
from app.generation.service import GenerationService
from app.retrieval.service import RetrievalService


SYNTHETIC_PII = ("010-0000-0042", "log-check@example.invalid", "가상시 검증로 987 654동 321호")
QUERY = "공사 소음 조치 절차 문의 " + " / ".join(SYNTHETIC_PII)
CONTEXT = [{
    "doc_id": "DOC-LOG-1", "case_id": "CASE-LOG-1", "chunk_id": "CASE-LOG-1__chunk-0",
    "snippet": "접수 후 담당부서에서 사실관계를 확인합니다.", "score": 0.9,
}]


@pytest.fixture
def log_output(tmp_path, caplog, monkeypatch):
    path = tmp_path / "privacy.log"
    console = StringIO()
    with monkeypatch.context() as context:
        context.setattr("sys.stderr", console)
        logger = setup_logger(f"privacy-test-{uuid4().hex}", str(path))
    logger.addHandler(caplog.handler)
    monkeypatch.setattr(retrieval, "api_logger", logger)
    monkeypatch.setattr(generation, "api_logger", logger)
    monkeypatch.setattr(prompt_factory, "pipeline_logger", logger)
    yield logger, path, console
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if handler is not caplog.handler:
            handler.close()


def _assert_private_logs(log_output, caplog):
    logger, path, console = log_output
    for handler in logger.handlers:
        handler.flush()
    outputs = [caplog.text, path.read_text(encoding="utf-8"), console.getvalue()]
    assert all(outputs), "capture·파일·콘솔 모두 실제 로그를 검사해야 합니다."
    for output in outputs:
        assert QUERY not in output
        for value in SYNTHETIC_PII:
            assert value not in output
    return outputs[1]


class _Store:
    def __init__(self, failure=False):
        self.failure = failure
        self.queries = []

    def count(self, collection_name):
        return 1

    def query(self, **kwargs):
        self.queries.append(kwargs["query"])
        if self.failure:
            raise ValueError(f"store rejected {kwargs['query']}")
        return [dict(item) for item in CONTEXT]


def _retrieval(monkeypatch, logger, *, failure=False):
    service = RetrievalService()
    service.logger = logger
    store = _Store(failure)
    monkeypatch.setattr(service, "_get_vectorstore", lambda: store)
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "dense")

    async def grounded(query, results, top_k):
        return results[:top_k]

    monkeypatch.setattr(service, "_apply_grounding_filter", grounded)
    return service, store


def _generation(monkeypatch, logger, *, mode="success"):
    service = GenerationService()
    service.logger = logger
    monkeypatch.setattr(service, "_prepare_legal_context", lambda *a, **kw: (
        [], "", {"status": "no_candidates", "error": ""},
    ))
    calls = []

    async def call_ollama(prompt, **kwargs):
        calls.append(prompt)
        assert QUERY in prompt
        if mode == "error":
            raise ValueError(f"upstream echoed {QUERY}")
        if mode == "retry-exhausted" or (mode == "retry" and len(calls) == 1):
            raise GenerationError(f"parse rejected {QUERY}", code="PARSE_SCHEMA_MISMATCH")
        return json.dumps({
            "answer": "담당부서에서 사실관계를 확인하겠습니다.",
            "citations": [{
                key: value for key, value in {**CONTEXT[0], "relevance_score": 0.9}.items() if key != "score"
            }],
            "limitations": "추가 확인이 필요합니다.",
            "structured_output": {
                "summary": "소음 조치 절차 문의", "action_items": ["접수 확인", "담당부서 확인"],
                "request_segments": [],
            },
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "call_ollama", call_ollama)
    return service, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "store-error", "hybrid-error"])
async def test_search_logs_metadata_without_query_even_on_failure(log_output, caplog, monkeypatch, mode):
    service, store = _retrieval(monkeypatch, log_output[0], failure=mode == "store-error")
    if mode == "hybrid-error":
        class FailingHybrid:
            def search(self, *args, **kwargs):
                raise ValueError(QUERY)
        monkeypatch.setattr(service, "_get_hybrid", lambda: FailingHybrid())

    if mode == "store-error":
        with pytest.raises(RetrievalError):
            await service.search(QUERY, grounding_filter=False)
    else:
        result = await service.search(
            QUERY, grounding_filter=False, strategy="hybrid" if mode == "hybrid-error" else "dense",
        )
        assert result[0]["case_id"] == CONTEXT[0]["case_id"]

    assert store.queries == [QUERY]
    output = _assert_private_logs(log_output, caplog)
    assert f"query_length={len(QUERY)}" in output
    assert "top_k=5" in output
    if mode != "success":
        assert "error_type=ValueError" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "retry", "retry-exhausted", "error"])
async def test_qa_logs_metadata_without_query_on_retry_and_failure(log_output, caplog, monkeypatch, mode):
    service, calls = _generation(monkeypatch, log_output[0], mode=mode)
    if mode == "error":
        with pytest.raises(GenerationError):
            await service.generate_qa(QUERY, CONTEXT)
    else:
        result = await service.generate_qa(QUERY, CONTEXT)
        assert result["question"] == QUERY
        assert result["answer"]
        expected_retries = {"success": 0, "retry": 1, "retry-exhausted": 2}[mode]
        assert result["generation_metadata"]["parse_retry_count"] == expected_retries
    assert calls

    output = _assert_private_logs(log_output, caplog)
    assert f"query_length={len(QUERY)}" in output
    if mode.startswith("retry"):
        assert "PARSE_SCHEMA_MISMATCH" in output


@pytest.mark.parametrize("endpoint, mode", [
    ("search", "success"), ("search", "retrieval-error"),
    ("qa", "success"), ("qa", "retrieval-error"), ("qa", "generation-error"),
    ("qa/stream", "success"), ("qa/stream", "retrieval-error"), ("qa/stream", "generation-error"),
])
def test_direct_and_workbench_api_logs_keep_request_diagnostics_without_input(
    log_output, caplog, monkeypatch, endpoint, mode,
):
    search, _ = _retrieval(monkeypatch, log_output[0], failure=mode == "retrieval-error")
    qa, _ = _generation(monkeypatch, log_output[0], mode="error" if mode == "generation-error" else "success")
    monkeypatch.setattr(retrieval, "get_retrieval_service", lambda: search)
    monkeypatch.setattr(generation, "get_retrieval_service", lambda: search)
    monkeypatch.setattr(generation, "get_generation_service", lambda: qa)
    monkeypatch.setattr(settings, "ENABLE_CIVIL_LLM_RUBRIC", False)
    monkeypatch.setattr(settings, "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK", False)
    payload = {"request_id": "REQ-PRIVATE-42", "query": QUERY}
    if endpoint != "search":
        payload["complaint_id"] = "CMP-PRIVATE-42"
        payload["routing_hint"] = {
            "strategy_id": "topic_general_low_v1", "route_key": "general/low",
            "top_k": 1, "snippet_max_chars": 400, "chunk_policy": "compact",
        }

    response = TestClient(app).post(f"/api/v1/{endpoint}", json=payload)

    if endpoint.endswith("stream"):
        assert response.status_code == 200
        assert ("event: done" if mode == "success" else "event: error") in response.text
    else:
        assert response.status_code == (200 if mode == "success" else 503 if mode == "retrieval-error" else 500)
        assert response.json()["success"] is (mode == "success")
    output = _assert_private_logs(log_output, caplog)
    assert "request_id=REQ-PRIVATE-42" in output
    assert "latency_ms=" in output
    if mode != "success":
        assert "error_code=" in output


@pytest.mark.parametrize("include_request_id", [True, False])
def test_unhandled_stream_exception_does_not_log_traceback_input(log_output, caplog, monkeypatch, include_request_id):
    async def fail(*args, **kwargs):
        raise ValueError(QUERY)
    monkeypatch.setattr(generation, "_generate_qa", fail)

    payload = {"query": QUERY}
    if include_request_id:
        payload["request_id"] = "REQ-PRIVATE-STREAM-42"
    response = TestClient(app).post("/api/v1/qa/stream", json=payload)

    assert "event: error" in response.text
    error = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
    assert error["request_id"]
    output = _assert_private_logs(log_output, caplog)
    assert "error_type=ValueError" in output
    assert f"request_id={error['request_id']}" in output


@pytest.mark.asyncio
async def test_autoretrieve_trace_logs_lengths_without_derived_query(log_output, caplog, monkeypatch):
    search, store = _retrieval(monkeypatch, log_output[0])
    monkeypatch.setattr(prompt_factory, "get_retrieval_service", lambda: search)
    service = GenerationService()
    service.logger = log_output[0]

    prompt, context, trace = await service.build_rag_prompt_from_record_autoretrieve(
        {"case_id": "CASE-SOURCE", "query": QUERY, "consulting_content": QUERY},
    )

    assert QUERY in prompt
    assert context
    assert trace["derived_query"] == QUERY
    assert store.queries
    output = _assert_private_logs(log_output, caplog)
    assert "derived_query_length=" in output
    assert "search_query_length=" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("error_class", [
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPError, ValueError,
])
async def test_ollama_transport_errors_do_not_log_echoed_prompt(log_output, caplog, monkeypatch, error_class):
    class FailingClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            raise error_class(QUERY)
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)
    service = GenerationService()
    service.logger = log_output[0]

    with pytest.raises(GenerationError):
        await service.call_ollama(QUERY)

    output = _assert_private_logs(log_output, caplog)
    assert '"stage": "connect"' in output
    assert '"error_code":' in output


@pytest.mark.asyncio
@pytest.mark.parametrize("method, factory_method, error_class", [
    ("build_rag_prompt", "build", ValueError),
    ("build_rag_prompt_from_record", "build_from_dataset_record", ValueError),
    ("build_rag_prompt_from_record_autoretrieve", "build_from_dataset_record_autoretrieve", ValueError),
    ("build_rag_prompt_from_record_autoretrieve", "build_from_dataset_record_autoretrieve", RetrievalError),
])
async def test_prompt_failure_logs_do_not_echo_inputs(log_output, caplog, monkeypatch, method, factory_method, error_class):
    def fail(*args, **kwargs):
        raise error_class(QUERY)
    async def async_fail(*args, **kwargs):
        fail()
    monkeypatch.setattr(
        prompt_factory.PromptFactory, factory_method,
        async_fail if factory_method.endswith("autoretrieve") else fail,
    )
    service = GenerationService()
    service.logger = log_output[0]

    with pytest.raises((GenerationError, RetrievalError)):
        if method == "build_rag_prompt":
            await service.build_rag_prompt(QUERY, CONTEXT)
        elif method.endswith("autoretrieve"):
            await service.build_rag_prompt_from_record_autoretrieve({"query": QUERY})
        else:
            await service.build_rag_prompt_from_record({"query": QUERY}, CONTEXT)

    output = _assert_private_logs(log_output, caplog)
    assert f"error_type={error_class.__name__}" in output


@pytest.mark.parametrize("stage", ["prepare", "validate"])
def test_legal_grounding_warnings_do_not_echo_input(log_output, caplog, monkeypatch, stage):
    from app.generation import service as generation_module

    def fail(*args, **kwargs):
        raise ValueError(QUERY)
    service = GenerationService()
    service.logger = log_output[0]
    if stage == "prepare":
        monkeypatch.setattr(settings, "ENABLE_LEGAL_CITATIONS", True)
        monkeypatch.setattr(service, "_normalize_generation_signals", fail)
        assert service._prepare_legal_context(QUERY)[2]["status"] == "error"
    else:
        monkeypatch.setattr(generation_module, "ground_legal_citations", fail)
        status = {"status": "grounded", "error": ""}
        service._apply_legal_grounding({"answer": QUERY}, [], status)
        assert status["status"] == "error"

    assert "error_type=ValueError" in _assert_private_logs(log_output, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["rubric", "revision"])
async def test_qa_evaluation_and_revision_warnings_do_not_echo_input(log_output, caplog, monkeypatch, stage):
    async def fail(**kwargs):
        raise ValueError(QUERY)
    request = generation.QARequest(query=QUERY, request_id="REQ-PRIVATE-EVAL-42", complaint_id="CMP-42")
    service = GenerationService()
    service.logger = log_output[0]
    if stage == "rubric":
        monkeypatch.setattr(settings, "ENABLE_CIVIL_LLM_RUBRIC", True)
        monkeypatch.setattr(generation, "get_civil_llm_rubric_evaluator", lambda: SimpleNamespace(evaluate=fail))
        result = await generation._attach_civil_llm_rubric(
            unified_payload={}, request=request, references=CONTEXT, generation_service=service,
        )
        assert result["judge_status"] == "error"
    else:
        monkeypatch.setattr(settings, "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK", True)
        monkeypatch.setattr(settings, "PROMETHEUS_RUBRIC_MAX_REGENERATION_ATTEMPTS", 1)
        monkeypatch.setattr(generation, "select_low_score_items", lambda *a, **kw: [{"item_id": "q1"}])
        monkeypatch.setattr(generation, "get_prometheus_feedback_engine", lambda: SimpleNamespace(build_feedback=fail))
        result = await generation._maybe_apply_prometheus_revision(
            unified_payload={"generation_metadata": {"civil_llm_rubric": {}}},
            request=request, generation_service=service, context=CONTEXT, current_citations=[],
            retrieval_elapsed_ms=1, generation_elapsed_ms=1, routing_trace={},
            strategy_id="topic_general_low_v1", route_key="general/low", query_signals=None,
        )
        assert result is None

    output = _assert_private_logs(log_output, caplog)
    assert "error_type=ValueError" in output
    assert "request_id=REQ-PRIVATE-EVAL-42" in output
