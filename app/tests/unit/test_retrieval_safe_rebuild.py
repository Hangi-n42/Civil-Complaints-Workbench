from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.core.exceptions import RetrievalError
from app.retrieval.service import RetrievalService
from app.retrieval.vectorstores.chroma_store import ChromaVectorStore


class _MemoryStore:
    def __init__(self):
        self.records = [{"case_id": "CASE-OLD", "chunk_text": "공사 소음"}]
        self.calls = []
        self.fail = False

    def reset_collection(self, name):
        self.calls.append("reset")
        self.records = []

    def upsert_records(self, name, records):
        self.calls.append("upsert")
        if self.fail:
            raise RuntimeError("synthetic storage failure")
        self.records.extend(records)
        return {"indexed_count": len(records), "chunk_count": len(records), "records": []}

    def replace_records(self, name, records):
        self.calls.append("replace")
        if self.fail:
            raise RuntimeError("synthetic storage failure")
        self.records = list(records)
        return {"indexed_count": len(records), "chunk_count": len(records), "records": []}


@pytest.mark.parametrize("record", [
    {"case_id": "CASE-BLOCKED", "text": "격리 자료", "pii_status": "QUARANTINED"},
    {"case_id": "CASE-REVIEW", "text": "검토 자료", "needs_review": True},
    {"case_id": "CASE-EMPTY", "text": "  "},
])
def test_service_rejects_empty_rebuild_before_touching_store(record):
    service = RetrievalService()
    store = _MemoryStore()
    service._vectorstore = store

    with pytest.raises(RetrievalError, match="색인 가능한 문서가 없습니다"):
        service._index_documents_internal([record], rebuild=True)

    assert store.calls == []
    assert store.records[0]["case_id"] == "CASE-OLD"


def test_service_uses_replacement_without_destructive_reset():
    service = RetrievalService()
    store = _MemoryStore()
    store.fail = True
    service._vectorstore = store

    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        service._index_documents_internal([{"case_id": "CASE-NEW", "text": "폐기물 처리"}], rebuild=True)

    assert store.calls == ["replace"]
    assert store.records[0]["case_id"] == "CASE-OLD"


@pytest.fixture
def service_and_store(tmp_path, monkeypatch):
    service = RetrievalService()
    store = ChromaVectorStore(str(tmp_path / "chroma"), "unused-test-model", "cpu")
    monkeypatch.setattr(store, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(service, "_get_vectorstore", lambda: store)
    service._index_documents_internal(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="safe_rebuild_test",
    )
    return service, store


async def _old_search(service):
    return await service.search(
        "공사 소음", collection_name="safe_rebuild_test", strategy="dense", grounding_filter=False,
    )


@pytest.mark.asyncio
async def test_real_chroma_keeps_old_search_on_embedding_failure(service_and_store, monkeypatch):
    service, store = service_and_store
    old_id = store._get_collection("safe_rebuild_test").id

    def fail(texts):
        raise RuntimeError("synthetic embedding failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "embed_texts", fail)
        with pytest.raises(RetrievalError, match="synthetic embedding failure"):
            await service.index_documents(
                [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
                rebuild=True, collection_name="safe_rebuild_test",
            )

    assert store._get_collection("safe_rebuild_test").id == old_id
    assert [row["case_id"] for row in await _old_search(service)] == ["CASE-OLD"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("synthetic batch failure"), asyncio.CancelledError()])
async def test_real_chroma_keeps_old_after_partial_build(service_and_store, monkeypatch, failure):
    service, store = service_and_store
    old_id = store._get_collection("safe_rebuild_test").id
    monkeypatch.setattr(store._get_client(), "get_max_batch_size", lambda: 1)
    upsert = store.upsert_records
    written = []

    def fail_second_batch(name, records):
        if written:
            raise failure
        result = upsert(name, records)
        written.append(store.count(name))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(store, "upsert_records", fail_second_batch)
        with pytest.raises((RetrievalError, asyncio.CancelledError)):
            await service.index_documents(
                [{"case_id": "CASE-NEW-1", "text": "폐기물 처리"},
                 {"case_id": "CASE-NEW-2", "text": "도로 정비"}],
                rebuild=True, collection_name="safe_rebuild_test",
            )

    assert written == [1]
    assert store._get_collection("safe_rebuild_test").id == old_id
    assert [row["case_id"] for row in await _old_search(service)] == ["CASE-OLD"]


@pytest.mark.asyncio
async def test_success_switches_only_after_complete_build_and_survives_new_store(service_and_store, monkeypatch):
    service, store = service_and_store
    old_collection = store._get_collection("safe_rebuild_test")
    upsert = store.upsert_records
    old_visible_during_build = []

    def observe(name, records):
        result = upsert(name, records)
        old_visible_during_build.append(store._get_collection("safe_rebuild_test").id)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(store, "upsert_records", observe)
        result = await service.index_documents(
            [{"case_id": "CASE-NEW", "text": "폐기물 처리"},
             {"case_id": "CASE-BLOCKED", "text": "격리 자료", "pii_status": "QUARANTINED"}],
            rebuild=True, collection_name="safe_rebuild_test",
        )

    assert old_visible_during_build == [old_collection.id]
    assert result["indexed_count"] == 1
    assert result["skipped_pii_count"] == 1
    assert result["index_name"] == "safe_rebuild_test"
    assert store._get_collection("safe_rebuild_test").id != old_collection.id
    assert old_collection.count() == 1
    fresh = ChromaVectorStore(str(store.persist_directory), "unused-test-model", "cpu")
    assert fresh._get_collection("safe_rebuild_test").id == store._get_collection("safe_rebuild_test").id
    assert fresh._get_collection("safe_rebuild_test").get(include=["metadatas"])["metadatas"][0]["case_id"] == "CASE-NEW"

    await service.index_documents(
        [{"case_id": "CASE-ADDED", "text": "하수도 정비"}], collection_name="safe_rebuild_test",
    )
    assert store.count("safe_rebuild_test") == 2
    assert old_collection.count() == 1


@pytest.mark.asyncio
async def test_pointer_write_failure_preserves_old_and_cleans_staging(service_and_store, monkeypatch):
    from app.retrieval.vectorstores import collection_alias

    service, store = service_and_store
    old_id = store._get_collection("safe_rebuild_test").id

    def fail_replace(*args):
        raise OSError("synthetic pointer failure")

    with monkeypatch.context() as patch:
        patch.setattr(collection_alias.os, "replace", fail_replace)
        with pytest.raises(RetrievalError, match="synthetic pointer failure"):
            await service.index_documents(
                [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
                rebuild=True, collection_name="safe_rebuild_test",
            )

    assert store._get_collection("safe_rebuild_test").id == old_id
    assert len(store._get_client().list_collections()) == 1
    assert [row["case_id"] for row in await _old_search(service)] == ["CASE-OLD"]


@pytest.mark.asyncio
async def test_incomplete_storage_is_not_published(service_and_store, monkeypatch):
    service, store = service_and_store
    old_id = store._get_collection("safe_rebuild_test").id
    monkeypatch.setattr(store, "upsert_records", lambda name, records: {"indexed_count": len(records)})

    with pytest.raises(RetrievalError, match="저장 검증"):
        await service.index_documents(
            [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
            rebuild=True, collection_name="safe_rebuild_test",
        )

    assert store._get_collection("safe_rebuild_test").id == old_id


@pytest.mark.asyncio
async def test_previous_physical_collection_can_be_explicitly_restored(service_and_store):
    service, store = service_and_store
    old_name = store._get_collection("safe_rebuild_test").name
    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
        rebuild=True, collection_name="safe_rebuild_test",
    )
    await service.publish_index("safe_rebuild_test", old_name)

    assert [row["case_id"] for row in await _old_search(service)] == ["CASE-OLD"]


@pytest.mark.asyncio
async def test_logical_readers_and_resume_use_new_physical_collection(service_and_store, monkeypatch):
    from app.core.config import settings
    from app.retrieval.pipeline.stages.bm25_retriever import BM25RetrieveStage, _load_corpus_from_chroma
    from app.retrieval.vectorstores.collection_alias import get_active_collection
    from scripts.build_index import _read_existing_case_ids

    service, store = service_and_store
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(store.persist_directory))
    stage = BM25RetrieveStage(collection="safe_rebuild_test")
    old_path = stage._index_path()
    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
        rebuild=True, collection_name="safe_rebuild_test",
    )

    assert stage._index_path() != old_path
    assert get_active_collection(store._get_client(), "safe_rebuild_test").id == store._get_collection("safe_rebuild_test").id
    assert _load_corpus_from_chroma("safe_rebuild_test")[0] == ["CASE-NEW"]
    assert _read_existing_case_ids(
        "safe_rebuild_test", logger=logging.getLogger(__name__), persist_dir=store.persist_directory,
    ) == {"CASE-NEW"}


@pytest.mark.asyncio
async def test_missing_active_collection_fails_closed(service_and_store):
    from app.retrieval.vectorstores.collection_alias import activate_collection

    _, store = service_and_store
    activate_collection(store.persist_directory, "safe_rebuild_test", "missing_physical_collection")

    with pytest.raises(Exception, match="does not exist"):
        store.count("safe_rebuild_test")
    assert len(store._get_client().list_collections()) == 1


@pytest.mark.asyncio
async def test_missing_original_collection_after_rollback_fails_closed(service_and_store):
    service, store = service_and_store
    logical_name = "safe_rebuild_test"
    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "폐기물 처리"}],
        rebuild=True, collection_name=logical_name,
    )
    await service.publish_index(logical_name, logical_name)
    pointer = next((store.persist_directory / "collection_aliases").glob("*.json"))
    assert json.loads(pointer.read_text())["active_name"] == logical_name
    fresh = ChromaVectorStore(str(store.persist_directory), "unused-test-model", "cpu")
    assert fresh.count(logical_name) == 1
    assert fresh._get_collection(logical_name).get(include=["metadatas"])["metadatas"][0]["case_id"] == "CASE-OLD"

    client = store._get_client()
    client.delete_collection(logical_name)
    before = {collection.name for collection in client.list_collections()}
    fresh = ChromaVectorStore(str(store.persist_directory), "unused-test-model", "cpu")
    with pytest.raises(Exception, match="does not exist"):
        fresh.count(logical_name)
    assert {collection.name for collection in client.list_collections()} == before


def test_unaliased_database_still_allows_new_collection(service_and_store, monkeypatch):
    _, store = service_and_store
    fresh = ChromaVectorStore(str(store.persist_directory), "unused-test-model", "cpu")
    monkeypatch.setattr(fresh, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    assert fresh.count("unaliased_new") == 0
    fresh.upsert_records("unaliased_new", [{
        "doc_id": "CASE-RAW", "case_id": "CASE-RAW", "chunk_id": "chunk-0", "chunk_text": "합성 자료",
    }])
    assert fresh.count("unaliased_new") == 1


def test_bulk_api_distinguishes_rejected_input_storage_failure_and_success(service_and_store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routers import retrieval

    service, store = service_and_store
    monkeypatch.setattr(retrieval, "get_retrieval_service", lambda: service)
    application = FastAPI()
    application.include_router(retrieval.router)
    client = TestClient(application)
    payload = {"action": "bulk", "collection_name": "safe_rebuild_test", "cases": [
        {"case_id": "CASE-BLOCKED", "text": "격리 자료", "pii_status": "QUARANTINED"},
    ]}
    rejected = client.post("/api/v1/index", json=payload)
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "BAD_REQUEST"
    assert rejected.json()["error"]["retryable"] is False
    assert store.count("safe_rebuild_test") == 1

    payload["cases"] = [{"case_id": "CASE-NEW", "text": "폐기물 처리"}]

    def fail_embeddings(texts):
        raise RuntimeError("synthetic failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "embed_texts", fail_embeddings)
        failed = client.post("/api/v1/index", json=payload)
    assert failed.status_code == 500
    assert failed.json()["success"] is False
    assert store.count("safe_rebuild_test") == 1

    succeeded = client.post("/api/v1/index", json=payload)
    assert succeeded.status_code == 202
    assert succeeded.json()["data"]["indexed_count"] == 1
    assert succeeded.json()["data"]["index_name"] == "safe_rebuild_test"
