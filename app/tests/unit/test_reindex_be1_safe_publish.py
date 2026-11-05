from __future__ import annotations

import asyncio
import json

import pytest

from app.core.exceptions import RetrievalError
from app.retrieval.service import RetrievalService
from app.retrieval.vectorstores.chroma_store import ChromaVectorStore
from scripts import reindex_be1_direct as direct


COLLECTION = "direct_reindex_test"


class _Ingestion:
    async def process(self, records, **kwargs):
        return records


class _Structuring:
    async def structure(self, record):
        return {"case_id": record["case_id"], "source": "synthetic-test", "category": "시험",
                "observation": {"text": record.get("text", "")}, "validation": {"is_valid": True}}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    service = RetrievalService()
    store = ChromaVectorStore(str(tmp_path / "chroma"), "unused-test-model", "cpu")
    monkeypatch.setattr(store, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(service, "_get_vectorstore", lambda: store)
    service._index_documents_internal([{"case_id": "CASE-OLD", "text": "이전 정상 자료"}], collection_name=COLLECTION)
    monkeypatch.setattr(direct, "get_ingestion_service", _Ingestion)
    monkeypatch.setattr(direct, "get_structuring_service", _Structuring)
    monkeypatch.setattr(direct, "get_retrieval_service", lambda: service)
    monkeypatch.setattr(direct, "_existing_case_ids", lambda name: {"CASE-OLD"})
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "cases.json").write_text(json.dumps([
        {"case_id": "CASE-NEW-1", "text": "첫 신규 자료"},
        {"case_id": "CASE-NEW-2", "text": "둘째 신규 자료"},
    ]))
    return inputs, service, store


async def _run(inputs, *, rebuild=True, resume=False):
    await direct.main(str(inputs), COLLECTION, 1, rebuild, 0, resume=resume)


def _case_ids(store):
    return {meta["case_id"] for meta in store._get_collection(COLLECTION).get(include=["metadatas"])["metadatas"]}


@pytest.mark.asyncio
async def test_direct_rebuild_publishes_once_after_all_batches(runtime, monkeypatch):
    inputs, service, store = runtime
    old = store._get_collection(COLLECTION)
    upsert, publish = store.upsert_records, service.publish_index
    writes, publications = [], []

    def observe_write(name, records):
        result = upsert(name, records)
        writes.append((name, store._get_collection(COLLECTION).id))
        return result

    async def observe_publish(*args, **kwargs):
        publications.append((args, kwargs))
        return await publish(*args, **kwargs)

    monkeypatch.setattr(store, "upsert_records", observe_write)
    monkeypatch.setattr(service, "publish_index", observe_publish)
    await _run(inputs)

    assert [active_id for _, active_id in writes] == [old.id, old.id]
    assert len({name for name, _ in writes}) == 1
    assert writes[0][0] != COLLECTION
    assert len(publications) == 1
    assert publications[0][1]["expected_count"] == 2
    assert _case_ids(store) == {"CASE-NEW-1", "CASE-NEW-2"}
    assert old.count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("synthetic second batch failure"), asyncio.CancelledError()])
async def test_direct_second_batch_failure_keeps_old_active_collection(runtime, monkeypatch, failure):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)
    upsert, writes = store.upsert_records, []

    def fail_second(name, records):
        if writes:
            raise failure
        writes.append(name)
        return upsert(name, records)

    monkeypatch.setattr(store, "upsert_records", fail_second)
    with pytest.raises((RetrievalError, asyncio.CancelledError)):
        await _run(inputs)

    assert store._get_collection(COLLECTION).id == old.id
    assert _case_ids(store) == {"CASE-OLD"}
    assert store.count(writes[0]) == 1


@pytest.mark.asyncio
async def test_direct_structuring_failure_does_not_publish_partial_result(runtime, monkeypatch):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)

    class FailingStructuring(_Structuring):
        async def structure(self, record):
            if record["case_id"] == "CASE-NEW-2":
                raise RuntimeError("synthetic structuring failure")
            return await super().structure(record)

    monkeypatch.setattr(direct, "get_structuring_service", FailingStructuring)
    with pytest.raises(RuntimeError, match="재구축 미완료"):
        await _run(inputs)
    assert store._get_collection(COLLECTION).id == old.id


@pytest.mark.asyncio
async def test_direct_empty_batch_does_not_publish_other_successful_batch(runtime):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)
    (inputs / "cases.json").write_text(json.dumps([
        {"case_id": "CASE-NEW-1", "text": "첫 신규 자료"}, {"case_id": "CASE-EMPTY", "text": ""},
    ]))

    with pytest.raises(RuntimeError, match="일부가 저장되지 않아"):
        await _run(inputs)
    assert store._get_collection(COLLECTION).id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("case_ids", [("CASE-SAME", "CASE-SAME"), ("collision_1", "collision-1")])
async def test_direct_publish_rejects_cross_batch_id_count_mismatch(runtime, case_ids):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)
    (inputs / "cases.json").write_text(json.dumps([
        {"case_id": case_ids[0], "text": "첫 신규 자료"}, {"case_id": case_ids[1], "text": "둘째 신규 자료"},
    ]))

    with pytest.raises(RetrievalError, match="expected=2.*actual=1"):
        await _run(inputs)
    assert store._get_collection(COLLECTION).id == old.id


@pytest.mark.asyncio
async def test_direct_input_read_failure_blocks_rebuild(runtime):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)
    (inputs / "broken.json").write_text("{invalid-json")

    with pytest.raises(ValueError):
        await _run(inputs)
    assert store._get_collection(COLLECTION).id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_direct_incremental_and_resume_keep_existing_collection(runtime, resume):
    inputs, _, store = runtime
    old = store._get_collection(COLLECTION)
    if resume:
        rows = json.loads((inputs / "cases.json").read_text())
        rows.append({"case_id": "CASE-OLD", "source_id": "OLD", "text": "중복 기존 자료"})
        (inputs / "cases.json").write_text(json.dumps(rows))

    await _run(inputs, rebuild=resume, resume=resume)

    assert store._get_collection(COLLECTION).id == old.id
    assert _case_ids(store) == {"CASE-OLD", "CASE-NEW-1", "CASE-NEW-2"}
