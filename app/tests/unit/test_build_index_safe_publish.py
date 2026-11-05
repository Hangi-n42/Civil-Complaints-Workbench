from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import retrieval
from app.retrieval.service import RetrievalService
from app.retrieval.vectorstores.chroma_store import ChromaVectorStore
from scripts import build_index


class _Ingestion:
    async def process(self, records, **kwargs):
        return records


class _Structuring:
    async def structure(self, record):
        return {
            "case_id": record["case_id"],
            "source": "synthetic-test",
            "category": "시험",
            "observation": {"text": record.get("text", "")},
            "validation": {"is_valid": True},
        }


@pytest.fixture
def cli_runtime(tmp_path, monkeypatch):
    service = RetrievalService()
    store = ChromaVectorStore(str(tmp_path / "chroma"), "unused-test-model", "cpu")
    monkeypatch.setattr(store, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(service, "_get_vectorstore", lambda: store)
    service._index_documents_internal(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cli_rebuild_test",
    )
    monkeypatch.setattr(retrieval, "get_retrieval_service", lambda: service)
    application = FastAPI()
    application.include_router(retrieval.router)

    @application.get("/api/v1/health")
    def health():
        return {"success": True}

    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(transport=httpx.ASGITransport(app=application), **kwargs)

    monkeypatch.setattr(build_index.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(build_index, "get_ingestion_service", _Ingestion)
    monkeypatch.setattr(build_index, "get_structuring_service", _Structuring)
    monkeypatch.setattr(build_index, "STRUCTURED_OUTPUT_DIR", tmp_path / "outputs")
    save_outputs = build_index._save_structured_outputs
    monkeypatch.setattr(build_index, "_save_structured_outputs", lambda **kwargs: save_outputs(
        output_dir=tmp_path / "outputs", **kwargs,
    ))
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    rows = [{"case_id": "CASE-NEW-1", "text": "폐기물 처리"},
            {"case_id": "CASE-NEW-2", "text": "도로 정비"}]
    (input_dir / "cases.json").write_text(json.dumps(rows), encoding="utf-8")
    return input_dir, service, store


async def _run_main(input_dir, **kwargs):
    await build_index.main(
        input_dir=str(input_dir), api_url="http://test", collection_name="cli_rebuild_test",
        batch_size=1, rebuild=True, index_retries=0, index_retry_delay=0, **kwargs,
    )


@pytest.mark.asyncio
async def test_cli_keeps_target_until_all_batches_complete_then_publishes(cli_runtime, monkeypatch):
    input_dir, service, store = cli_runtime
    old = store._get_collection("cli_rebuild_test")
    upsert = store.upsert_records
    visible = []

    def observe(name, records):
        result = upsert(name, records)
        visible.append(store._get_collection("cli_rebuild_test").id)
        return result

    monkeypatch.setattr(store, "upsert_records", observe)
    await service.search("공사 소음", collection_name="cli_rebuild_test", strategy="hybrid", grounding_filter=False)
    await _run_main(input_dir)

    assert visible == [old.id, old.id]
    assert old.count() == 1
    got = store._get_collection("cli_rebuild_test").get(include=["metadatas"])
    assert {meta["case_id"] for meta in got["metadatas"]} == {"CASE-NEW-1", "CASE-NEW-2"}
    results = await service.search("폐기물", collection_name="cli_rebuild_test", strategy="hybrid", grounding_filter=False)
    assert {row["case_id"] for row in results} == {"CASE-NEW-1", "CASE-NEW-2"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("synthetic second batch failure"), asyncio.CancelledError()])
async def test_cli_second_batch_failure_preserves_old_target(cli_runtime, monkeypatch, failure):
    input_dir, _, store = cli_runtime
    old = store._get_collection("cli_rebuild_test")
    upsert = store.upsert_records
    calls = []

    def fail_second(name, records):
        if calls:
            raise failure
        calls.append(name)
        return upsert(name, records)

    monkeypatch.setattr(store, "upsert_records", fail_second)
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await _run_main(input_dir)

    assert store._get_collection("cli_rebuild_test").id == old.id
    assert old.count() == 1
    assert store.count(calls[0]) == 1


@pytest.mark.asyncio
async def test_cli_structuring_failure_never_publishes_partial_index(cli_runtime, monkeypatch):
    input_dir, _, store = cli_runtime
    old = store._get_collection("cli_rebuild_test")

    class _FailSecond(_Structuring):
        async def structure(self, record):
            if record["case_id"] == "CASE-NEW-2":
                raise RuntimeError("synthetic structuring failure")
            return await super().structure(record)

    monkeypatch.setattr(build_index, "get_structuring_service", _FailSecond)
    with pytest.raises(RuntimeError, match="재구축 미완료"):
        await _run_main(input_dir)

    assert store._get_collection("cli_rebuild_test").id == old.id
    assert old.count() == 1


@pytest.mark.asyncio
async def test_cli_rejected_empty_batch_preserves_target(cli_runtime):
    input_dir, _, store = cli_runtime
    old = store._get_collection("cli_rebuild_test")
    (input_dir / "cases.json").write_text(
        json.dumps([{"case_id": "CASE-EMPTY", "text": ""}]), encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="일부가 저장되지 않아"):
        await _run_main(input_dir)

    assert store._get_collection("cli_rebuild_test").id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["main", "rest-helper"])
@pytest.mark.parametrize("case_ids", [("CASE-SAME", "CASE-SAME"), ("collision_1", "collision-1")])
async def test_cross_batch_id_collision_does_not_publish_smaller_stage(cli_runtime, monkeypatch, entrypoint, case_ids):
    input_dir, _, store = cli_runtime
    old = store._get_collection("cli_rebuild_test")
    rows = [{"case_id": case_ids[0], "text": "폐기물 처리"},
            {"case_id": case_ids[1], "text": "도로 정비"}]
    (input_dir / "cases.json").write_text(json.dumps(rows), encoding="utf-8")
    publish = store.publish_collection
    staged_counts = []

    def observe_publish(collection_name, staged_collection_name, **kwargs):
        staged_counts.append(store._get_client().get_collection(staged_collection_name).count())
        return publish(collection_name, staged_collection_name, **kwargs)

    monkeypatch.setattr(store, "publish_collection", observe_publish)
    rejected = None
    try:
        if entrypoint == "main":
            await _run_main(input_dir)
        else:
            await build_index._index_via_rest_api(
                api_url="http://test", cases=rows, collection_name="cli_rebuild_test",
                batch_size=1, rebuild=True, logger=logging.getLogger(__name__), index_retries=0,
            )
    except httpx.HTTPStatusError as exc:
        rejected = exc

    assert staged_counts == [1]
    assert rejected is not None, "입력 2건이 ID 충돌로 1건이 됐는데도 임시 컬렉션이 발행됐습니다."
    assert rejected.response.status_code == 500
    assert "expected=2" in rejected.response.text
    assert "actual=1" in rejected.response.text
    assert store._get_collection("cli_rebuild_test").id == old.id
    assert old.get(include=["metadatas"])["metadatas"][0]["case_id"] == "CASE-OLD"
