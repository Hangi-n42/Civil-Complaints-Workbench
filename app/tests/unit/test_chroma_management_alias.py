from __future__ import annotations

import argparse
import gzip
import json
import pickle
import sqlite3

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings

from app.retrieval.vectorstores.collection_alias import activate_collection, get_active_collection, resolve_collection_name
from scripts import export_chroma_collection, import_chroma_collection, inspect_chromadb
from scripts import cleanup_chromadb_case_duplicates, prepare_processed_append_inputs, repair_chromadb_hnsw


LOGICAL = "management_cases"
ACTIVE = "management_cases_active"


@pytest.fixture
def aliased_chroma(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path), settings=Settings(anonymized_telemetry=False))
    backup = client.create_collection(LOGICAL)
    backup.add(ids=["old"], documents=["obsolete document"], embeddings=[[1.0, 0.0]],
               metadatas=[{"case_id": "CASE-OLD", "chunk_id": "CASE-OLD__chunk-0"}])
    active = client.create_collection(ACTIVE)
    active.add(ids=["new", "new-stale"], documents=["current document", "stale duplicate"],
               embeddings=[[0.0, 1.0], [0.0, 1.0]], metadatas=[
                   {"case_id": "CASE-ACTIVE", "chunk_id": "CASE-ACTIVE__chunk-0"},
                   {"case_id": "CASE-ACTIVE", "chunk_id": "CASE-ACTIVE__chunk-1"},
               ])
    activate_collection(tmp_path, LOGICAL, ACTIVE)
    return client, tmp_path


def _import_args(tmp_path, payload, *, replace=True):
    source = tmp_path / "transfer.pkl.gz"
    with gzip.open(source, "wb") as handle:
        pickle.dump(payload, handle)
    return argparse.Namespace(persist_dir=str(tmp_path), input=source, collection=LOGICAL,
                              batch_size=1, replace=replace)


def _payload():
    return {"collection_name": LOGICAL, "ids": ["imported"], "documents": ["imported current"],
            "metadatas": [{"case_id": "CASE-IMPORTED", "chunk_id": "CASE-IMPORTED__chunk-0"}],
            "embeddings": np.asarray([[0.5, 0.5]], dtype=np.float32)}


def test_export_reads_active_collection_with_existing_embeddings(aliased_chroma, monkeypatch):
    _, path = aliased_chroma
    output = path / "export.pkl.gz"
    monkeypatch.setattr(export_chroma_collection, "_parse_args", lambda: argparse.Namespace(
        persist_dir=str(path), collection=LOGICAL, out=output, batch_size=1,
    ))

    export_chroma_collection.main()

    with gzip.open(output, "rb") as handle:
        payload = pickle.load(handle)
    assert set(payload["ids"]) == {"new", "new-stale"}
    assert payload["collection_name"] == LOGICAL
    assert payload["embeddings"].tolist() == [[0.0, 1.0], [0.0, 1.0]]
    monkeypatch.setattr(import_chroma_collection, "_parse_args", lambda: argparse.Namespace(
        persist_dir=str(path), input=output, collection=LOGICAL, batch_size=1, replace=True,
    ))
    import_chroma_collection.main()
    restored = get_active_collection(aliased_chroma[0], LOGICAL).get(include=["documents", "metadatas", "embeddings"])
    assert restored["ids"] == payload["ids"]
    assert restored["documents"] == payload["documents"]
    assert restored["embeddings"].tolist() == payload["embeddings"].tolist()


@pytest.mark.parametrize("replace", [True, False])
def test_import_targets_active_name_and_preserves_prior_physical_collections(aliased_chroma, monkeypatch, replace):
    client, path = aliased_chroma
    monkeypatch.setattr(import_chroma_collection, "_parse_args", lambda: _import_args(path, _payload(), replace=replace))

    import_chroma_collection.main()

    assert client.get_collection(LOGICAL).get()["ids"] == ["old"]
    expected = {"imported"} if replace else {"new", "new-stale", "imported"}
    assert set(get_active_collection(client, LOGICAL).get()["ids"]) == expected
    if replace:
        assert resolve_collection_name(path, LOGICAL) != ACTIVE
        assert set(client.get_collection(ACTIVE).get()["ids"]) == {"new", "new-stale"}


def test_empty_replace_import_preserves_active_pointer_and_backups(aliased_chroma, monkeypatch):
    client, path = aliased_chroma
    empty = {**_payload(), "ids": [], "documents": [], "metadatas": [], "embeddings": np.empty((0, 2))}
    monkeypatch.setattr(import_chroma_collection, "_parse_args", lambda: _import_args(path, empty))

    with pytest.raises(ValueError):
        import_chroma_collection.main()

    assert resolve_collection_name(path, LOGICAL) == ACTIVE
    assert client.get_collection(LOGICAL).get()["ids"] == ["old"]
    assert get_active_collection(client, LOGICAL).count() == 2


@pytest.mark.parametrize("reader", ["inspect", "dump", "prepare-append", "cleanup", "repair-source"])
def test_sql_management_readers_follow_same_active_collection(aliased_chroma, tmp_path, reader):
    client, path = aliased_chroma
    if reader == "inspect":
        result = inspect_chromadb._sqlite_docs(persist_dir=path, collection_name=LOGICAL, limit=10, max_chars=100)
        assert {row["embedding_id"] for row in result["docs"]} == {"new", "new-stale"}
    elif reader == "dump":
        result = inspect_chromadb._sqlite_dump_docs(
            persist_dir=path, collection_name=LOGICAL, output=tmp_path / "dump.jsonl",
            batch_size=1, limit=None, max_chars=None,
        )
        assert result["written_rows"] == 2
        assert "CASE-OLD" not in (tmp_path / "dump.jsonl").read_text()
    elif reader == "prepare-append":
        assert prepare_processed_append_inputs._read_existing_case_ids(path / "chroma.sqlite3", LOGICAL) == {"CASE-ACTIVE"}
    elif reader == "cleanup":
        result = cleanup_chromadb_case_duplicates.analyze_duplicates(
            sqlite_path=path / "chroma.sqlite3", collection_name=LOGICAL, case_ids={"CASE-ACTIVE"},
        )
        assert result["missing_current_case_count"] == 0
        assert result["stale_ids"] == ["new-stale"]
    else:
        with sqlite3.connect(path / "chroma.sqlite3") as connection:
            collection_id, _ = repair_chromadb_hnsw._get_collection_id(connection, LOGICAL)
        assert collection_id == str(client.get_collection(ACTIVE).id)


def test_failed_import_keeps_active_and_prior_physical_collections(aliased_chroma, monkeypatch):
    client, path = aliased_chroma
    payload = _payload()
    payload.update(ids=["imported", "imported-2"], documents=["first", "second"],
                   metadatas=[{"case_id": "CASE-1"}, {"case_id": "CASE-2"}],
                   embeddings=np.asarray([[0.5, 0.5], [0.5, 0.5]], dtype=np.float32))
    monkeypatch.setattr(import_chroma_collection, "_parse_args", lambda: _import_args(path, payload))
    collection_type = type(client.get_collection(ACTIVE))
    original_add = collection_type.add

    def failing_add(self, *args, **kwargs):
        if kwargs["ids"] == ["imported-2"]:
            raise RuntimeError("synthetic batch write failure")
        return original_add(self, *args, **kwargs)
    monkeypatch.setattr(collection_type, "add", failing_add)

    with pytest.raises(RuntimeError, match="synthetic batch write failure"):
        import_chroma_collection.main()

    assert resolve_collection_name(path, LOGICAL) == ACTIVE
    assert get_active_collection(client, LOGICAL).count() == 2
    assert client.get_collection(LOGICAL).get()["ids"] == ["old"]


@pytest.mark.parametrize("fail", [False, True])
def test_repair_reset_stages_before_activation_with_fake_encoder(aliased_chroma, monkeypatch, fail):
    client, path = aliased_chroma
    monkeypatch.setattr(repair_chromadb_hnsw, "_get_st_model", lambda *args: object())
    encoded = []

    def encode(model, documents, **kwargs):
        encoded.extend(documents)
        if fail and len(encoded) > 1:
            raise RuntimeError("synthetic encoder failure")
        return [[0.0, 1.0] for _ in documents]
    monkeypatch.setattr(repair_chromadb_hnsw, "_encode_with_retry", encode)
    argv = ["--source-persist-dir", str(path), "--source-collection", LOGICAL,
            "--target-persist-dir", str(path), "--target-collection", LOGICAL, "--read-batch-size", "1"]

    if fail:
        with pytest.raises(RuntimeError, match="synthetic encoder failure"):
            repair_chromadb_hnsw.main(argv)
        assert resolve_collection_name(path, LOGICAL) == ACTIVE
    else:
        assert repair_chromadb_hnsw.main(argv) == 0
        assert resolve_collection_name(path, LOGICAL) != ACTIVE
        assert set(get_active_collection(client, LOGICAL).get()["ids"]) == {"new", "new-stale"}
    assert client.get_collection(LOGICAL).get()["ids"] == ["old"]
    assert client.get_collection(ACTIVE).count() == 2


def test_overlay_replace_preserves_base_and_previous_active(aliased_chroma, monkeypatch):
    from scripts import build_be1_metadata_overlay_collection as overlay

    client, path = aliased_chroma
    monkeypatch.setattr(overlay, "_parse_args", lambda: argparse.Namespace(
        persist_dir=str(path), base_collection=LOGICAL, metadata_collection=LOGICAL,
        output_collection=LOGICAL, batch_size=1, replace=True,
        out_json=path / "overlay.json", out_md=path / "overlay.md",
    ))

    overlay.main()

    assert resolve_collection_name(path, LOGICAL) != ACTIVE
    assert set(get_active_collection(client, LOGICAL).get()["ids"]) == {"new", "new-stale"}
    assert client.get_collection(LOGICAL).get()["ids"] == ["old"]
    assert client.get_collection(ACTIVE).count() == 2


@pytest.mark.parametrize("command", ["inspect-count", "backfill", "coverage", "cleanup-apply", "reindex-resume"])
def test_management_commands_target_the_active_collection(aliased_chroma, monkeypatch, capsys, command):
    client, path = aliased_chroma
    if command == "inspect-count":
        assert inspect_chromadb.cmd_count(argparse.Namespace(persist_dir=str(path), collection=LOGICAL)) == 0
        assert capsys.readouterr().out.strip() == "2"
    elif command == "backfill":
        from scripts import backfill_chromadb_search_signals as backfill
        monkeypatch.setattr(backfill, "_build_signals", lambda *args: {"key_terms": "synthetic-active-marker"})
        monkeypatch.setattr("sys.argv", ["backfill", "--persist-dir", str(path), "--collection", LOGICAL,
                                        "--report", str(path / "backfill.json")])
        backfill.main()
        assert all(meta["key_terms"] == "synthetic-active-marker" for meta in get_active_collection(client, LOGICAL).get()["metadatas"])
        assert "key_terms" not in client.get_collection(LOGICAL).get()["metadatas"][0]
    elif command == "coverage":
        from scripts import check_chromadb_search_signal_coverage as coverage
        monkeypatch.setattr("sys.argv", ["coverage", "--persist-dir", str(path), "--collection", LOGICAL,
                                        "--out-json", str(path / "coverage.json"), "--out-md", str(path / "coverage.md")])
        coverage.main()
        assert json.loads((path / "coverage.json").read_text())["total_count"] == 2
    elif command == "cleanup-apply":
        inputs = path / "inputs"
        inputs.mkdir()
        (inputs / "case.json").write_text(json.dumps([{"case_id": "CASE-ACTIVE"}]))
        monkeypatch.setattr("sys.argv", ["cleanup", "--collection-name", LOGICAL,
                                        "--chroma-sqlite", str(path / "chroma.sqlite3"), "--chroma-dir", str(path),
                                        "--repair-input-dir", str(inputs), "--apply"])
        assert cleanup_chromadb_case_duplicates.main() == 0
        assert get_active_collection(client, LOGICAL).get()["ids"] == ["new"]
        assert client.get_collection(LOGICAL).get()["ids"] == ["old"]
    else:
        from scripts import reindex_be1_direct
        client.delete_collection(LOGICAL)
        monkeypatch.setattr(chromadb, "PersistentClient", lambda **kwargs: client)
        assert reindex_be1_direct._existing_case_ids(LOGICAL) == {"CASE-ACTIVE"}
