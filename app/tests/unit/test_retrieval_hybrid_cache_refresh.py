from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.core.exceptions import RetrievalError
from app.retrieval.service import RetrievalService
from app.retrieval.vectorstores.chroma_store import ChromaVectorStore


@pytest.fixture
def service_and_store(tmp_path, monkeypatch):
    service = RetrievalService()
    store = ChromaVectorStore(str(tmp_path / "chroma"), "unused-test-model", "cpu")
    monkeypatch.setattr(store, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(service, "_get_vectorstore", lambda: store)
    return service, store


async def _search(service, query):
    return await service.search(
        query, collection_name="cache_refresh_test", strategy="hybrid", grounding_filter=False,
    )


@pytest.mark.asyncio
async def test_bulk_success_refreshes_same_service_hybrid_cache(service_and_store):
    service, store = service_and_store
    await service.index_documents(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test",
    )
    assert [row["case_id"] for row in await _search(service, "공사 소음")] == ["CASE-OLD"]

    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "폐기물 처리", "answer": "새 안내 답변"}],
        rebuild=True, collection_name="cache_refresh_test",
    )

    assert store.count("cache_refresh_test") == 1
    new_results = await _search(service, "폐기물 처리")
    assert [row["case_id"] for row in new_results] == ["CASE-NEW"]
    assert new_results[0]["answer"] == "새 안내 답변"
    assert new_results[0]["metadata"]["answer"] == "새 안내 답변"
    assert all(row["case_id"] != "CASE-OLD" for row in await _search(service, "공사 소음"))


@pytest.mark.asyncio
async def test_incremental_add_and_edit_refresh_payload_and_ranking(service_and_store):
    service, _ = service_and_store
    await service.index_documents(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test",
    )
    await _search(service, "공사 소음")

    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "폐기물 처리"}], collection_name="cache_refresh_test",
    )
    added = await _search(service, "폐기물 처리")
    assert added[0]["case_id"] == "CASE-NEW"

    await service.index_documents(
        [{"case_id": "CASE-NEW", "text": "하수도 정비", "answer": "수정 답변", "region": "시험지역"}],
        collection_name="cache_refresh_test",
    )
    edited = await _search(service, "하수도 정비")
    assert edited[0]["case_id"] == "CASE-NEW"
    assert "하수도 정비" in edited[0]["snippet"]
    assert edited[0]["answer"] == "수정 답변"
    assert edited[0]["metadata"]["region"] == "시험지역"


@pytest.mark.asyncio
async def test_failed_incremental_preserves_current_data_and_cache(service_and_store, monkeypatch):
    service, store = service_and_store
    await service.index_documents(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test",
    )
    before = await _search(service, "공사 소음")
    cached = service._get_hybrid()._bm25

    def fail_embeddings(texts):
        raise RuntimeError("synthetic embedding failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "embed_texts", fail_embeddings)
        with pytest.raises(RetrievalError, match="synthetic embedding failure"):
            await service.index_documents(
                [{"case_id": "CASE-NEW", "text": "폐기물 처리"}], collection_name="cache_refresh_test",
            )

    assert service._get_hybrid()._bm25 is cached
    assert await _search(service, "공사 소음") == before


@pytest.mark.asyncio
async def test_other_collection_write_keeps_current_cache(service_and_store):
    service, _ = service_and_store
    await service.index_documents(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test",
    )
    await _search(service, "공사 소음")
    cached = service._get_hybrid()._bm25

    await service.index_documents(
        [{"case_id": "CASE-OTHER", "text": "폐기물 처리"}], collection_name="other_cache_test",
    )

    assert service._get_hybrid()._bm25 is cached


@pytest.mark.asyncio
async def test_sample_bootstrap_refreshes_existing_empty_cache(service_and_store, tmp_path, monkeypatch):
    service, _ = service_and_store
    samples = [{"case_id": "CASE-SAMPLE", "text": "공사 소음"}]
    (tmp_path / "sample_cases.json").write_text(json.dumps(samples), encoding="utf-8")
    monkeypatch.setattr(settings, "SAMPLES_DATA_PATH", str(tmp_path))
    assert service._get_hybrid().search("cache_refresh_test", "공사 소음", 5, []) == []

    assert [row["case_id"] for row in await _search(service, "공사 소음")] == ["CASE-SAMPLE"]


@pytest.mark.asyncio
async def test_same_name_new_physical_collection_rebuilds_cache(service_and_store):
    service, store = service_and_store
    await service.index_documents(
        [{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test",
    )
    await _search(service, "공사 소음")
    old_id = store._get_collection("cache_refresh_test").id
    store.reset_collection("cache_refresh_test")
    store.upsert_records("cache_refresh_test", [service._normalize_record(
        {"case_id": "CASE-NEW", "text": "폐기물 처리"}, index=0,
    )])
    assert store._get_collection("cache_refresh_test").id != old_id

    assert [row["case_id"] for row in await _search(service, "폐기물 처리")] == ["CASE-NEW"]


@pytest.mark.asyncio
async def test_completed_staging_publish_refreshes_current_payload(service_and_store):
    service, _store = service_and_store
    await service.index_documents([{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test")
    await _search(service, "공사 소음")
    cached_retriever = service._get_hybrid()
    await service.index_documents([{"case_id": "CASE-NEW", "text": "폐기물 처리"}], collection_name="complete_staging")
    await service.publish_index("cache_refresh_test", "complete_staging", expected_count=1)
    assert service._get_hybrid() is cached_retriever
    assert [row["case_id"] for row in await _search(service, "폐기물 처리")] == ["CASE-NEW"]


@pytest.mark.asyncio
async def test_rejected_staging_publish_preserves_cache_and_current_data(service_and_store):
    service, _store = service_and_store
    await service.index_documents([{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test")
    before = await _search(service, "공사 소음")
    cached = service._get_hybrid()._bm25
    await service.index_documents([{"case_id": "CASE-NEW", "text": "폐기물 처리"}], collection_name="incomplete_staging")
    with pytest.raises(RetrievalError):
        await service.publish_index("cache_refresh_test", "incomplete_staging", expected_count=2)
    assert service._get_hybrid()._bm25 is cached
    assert await _search(service, "공사 소음") == before


@pytest.mark.asyncio
async def test_separate_store_activation_is_seen_on_next_search(service_and_store, monkeypatch):
    service, store = service_and_store
    await service.index_documents([{"case_id": "CASE-OLD", "text": "공사 소음"}], collection_name="cache_refresh_test")
    await _search(service, "공사 소음")
    other = ChromaVectorStore(store.persist_directory, "unused-test-model", "cpu")
    monkeypatch.setattr(other, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    other.replace_records("cache_refresh_test", [service._normalize_record(
        {"case_id": "CASE-NEW", "text": "폐기물 처리"}, index=0,
    )])
    assert [row["case_id"] for row in await _search(service, "폐기물 처리")] == ["CASE-NEW"]
