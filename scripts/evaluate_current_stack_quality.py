"""고정 근거 QA 기준선의 준비·실행·사람 검토·보고. 기본 실행은 추론하지 않는다."""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = ROOT / "data/evaluation/current_stack_quality_v1.json"
MODELS = {"generation": "gemma4:31b-it-q4_K_M", "feedback": "qwen3.8:27b-q4_K_M"}
PER_CASE_CALLS, TOTAL_CALLS = 14, 28
SETTINGS = {
    "OLLAMA_MODEL": MODELS["generation"], "CIVIL_LLM_RUBRIC_MODEL": MODELS["generation"],
    "PROMETHEUS_FEEDBACK_MODEL": MODELS["feedback"], "PROMETHEUS_REVISION_MODEL": MODELS["generation"],
    "GENERATION_NUM_PREDICT": 1536, "GENERATION_NUM_CTX": 8192, "OLLAMA_TIMEOUT": 180,
    "ENABLE_CIVIL_LLM_RUBRIC": True, "CIVIL_LLM_RUBRIC_USE_LLM_JUDGE": True,
    "CIVIL_LLM_RUBRIC_VERSION": "civil_llm_rubric_q0_q7_v1.0",
    "CIVIL_LLM_RUBRIC_MAX_CONTEXTS": 5, "CIVIL_LLM_RUBRIC_TEMPERATURE": 0.0,
    "CIVIL_LLM_RUBRIC_JUDGE_PROMPT_VERSION": "judge_prompt_2026_09_29_citation_claims",
    "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK": True, "PROMETHEUS_RUBRIC_MAX_REGENERATION_ATTEMPTS": 1,
    "PROMETHEUS_RUBRIC_TRIGGER_MAX_CHOICE": 2.0, "PROMETHEUS_RUBRIC_TEMPERATURE": 0.0,
    "ENABLE_LEGAL_CITATIONS": True,
    "REQUEST_SEGMENT_LLM_MODE": "off", "REQUEST_SEGMENT_LLM_PROVIDER": "none",
}
SOURCE_FILES = [
    "app/core/config.py", "app/api/routers/generation.py", "app/api/schemas/generation.py",
    "app/api/schemas/retrieval.py", "app/generation/service.py", "app/generation/context_mapper.py",
    "app/generation/prompts/prompt_factory.py", "app/generation/grounding_quality.py",
    "app/generation/citation/citation_mapper.py", "app/generation/citation/legal_citation.py",
    "app/generation/normalization/response_normalizer.py", "app/generation/validators/qa_response_validator.py",
    "app/generation/parsing/json_utils.py", "app/retrieval/law_article_store.py",
    "app/structuring/legal_dictionary.py", "data/laws/law_dictionary.json", "data/laws/busan_ordinances.json",
    "app/evaluation/civil_llm_rubric.py", "app/evaluation/prometheus_feedback.py",
    "scripts/evaluate_current_stack_quality.py",
]


class EvaluationHalt(BaseException):
    """제품의 soft fallback/retry가 평가 예산·접근 차단을 삼키지 못하게 한다."""


def sha(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_synced(path, value):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_journal(path):
    raw = Path(path).read_bytes() if Path(path).exists() else b""
    partial_tail = bool(raw) and not raw.endswith(b"\n")
    lines = raw.splitlines()
    return [json.loads(line) for line in (lines[:-1] if partial_tail else lines)], partial_tail


def source_hashes():
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def product_code_hash():
    return sha({str(path.relative_to(ROOT)): sha(path.read_bytes())
                for path in sorted((ROOT / "app").rglob("*.py"))
                if "tests" not in path.relative_to(ROOT / "app").parts})


def runtime_packages():
    return {name: version(name) for name in ("httpx", "fastapi", "starlette", "pydantic", "anyio")}


@contextmanager
def isolated_runtime(*, endpoint=None):
    """기존 DB 경로를 사용하지 않으며, 준비/재생은 소켓 연결도 차단한다."""
    original_env = dict(os.environ)
    original_connect, original_create = socket.socket.connect, socket.create_connection
    with tempfile.TemporaryDirectory(prefix="civil-quality-") as temp:
        os.environ.update({
            "CHROMA_DB_PATH": str(Path(temp) / "chroma"),
            "COMPLAINT_INTELLIGENCE_DB_PATH": str(Path(temp) / "intelligence.db"),
            "COMPLAINT_INTELLIGENCE_SCHEDULER_ENABLED": "false", "PUBLIC_INSIGHT_LLM_PROVIDER": "fake",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "ANONYMIZED_TELEMETRY": "False",
        })
        def blocked(*args, **kwargs):
            raise EvaluationHalt("network_disabled")
        if endpoint is None:
            socket.socket.connect = blocked
            socket.create_connection = blocked
        else:
            port = urlsplit(endpoint).port
            def loopback_only(sock, address):
                if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1") or address[1] != port:
                    raise EvaluationHalt("network_out_of_scope")
                return original_connect(sock, address)
            socket.socket.connect = loopback_only
        try:
            yield Path(temp)
        finally:
            socket.socket.connect, socket.create_connection = original_connect, original_create
            os.environ.clear()
            os.environ.update(original_env)


def prepare(output: Path, inventory: Path | None = None):
    data = read(CASES)
    with isolated_runtime():
        from app.api.schemas.generation import QARequest
        from app.core.config import settings
        from app.generation.context_mapper import DEFAULT_POLICY, map_retrieval_to_qa_context
        from app.generation.service import GenerationService, local_ollama_url
        from app.retrieval.law_article_store import LawArticleStore

        profile = {key: getattr(settings, key) for key in SETTINGS}
        if profile != SETTINGS:
            raise ValueError("현재 설정이 고정 프로필과 다릅니다. 새 manifest 검토가 필요합니다.")
        inputs = []
        service = GenerationService()
        endpoint = local_ollama_url(service.ollama_url)
        for case in data["cases"]:
            request = QARequest.model_validate(case["request"])
            context, trace = map_retrieval_to_qa_context(
                retrieval_results=[item.model_dump() for item in request.search_results],
                top_k=len(request.search_results), policy=None,
            )
            lengths = {}
            for mode in ("default", "compact"):
                prompt = asyncio.run(service.build_rag_prompt(
                    request.query, context, routing_trace=request.routing_trace.model_dump(), mode=mode,
                ))
                prompt = service._append_output_contract(prompt)
                lengths[mode] = {"chars": len(prompt), "utf8_bytes": len(prompt.encode()), "sha256": sha(prompt)}
            inputs.append({"case_id": case["case_id"], "request": case["request"], "provenance": case["source"],
                           "request_sha256": sha(case["request"]),
                           "context_trace": trace, "base_generation_prompts": lengths})
            def no_embedding(*args, **kwargs):
                raise EvaluationHalt("legal_database_or_embedding_required")
            try:
                with patch.object(LawArticleStore, "_get_collection", no_embedding), \
                     patch.object(LawArticleStore, "_get_model", no_embedding):
                    _articles, _extra, legal = service._prepare_legal_context(request.query)
                inputs[-1]["legal_preflight"] = legal
            except EvaluationHalt as exc:
                inputs[-1]["legal_preflight"] = {"status": "blocked", "reason": str(exc)}
    inventory_data = read(inventory) if inventory else None
    limits = [{"role": role, "num_predict": output_tokens, "num_ctx": 8192,
               "system_reservation_tokens": 1024, "input_token_estimator": "ceil(prompt_chars / 2); estimate only",
               "enforced_max_prompt_chars": (8192 - output_tokens - 1024) * 2,
               "max_utf8_bytes_at_character_guard": (8192 - output_tokens - 1024) * 8}
              for role, output_tokens in [("generation_default_or_compact", 1536), ("rubric_q2_q6_q0", 192),
                                          ("rubric_q3_q4_q5", 1536), ("rubric_q1_q7", 640),
                                          ("qwen_feedback", 1536), ("gemma_revision", 1536)]]
    manifest = {
        "schema_version": "current_stack_quality_manifest_v2", "state": "prepared_not_executed",
        "scope": "현재 제품 POST /api/v1/qa의 고정 근거 기준선; 검색/임베딩/전체 서비스 품질 평가는 제외",
        "exposure": data["exposure"], "cases_sha256": sha(data), "settings": profile,
        "ollama_endpoint": endpoint,
        "ollama_endpoint_raw": settings.OLLAMA_BASE_URL,
        "explicit_profile_override": {"PROMETHEUS_REVISION_MODEL": {"product_default": "", "evaluation": MODELS["generation"],
                                                                  "reason": "canonical local profile와 같은 명시값. 실효 모델이 같아도 원시설정 일치를 요구한다."}},
        "source_hashes": source_hashes(), "product_python_tree_sha256": product_code_hash(),
        "runtime_packages": runtime_packages(), "inputs": inputs,
        "base_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
        "python_version": sys.version,
        "api_context_policy": DEFAULT_POLICY, "model_prompt_limits": limits,
        "prompt_length_limit_note": "base prompt만 실제 렌더 길이. 법령/평가/피드백/재작성의 동적 내용은 실행 전 guard로 제한하며 절단하지 않는다. 문자 추정은 토큰화 증명이 아니며, 실제 usage/length 검사도 서버가 입력을 절단하지 않았다는 증명은 아니다.",
        "call_budget": {"per_case": PER_CASE_CALLS, "total": TOTAL_CALLS, "cases": 2,
                        "normal_path": {"generation_including_parse_retry": 2, "initial_rubric_groups": 5,
                                        "feedback": 1, "revision": 1, "reevaluation_without_q2_cache": 5},
                        "failure_path": "Q2 cache 성공 시 최대13회, 초기 Q2 파싱 실패로 cache가 없으면14회. Python TypeError 호환 재호출 등 예외 경로가 더 요청해도15번째 연결 전 중단한다.",
                        "stages": ["Q16-MULTI: 최대14콜 후 결과/자원 gate", "Q16-SCHEDULE: 최대14콜"],
                        "feedback": "제품의 조건부 발동 유지; Qwen 실행을 강제하지 않음"},
        "readonly_http_budget": {"tags": 1, "ps": 57, "note": "모델 요청28회와 별도의 최대58회 loopback GET. 조회 실패 시 중단."},
        "execution_limits": {"call_timeout_seconds": 180, "wall_seconds": 2700,
                             "min_available_memory_bytes": 8 * 1024**3,
                             "memory_guard_version": "cold_headroom_and_macos_pressure_v2",
                             "memory_measurement": "가용량은 macOS vm_stat free+inactive+speculative 또는 Linux MemAvailable. 검증된 resident_before가 비어 있는 모든 호출은8GiB 필요. macOS 매 호출 전후 sysctl kern.memorystatus_vm_pressure_level=1(normal) 필요; 자기 모델 상주 시 가용량은 기록만. non-macOS는 매 checkpoint8GiB. 연속 측정/OOM 방지 보장 아님",
                             "concurrency": 1, "model_unload_or_keepalive_mutation": False,
                             "stop": ["승인/입력/설정/코드 불일치", "숨은 DB/임베딩 진입", "모델 호출 오류",
                                      "length 종료/usage 누락/입출력 ctx 초과", "14/28콜 또는45분 초과",
                                      "모델 미상주 호출 전 여유8GiB 미만/메모리 측정 불가", "macOS pressure warning/critical/알 수 없는 값(non-macOS는 매 checkpoint8GiB)", "다른 작업 모델 점유/복수 모델 상주"],
                             "model_transition": "자기 실행 모델 한 개만 점유하면 제품처럼 다음 모델을 직렬 요청해 Ollama native eviction 사용. 자연만료 추가 대기·keep_alive 변경·강제 unload 없음. 호출 전후 상주 점검."},
        "inventory_evidence": {"source_sha256": sha(inventory_data) if inventory_data else None,
                               "physical_memory_bytes": int(inventory_data["memory"]) if inventory_data else None,
                               "resident_models_at_readonly_check": inventory_data.get("ps") if inventory_data else None,
                               "models": [row for row in (inventory_data or {}).get("tags", []) if row["name"] in MODELS.values()],
                               "simultaneous_residency_allowed": False,
                               "note": "루트의 읽기 전용 inventory 기록이며 실행 직전 재확인이 필요하다. 모델 로드/추론 아님."},
        "retained_failures": [{"metric": "duplicate_merge_real_holdout.recall", "value": 0.9333,
                               "minimum": 0.99, "status": "failed", "new_measurement": False,
                               "source_test": "app/tests/unit/test_duplicate_merger_real_holdout.py:31"}],
        "historical_results": {"reuse": "not_used", "reason": "과거 모델·정확한 입력·완성 prompt·설정·코드·모델 digest 모두 일치할 때만 재생 허용. 기존20/13/6호출 결과를 현재 생성 효과로 재사용하지 않음."},
        "human_review": {"status": "unmeasured", "minimum_reviewed_cases": 2,
                         "time": "사용자 제출 사람 검토 시간만 집계. 자동 점수/편집량/AI 검수는 사람 시간으로 대체하지 않음.",
                         "burden_reduction": "수작업 기준선이 없으면 확인 불가"},
        "execution_approval": "별도 명시 승인 파일의 manifest hash·정확한 output_directory 일치 및 파일 옆 exclusive single-use claim 후만 live 실행. 0콜/중단/실패도 소비되며 재실행은 새 승인 필요",
        "completion": "실제 추론 및 사람 검토 전 이슈 #16 미완료",
    }
    manifest["manifest_sha256"] = sha(manifest)
    write(output, manifest)
    return manifest


def validate_manifest(manifest):
    expected = dict(manifest)
    fingerprint = expected.pop("manifest_sha256", None)
    if (sha(expected) != fingerprint or manifest["source_hashes"] != source_hashes()
        or manifest["product_python_tree_sha256"] != product_code_hash()
        or manifest["runtime_packages"] != runtime_packages()
        or manifest["python_version"] != sys.version):
        raise EvaluationHalt("manifest_or_code_changed")
    if manifest["cases_sha256"] != sha(read(CASES)):
        raise EvaluationHalt("frozen_inputs_changed")
    if [row["request"] for row in manifest["inputs"]] != [row["request"] for row in read(CASES)["cases"]]:
        raise EvaluationHalt("manifest_requests_differ_from_frozen_inputs")


def validate_approval(manifest, approval_path, output):
    if approval_path is None:
        raise EvaluationHalt("live_execution_not_approved")
    approval = read(approval_path)
    expiry = datetime.fromisoformat(approval["expires_at"])
    if (approval.get("approved") is not True or expiry.tzinfo is None
        or expiry <= datetime.now(timezone.utc)
        or approval.get("manifest_sha256") != manifest["manifest_sha256"]
        or approval.get("max_model_calls") != TOTAL_CALLS
        or approval.get("output_directory") != str(output.resolve())):
        raise EvaluationHalt("live_approval_invalid_or_expired")
    return approval


def claim_approval(manifest, approval_path, output):
    approval = validate_approval(manifest, approval_path, output)
    claim_path = approval_path.resolve().with_name(approval_path.name + ".claim.json")
    claim = {"state": "consumed_no_reuse", "approval_sha256": sha(approval),
             "manifest_sha256": manifest["manifest_sha256"], "output_directory": str(output.resolve()),
             "claimed_at": datetime.now(timezone.utc).isoformat()}
    try:
        with claim_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(claim, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise EvaluationHalt("live_approval_already_consumed") from exc
    return {"path": str(claim_path), **claim}


def available_memory():
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
            page = int(re.search(r"page size of (\d+) bytes", result)[1])
            return page * sum(int(re.search(rf"Pages {name}:\s+(\d+)", result)[1])
                              for name in ("free", "inactive", "speculative"))
        path = Path("/proc/meminfo")
        if path.exists():
            return int(re.search(r"MemAvailable:\s+(\d+)", path.read_text())[1]) * 1024
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, IndexError) as exc:
        raise EvaluationHalt("memory_measurement_unavailable") from exc
    raise EvaluationHalt("memory_measurement_unavailable")


def memory_pressure_level():
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                                capture_output=True, text=True, check=True, timeout=5)
        level = int(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise EvaluationHalt("memory_pressure_measurement_unavailable") from exc
    if level not in (1, 2, 4):
        raise EvaluationHalt("memory_pressure_measurement_unavailable")
    return level


class CallLedger:
    def __init__(self, manifest, output, mode, replay=()):
        self.manifest, self.output, self.mode = manifest, output, mode
        self.records, self.owned_models = [], set()
        self.case_id, self.case_calls = "", 0
        self.started = time.monotonic()
        self.replay = iter(replay)
        self.base_url = None
        self.native_send = None
        self.model_digests = {row["name"]: row.get("digest") for row in manifest["inventory_evidence"]["models"]}

    def memory_checkpoint(self, record, checkpoint):
        available = record[f"available_memory_{checkpoint}"] = available_memory()
        pressure = record[f"memory_pressure_{checkpoint}"] = memory_pressure_level()
        if pressure is not None and pressure != 1:
            raise EvaluationHalt("memory_pressure_warning_or_critical")
        if pressure is None or (checkpoint == "before" and not record["resident_before"]):
            if available < self.manifest["execution_limits"]["min_available_memory_bytes"]:
                raise EvaluationHalt("memory_headroom_below_8gib")

    async def inventory(self):
        import httpx
        async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False) as client:
            response = await client.get(self.base_url + "/api/tags")
            response.raise_for_status()
        installed = {row["name"]: row for row in response.json().get("models", [])}
        for expected in self.manifest["inventory_evidence"]["models"]:
            actual = installed.get(expected["name"])
            if not expected.get("digest"):
                raise EvaluationHalt("approved_model_identity_unverified")
            if not actual or actual["size"] != expected["size"] or not actual.get("digest"):
                raise EvaluationHalt("installed_model_identity_changed")
            if expected.get("digest") and expected["digest"] != actual["digest"]:
                raise EvaluationHalt("installed_model_digest_changed")
            self.model_digests[expected["name"]] = actual["digest"]
        if set(self.model_digests) != set(MODELS.values()):
            raise EvaluationHalt("model_inventory_missing")
        return self.model_digests

    async def residents(self):
        import httpx
        async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False) as client:
            response = await client.get(self.base_url + "/api/ps")
            response.raise_for_status()
        names = [row.get("name", row.get("model")) for row in response.json().get("models", [])]
        if len(names) > 1 or any(name not in self.owned_models for name in names):
            raise EvaluationHalt("foreign_or_multiple_resident_models")
        return names

    async def call(self, native, prompt, temperature=0.7, response_schema=None, **options):
        import httpx
        if self.case_calls >= PER_CASE_CALLS or len(self.records) >= TOTAL_CALLS:
            raise EvaluationHalt("model_call_budget_exhausted")
        if time.monotonic() - self.started >= self.manifest["execution_limits"]["wall_seconds"]:
            raise EvaluationHalt("wall_budget_exhausted")
        model = options.get("model") or MODELS["generation"]
        predict, ctx = options.get("num_predict", 1536), options.get("num_ctx", 8192)
        if model not in MODELS.values() or predict not in (192, 640, 1536) or ctx != 8192:
            raise EvaluationHalt("unapproved_model_or_budget")
        if math.ceil(len(prompt) / 2) + predict + 1024 > ctx:
            raise EvaluationHalt("estimated_prompt_budget_exceeded")
        if options.get("think", False) is not False:
            raise EvaluationHalt("thinking_option_changed")
        timeout = options.get("timeout") if options.get("timeout") is not None else 180
        if timeout != 180 or set(options) - {"model", "num_predict", "num_ctx", "think", "timeout"}:
            raise EvaluationHalt("unapproved_timeout_or_call_options")
        url = (self.base_url or self.manifest["ollama_endpoint"]) + "/api/generate"
        payload = {"model": model, "prompt": prompt, "stream": False, "format": response_schema or "json",
                   "options": {"temperature": temperature, "num_predict": predict, "num_ctx": ctx}, "think": False}
        expected_wire = {"method": "POST", "url": url,
                         "body_base64": base64.b64encode(httpx.Request("POST", url, json=payload).content).decode(),
                         "timeout": {name: float(timeout) for name in ("connect", "read", "write", "pool")}}
        request = {"model": model, "model_digest": self.model_digests.get(model), "wire": expected_wire}
        fingerprint = sha({"manifest": self.manifest["manifest_sha256"], "case": self.case_id, "request": request})
        record = {"call_id": f"call-{len(self.records) + 1:04d}",
                  "case_id": self.case_id, "mode": self.mode, "fingerprint": fingerprint,
                  "manifest_sha256": self.manifest["manifest_sha256"], "request": request, "sent_to_model": False}
        self.case_calls += 1
        self.records.append(record)
        began = time.monotonic()
        original_send = self.native_send or httpx.AsyncClient.send
        async def tee(client, actual, *args, **kwargs):
            wire = {"method": actual.method, "url": str(actual.url),
                    "body_base64": base64.b64encode(actual.content).decode(),
                    "timeout": {key: float(value) for key, value in actual.extensions.get("timeout", {}).items()}}
            record["wire_request"] = wire
            if wire != expected_wire:
                raise EvaluationHalt("native_wire_request_mismatch")
            if record.get("transport_dispatched"):
                raise EvaluationHalt("unbudgeted_native_http_retry")
            append_synced(self.output / "reservations.jsonl", {
                "call_id": record["call_id"], "case_id": self.case_id, "mode": self.mode,
                "fingerprint": fingerprint, "manifest_sha256": self.manifest["manifest_sha256"],
                "request": request, "wire_request": wire, "state": "reserved_before_transport",
                "server_completion_unknown": True, "reserved_at": datetime.now(timezone.utc).isoformat(),
            })
            record["transport_dispatched"] = True
            record["sent_to_model"] = self.mode == "live_inference"
            try:
                response = await original_send(client, actual, *args, **kwargs)
                body = await response.aread()
                record["http_response"] = {"status": response.status_code,
                                           "headers": list(response.headers.multi_items()),
                                           "body_base64": base64.b64encode(body).decode()}
                return response
            except Exception as exc:
                record["transport_error_type"] = type(exc).__name__
                raise
        try:
            if self.mode == "stored_replay":
                if not request["model_digest"]:
                    raise EvaluationHalt("replay_model_identity_unverified")
                saved = next(self.replay)
                if (saved.get("mode") != "live_inference" or saved["fingerprint"] != fingerprint
                    or saved.get("wire_request") != expected_wire or saved.get("http_response", {}).get("status") != 200):
                    raise EvaluationHalt("replay_request_or_provenance_mismatch")
                raw = json.loads(base64.b64decode(saved["http_response"]["body_base64"]))
                result = saved["response"]
                parsed = {"text": str(raw.get("response", "")).strip(),
                          **{key: raw.get(key) for key in ("done", "done_reason", "prompt_eval_count", "eval_count", "total_duration")}}
                if parsed != result:
                    raise EvaluationHalt("replay_raw_response_mismatch")
                record["wire_request"], record["http_response"] = saved["wire_request"], saved["http_response"]
            else:
                if self.mode == "live_inference":
                    record["resident_before"] = await self.residents()
                    self.memory_checkpoint(record, "before")
                    self.owned_models.add(model)
                with patch.object(httpx.AsyncClient, "send", tee):
                    result = await native(prompt, temperature, response_schema, **options,
                                          return_metadata=True, local_only=True)
                record["response"] = result
                if self.mode == "live_inference":
                    if "http_response" not in record:
                        raise EvaluationHalt("native_transport_not_observed")
                    record["resident_after"] = await self.residents()
                    self.memory_checkpoint(record, "after")
            record["response"] = result
            if result.get("done_reason") == "length":
                raise EvaluationHalt("output_truncated_length")
            if result.get("done") is not True:
                raise EvaluationHalt("upstream_response_incomplete")
            if any(type(result.get(key)) is not int or result[key] < 0 for key in ("prompt_eval_count", "eval_count")):
                raise EvaluationHalt("token_usage_unavailable")
            if result["eval_count"] > predict:
                raise EvaluationHalt("observed_output_budget_exceeded")
            if result["prompt_eval_count"] + predict > ctx:
                raise EvaluationHalt("observed_context_budget_exceeded")
            return result["text"]
        except EvaluationHalt as exc:
            record["stop_reason"] = str(exc)
            raise
        except Exception as exc:
            record["error_code"] = getattr(exc, "code", type(exc).__name__)
            record["error_details"] = getattr(exc, "details", {})
            raw_body = base64.b64decode(record.get("http_response", {}).get("body_base64", "")).decode(errors="replace").lower()
            if "out of memory" in raw_body or re.search(r"\boom\b", raw_body):
                record["upstream_reported_category"] = "oom"
            elif "context" in raw_body and any(word in raw_body for word in ("exceed", "too long", "maximum")):
                record["upstream_reported_category"] = "context_limit"
            elif "http_response" not in record:
                record["upstream_reported_category"] = "response_absent"
            raise EvaluationHalt(f"model_error:{record['error_code']}") from exc
        finally:
            record["server_completion_unknown"] = record["sent_to_model"] and "http_response" not in record
            record["raw_response_available"] = "http_response" in record
            record["elapsed_seconds"] = time.monotonic() - began if self.mode == "live_inference" else None
            append_synced(self.output / "calls.jsonl", record)


async def run_evaluation(manifest, output, *, mode="stored_replay", approval=None, replay=()):
    validate_manifest(manifest)
    claim = claim_approval(manifest, approval, output) if mode == "live_inference" else None
    output.mkdir(parents=True, exist_ok=False)
    write(output / "manifest.json", manifest)
    ledger = CallLedger(manifest, output, mode, replay)
    receipt = {"mode": mode, "manifest_sha256": manifest["manifest_sha256"], "status": "incomplete",
               "case_results": [], "new_model_calls": 0, "human_review": "unmeasured",
               "started_at": datetime.now(timezone.utc).isoformat(), "approval_claim": claim}
    write(output / "run.json", receipt)
    def scope_stop(*args, **kwargs):
        raise EvaluationHalt("database_or_embedding_access_out_of_scope")
    try:
        with isolated_runtime(endpoint=manifest["ollama_endpoint"] if mode == "live_inference" else None):
            import httpx
            from fastapi import FastAPI
            from app.api.routers import generation
            from app.core.config import settings
            from app.generation.service import GenerationService, local_ollama_url
            from app.retrieval.law_article_store import LawArticleStore
            from app.retrieval.vectorstores.chroma_store import ChromaVectorStore

            if {key: getattr(settings, key) for key in SETTINGS} != manifest["settings"]:
                raise EvaluationHalt("runtime_settings_changed")
            service = GenerationService()
            native = service.call_ollama
            ledger.base_url = local_ollama_url(service.ollama_url)
            if ledger.base_url != manifest["ollama_endpoint"] or settings.OLLAMA_BASE_URL != manifest["ollama_endpoint_raw"]:
                raise EvaluationHalt("ollama_endpoint_changed")
            if mode == "live_inference":
                await ledger.residents()
                receipt["model_digests"] = await ledger.inventory()
            async def observed(prompt, temperature=0.7, response_schema=None, **kwargs):
                return await ledger.call(native, prompt, temperature, response_schema, **kwargs)
            service.call_ollama = observed
            app = FastAPI()
            app.include_router(generation.router)
            ledger.native_send = httpx.AsyncClient.send
            async def guarded_send(client, request, *args, **kwargs):
                if isinstance(client._transport, httpx.ASGITransport):
                    return await ledger.native_send(client, request, *args, **kwargs)
                if request.method == "GET" and str(request.url) in (ledger.base_url + "/api/tags", ledger.base_url + "/api/ps"):
                    return await ledger.native_send(client, request, *args, **kwargs)
                raise EvaluationHalt("unobserved_http_request_out_of_scope")
            with patch.object(generation, "get_generation_service", lambda: service), \
                 patch.object(httpx.AsyncClient, "send", guarded_send), \
                 patch.object(ChromaVectorStore, "_get_client", scope_stop), \
                 patch.object(LawArticleStore, "_get_collection", scope_stop), \
                 patch.object(LawArticleStore, "_get_model", scope_stop):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://quality") as client:
                    for case in read(CASES)["cases"]:
                        ledger.case_id, ledger.case_calls = case["case_id"], 0
                        case_started = time.monotonic()
                        response = await asyncio.wait_for(client.post("/api/v1/qa", json=case["request"]),
                                                         timeout=max(0.01, manifest["execution_limits"]["wall_seconds"] - (time.monotonic() - ledger.started)))
                        body = response.json()
                        receipt["case_results"].append({"case_id": case["case_id"], "http_status": response.status_code,
                                                        "response_sha256": sha(body), "response": body,
                                                        "instrumented_api_elapsed_seconds": time.monotonic() - case_started if mode == "live_inference" else None})
                        write(output / "run.json", receipt)
                        if response.status_code != 200:
                            raise EvaluationHalt("product_api_error")
            receipt["status"] = "completed_fixed_evidence_api"
    except (EvaluationHalt, TimeoutError) as exc:
        receipt["stop_reason"] = str(exc) or "wall_timeout_server_completion_unknown"
    except Exception as exc:
        receipt["stop_reason"] = f"runtime_error:{type(exc).__name__}"
    finally:
        receipt["new_model_calls"] = sum(row["sent_to_model"] for row in ledger.records)
        receipt["processed_calls"] = len(ledger.records)
        write(output / "run.json", receipt)
    return receipt


def review_matches(run, entry):
    case = next((row for row in run["case_results"] if row["case_id"] == entry.get("case_id")), None)
    return not (case is None or sha(case["response"]) != case["response_sha256"]
        or entry.get("response_sha256") != case["response_sha256"]
        or entry.get("reviewer_kind") != "human" or entry.get("source") != "user_submission"
        or entry.get("time_kind") != "reviewer_measured_elapsed"
        or not isinstance(entry.get("reviewer_id"), str) or not entry["reviewer_id"].strip()
        or entry.get("decision") not in ("accept", "edit", "reject")
        or isinstance(entry.get("elapsed_seconds"), bool)
        or not isinstance(entry.get("elapsed_seconds"), (int, float))
        or not math.isfinite(entry["elapsed_seconds"]) or entry["elapsed_seconds"] < 0)


def collect_review(run_dir, entry):
    run = read(run_dir / "run.json")
    if not review_matches(run, entry):
        raise ValueError("사람 제출·원출력 hash·판정·측정시간이 필요합니다. 추정값을 채우지 마세요.")
    entry = {**entry, "run_mode": run["mode"], "time_provenance": "reviewer_reported_seconds"}
    with (run_dir / "human_reviews.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def review_templates(run_dir, output):
    run = read(run_dir / "run.json")
    cases = {case["case_id"]: case for case in read(CASES)["cases"]}
    calls_path = run_dir / "calls.jsonl"
    calls, _partial = read_journal(calls_path)
    output.mkdir(parents=True, exist_ok=False)
    for row in run["case_results"]:
        case = cases[row["case_id"]]
        write(output / f"{row['case_id']}.json", {
            "case_id": row["case_id"], "response_sha256": row["response_sha256"],
            "reviewer_kind": "human", "source": "user_submission", "reviewer_id": "",
            "time_kind": "reviewer_measured_elapsed", "elapsed_seconds": None, "decision": None,
            "notes": "", "final_text": None, "input": case["request"],
            "review_checks": case["human_review_checks"], "product_output": row["response"],
            "role_observations": {"gemma_generation": None, "qwen_feedback": None,
                                  "gemma_revision": None, "rubric_errors": None},
            "observed_model_calls": [{"mode": call["mode"], "model": call["request"]["model"],
                                      "request_payload": json.loads(base64.b64decode(call["request"]["wire"]["body_base64"])),
                                      "native_response": call.get("response"), "stop_reason": call.get("stop_reason")}
                                     for call in calls if call["case_id"] == row["case_id"]],
        })


def report(manifest, run_dir):
    run = read(run_dir / "run.json") if (run_dir / "run.json").exists() else {"mode": "not_executed", "case_results": []}
    path = run_dir / "human_reviews.jsonl"
    reviews = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    if run.get("mode") != "not_executed" and run.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("결과와 manifest가 다릅니다.")
    measured = [row for row in reviews if run["mode"] in ("live_inference", "stored_replay")
                and row.get("run_mode") == run["mode"] and review_matches(run, row)]
    covered = {row["case_id"] for row in measured}
    remaining = []
    if run["mode"] != "live_inference" or run.get("status") != "completed_fixed_evidence_api":
        remaining.append("실제 추론 승인 및 고정 근거 API 평가")
    if len(covered) < 2:
        remaining.append("사람의 원문·주장·근거 대조와 검토시간 제출")
    calls_path = run_dir / "calls.jsonl"
    calls, calls_partial = read_journal(calls_path)
    actual = [row for row in calls if row.get("mode") == "live_inference" and row.get("sent_to_model")]
    reservations_path = run_dir / "reservations.jsonl"
    reservations, reservations_partial = read_journal(reservations_path)
    reserved_ids = {row["call_id"] for row in reservations if row.get("mode") == "live_inference"}
    final_ids = {row["call_id"] for row in calls if row.get("mode") == "live_inference" and row.get("call_id")}
    unresolved = sorted(reserved_ids - final_ids)
    latency = sum(row["elapsed_seconds"] for row in actual) if actual else None
    quality_cases = []
    for row in run["case_results"]:
        metadata = row["response"].get("data", {}).get("generation_metadata", {})
        rubric = metadata.get("civil_llm_rubric", {})
        quality_cases.append({"case_id": row["case_id"], "response_sha256": row["response_sha256"],
                              "generation_fallback_used": metadata.get("fallback_used"),
                              "legal_grounding_status": metadata.get("legal_grounding_status"),
                              "automatic_judge_status": rubric.get("judge_status"),
                              "uncalibrated_automatic_safety_layer": rubric.get("safety_layer"),
                              "human_acceptability": "pending" if row["case_id"] not in covered else "see_human_records"})
    return {"scope": manifest["scope"], "execution": run, "automatic_metrics_are_human_burden": False,
            "reservation_journal": {"live_reserved_calls": len(reserved_ids), "unresolved_call_ids": unresolved,
                                    "incomplete_final_call_tail": calls_partial, "incomplete_reservation_tail": reservations_partial,
                                    "live_attempts_including_unresolved": len(actual) + len(unresolved),
                                    "server_completion_unknown": bool(unresolved) or any(row.get("server_completion_unknown") for row in actual),
                                    "note": "예약과 최종 기록은 call_id로 대응하며 중복 합산하지 않음. 미해결 예약은 전송/서버 완료 불명이며 초기 receipt의 0콜을 확정값으로 해석하지 않음."},
            "quality_cases": quality_cases, "automatic_quality_is_human_or_semantic_success": False,
            "latency": {"observed_client_call_wall_seconds": latency,
                        "scope": "고정 근거 QA+평가 observer의 소요시간. 검색·임베딩 제외. 재생/합성 시간은 실제 추론 시간으로 집계하지 않음."},
            "live_model_calls_by_tag": {model: sum(row["request"]["model"] == model for row in actual) for model in MODELS.values()},
            "qwen_feedback_quality": "requires_human_review" if any(row["request"]["model"] == MODELS["feedback"] for row in actual) else "unmeasured_no_live_feedback_call",
            "human_review": {"status": "measured" if len(covered) == 2 else "partial" if covered else "unmeasured",
                             "reviewed_cases": len(covered), "records": measured,
                             "invalid_or_synthetic_records": len(reviews) - len(measured),
                             "burden_reduction": "unknown_without_manual_baseline"},
            "retained_failures": manifest["retained_failures"], "issue_complete": False,
            "remaining": remaining + ["담당자의 결과 검토"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", required=True, type=Path)
    prep.add_argument("--inventory", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--mode", choices=("live_inference", "stored_replay"), default="stored_replay")
    run.add_argument("--approval", type=Path)
    run.add_argument("--replay-calls", type=Path)
    review = sub.add_parser("review")
    review.add_argument("--run-dir", required=True, type=Path)
    review.add_argument("--entry", required=True, type=Path)
    template = sub.add_parser("review-template")
    template.add_argument("--run-dir", required=True, type=Path)
    template.add_argument("--output", required=True, type=Path)
    summary = sub.add_parser("report")
    summary.add_argument("--manifest", required=True, type=Path)
    summary.add_argument("--run-dir", required=True, type=Path)
    summary.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output, args.inventory)
    elif args.command == "run":
        replay = [json.loads(line) for line in args.replay_calls.read_text().splitlines()] if args.replay_calls else []
        receipt = asyncio.run(run_evaluation(read(args.manifest), args.output, mode=args.mode, approval=args.approval, replay=replay))
        print(json.dumps({key: receipt.get(key) for key in ("mode", "status", "stop_reason", "new_model_calls")}, ensure_ascii=False))
        if receipt["status"] != "completed_fixed_evidence_api":
            raise SystemExit(1)
    elif args.command == "review":
        collect_review(args.run_dir, read(args.entry))
    elif args.command == "review-template":
        review_templates(args.run_dir, args.output)
    else:
        write(args.output, report(read(args.manifest), args.run_dir))


if __name__ == "__main__":
    main()
