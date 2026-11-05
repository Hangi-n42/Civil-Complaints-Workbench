from __future__ import annotations

import chromadb
import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routers import admin, chroma_debug
from app.core.config import settings
from app.retrieval.vectorstores.collection_alias import activate_collection


@pytest.fixture
def active_corpus(tmp_path, monkeypatch):
    client = chromadb.PersistentClient(path=str(tmp_path))
    original = client.create_collection("civil_cases_v3", embedding_function=None)
    original.add(ids=["OLD"], documents=["교체 전 합성 원문"], embeddings=[[1.0, 0.0]],
                 metadatas=[{"case_id": "OLD", "region": "이전 지역", "created_at": "2024-01-01"}])
    staged = client.create_collection("staged_civil_cases", embedding_function=None)
    staged.add(ids=["NEW-A", "NEW-B"], documents=["교체 후 합성 원문 A", "교체 후 합성 원문 B"],
               embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
               metadatas=[{"case_id": case_id, "region": "새 지역", "created_at": "2025-01-01"}
                          for case_id in ("NEW-A", "NEW-B")])
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "DEFAULT_CHROMA_COLLECTION", "civil_cases_v3")
    monkeypatch.setattr(admin, "_doc_index", None)
    monkeypatch.setattr(admin, "_doc_index_source", None)
    return tmp_path, client


def test_admin_cache_follows_active_collection_change(active_corpus):
    persist, _client = active_corpus
    old = admin._build_index()
    assert len(old) == 1
    activate_collection(persist, "civil_cases_v3", "staged_civil_cases")
    current = admin._build_index()
    assert len(current) == 2
    assert {row["region"] for row in current} == {"새 지역"}
    assert {row["region"] for row in old} == {"이전 지역"}


def test_debug_count_and_sample_read_current_logical_collection(active_corpus):
    persist, client = active_corpus
    activate_collection(persist, "civil_cases_v3", "staged_civil_cases")
    api = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    count = api.get("/api/v1/chroma/collections/civil_cases_v3/count")
    assert count.status_code == 200
    assert count.json()["data"]["count"] == 2
    sample = api.get("/api/v1/chroma/collections/civil_cases_v3/sample")
    assert sample.status_code == 200
    rows = sample.json()["data"]["rows"]
    assert {row["id"] for row in rows} == {"NEW-A", "NEW-B"}
    assert client.get_collection("civil_cases_v3").get()["ids"] == ["OLD"]


def test_debug_sqlite_fallback_reads_current_documents(active_corpus):
    persist, _client = active_corpus
    activate_collection(persist, "civil_cases_v3", "staged_civil_cases")
    result = chroma_debug._sqlite_sample_docs(persist_dir=persist, collection_name="civil_cases_v3",
                                             limit=5, max_chars=0)
    assert {row["document"] for row in result["rows"]} == {"교체 후 합성 원문 A", "교체 후 합성 원문 B"}


def test_debug_collection_listing_shows_current_dimension(active_corpus):
    persist, _client = active_corpus
    activate_collection(persist, "civil_cases_v3", "staged_civil_cases")
    rows = chroma_debug._sqlite_list_collections(persist)
    logical = next(row for row in rows if row["name"] == "civil_cases_v3")
    assert logical["dimension"] == 3
    assert logical["active_physical_name"] == "staged_civil_cases"


def test_empty_staging_does_not_break_debug_listing_or_sqlite_sample(active_corpus):
    persist, client = active_corpus
    client.create_collection("unfinished_staging", embedding_function=None)
    response = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)).get("/api/v1/chroma/collections")
    assert response.status_code == 200
    rows = response.json()["data"]["collections"]
    empty = next(row for row in rows if row["name"] == "unfinished_staging")
    assert empty["dimension"] is None
    sample = chroma_debug._sqlite_sample_docs(persist_dir=persist, collection_name="unfinished_staging",
                                             limit=5, max_chars=0)
    assert sample["rows"] == []
    assert sample["dimension"] is None


def test_direct_reindex_resume_uses_configured_active_database(active_corpus):
    from scripts.reindex_be1_direct import _existing_case_ids

    persist, _client = active_corpus
    activate_collection(persist, "civil_cases_v3", "staged_civil_cases")
    assert _existing_case_ids("civil_cases_v3") == {"NEW-A", "NEW-B"}
