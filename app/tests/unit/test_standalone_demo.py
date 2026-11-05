from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import standalone_demo as demo
from app.retrieval.vectorstores.collection_alias import activate_collection


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    bundle = tmp_path / "source"
    bundle.mkdir()
    for name in ("raw", "processed"):
        (bundle / name).mkdir()
        (bundle / name / "test.json").write_text('[{"text":"합성 검사 자료"}]', encoding="utf-8")
    (bundle / "asset_manifest.json").write_text(json.dumps({"data_kind": "synthetic", "source": "temporary metadata fixture"}))
    (bundle / "ui_cases.json").write_text('[{"id":"SYNTHETIC-1"}]')
    (bundle / "replay_events.json").write_text('{"events":[{"id":"SYNTHETIC-1"}]}')
    chroma = bundle / "chroma_db"
    chroma.mkdir()
    with sqlite3.connect(chroma / "chroma.sqlite3") as conn:
        conn.executescript("""
            CREATE TABLE collections(id TEXT, name TEXT, dimension INTEGER);
            CREATE TABLE segments(id TEXT, collection TEXT, type TEXT);
            CREATE TABLE embeddings(id INTEGER, embedding_id TEXT, segment_id TEXT);
            CREATE TABLE embedding_metadata(id INTEGER, key TEXT, string_value TEXT);
            INSERT INTO collections VALUES('new', 'physical_new', 2);
            INSERT INTO segments VALUES('segment-new','new','metadata');
            INSERT INTO embeddings VALUES(1,'SYNTHETIC-1','segment-new');
            INSERT INTO embedding_metadata VALUES(1,'chroma:document','합성 검사 원문');
        """)
    activate_collection(chroma, "civil_cases_v3", "physical_new")
    with sqlite3.connect(bundle / "intelligence.db") as conn:
        for table in sorted(demo.CI_TABLES):
            conn.execute(f"CREATE TABLE {table}(id TEXT)")
        conn.execute("INSERT INTO ci_events VALUES('SYNTHETIC-1')")
    embedding = tmp_path / "embedding"
    embedding.mkdir()
    for name in ("modules.json", "config.json", "tokenizer.json"):
        (embedding / name).write_text('{}')
    (embedding / "config.json").write_text('{"hidden_size":2}')
    (embedding / "model.safetensors").write_bytes(b"synthetic-not-model")
    models = tmp_path / "models"
    manifest = models / "manifests/registry.ollama.ai/library/test-model/local"
    manifest.parent.mkdir(parents=True)
    blobs = models / "blobs"
    blobs.mkdir()
    digest = "sha256:" + "a" * 64
    (blobs / digest.replace(":", "-")).write_bytes(b"test")
    layer = {"digest": digest, "size": 4}
    manifest.write_text(json.dumps({"config": layer, "layers": [layer]}))
    next_cli = tmp_path / "frontend/node_modules/next/dist/bin/next"
    next_cli.parent.mkdir(parents=True)
    next_cli.write_text("test placeholder")
    profile = json.loads((Path(__file__).resolve().parents[3] / "configs/demo/local.example.json").read_text())
    profile["assets_dir"] = str(bundle)
    profile["ollama_models_dir"] = str(models)
    env = profile["env"]
    for key, name in {"CHROMA_DB_PATH": "chroma_db", "COMPLAINT_INTELLIGENCE_DB_PATH": "intelligence.db", "RAW_DATA_PATH": "raw", "PROCESSED_DATA_PATH": "processed", "UI_CASES_DATA_PATH": "ui_cases.json"}.items():
        env[key] = str(bundle / name)
    env["EMBEDDING_MODEL"] = str(embedding)
    for key in ("OLLAMA_MODEL", "STRUCTURING_MODEL", "CIVIL_STRUCTURING_MODEL", "GROUNDING_FILTER_MODEL", "CIVIL_LLM_RUBRIC_MODEL", "PROMETHEUS_FEEDBACK_MODEL", "PROMETHEUS_REVISION_MODEL"):
        env[key] = "test-model:local"
    monkeypatch.setattr(demo, "port_available", lambda *args: True)
    monkeypatch.setattr(demo.importlib.util, "find_spec", lambda module: object())
    monkeypatch.setattr(demo.shutil, "which", lambda command, **kwargs: command)
    monkeypatch.setattr(demo.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="v22.0.0", returncode=0))
    return bundle, profile


def test_synthetic_bundle_never_becomes_real_runtime_ready(prepared):
    bundle, profile = prepared
    result = demo.check_profile(profile)
    assert result["preflight_passed"] is False
    assert result["runtime_verified"] is False
    assert [item["name"] for item in result["checks"] if not item["ok"]] == ["real_assets"]
    assets = demo.inspect_bundle(bundle, "civil_cases_v3")
    assert assets["declared_data_kind"] == "synthetic"
    assert assets["active_collection"] == "physical_new"
    assert assets["indexed_rows"] == 1


def test_prepare_copies_alias_and_preserves_original_and_existing_target(prepared, tmp_path):
    source, _ = prepared
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    target = tmp_path / "new-target"
    result = demo.prepare_bundle(source, target, "civil_cases_v3")
    assert result["runtime_ready"] is False
    assert {str(path.relative_to(target)): path.read_bytes() for path in target.rglob("*") if path.is_file()} == before
    with pytest.raises(ValueError, match="덮어쓰지"):
        demo.prepare_bundle(source, target, "civil_cases_v3")
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("damage", ["lfs", "missing-active", "empty-index", "wal", "knowledge", "symlink", "missing-document", "missing-ci"])
def test_bad_bundle_rejected_before_new_target_is_created(prepared, tmp_path, damage):
    source, _ = prepared
    if damage == "lfs":
        (source / "ui_cases.json").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:test\nsize 1000\n")
    elif damage == "missing-active":
        activate_collection(source / "chroma_db", "civil_cases_v3", "missing")
    elif damage == "empty-index":
        with sqlite3.connect(source / "chroma_db/chroma.sqlite3") as conn:
            conn.execute("DELETE FROM embeddings")
    elif damage == "wal":
        (source / "intelligence.db-wal").write_bytes(b"unfinished")
    elif damage == "knowledge":
        (source / "knowledge.db").write_bytes(b"unrelated")
    elif damage == "symlink":
        (source / "raw/link.json").symlink_to(source / "raw/test.json")
    elif damage == "missing-document":
        with sqlite3.connect(source / "chroma_db/chroma.sqlite3") as conn:
            conn.execute("DELETE FROM embedding_metadata")
    else:
        (source / "intelligence.db").unlink()
    target = tmp_path / "must-not-exist"
    with pytest.raises((ValueError, sqlite3.Error)):
        demo.prepare_bundle(source, target, "civil_cases_v3")
    assert not target.exists()


def test_raw_and_processed_absence_is_reported_without_inventing_source_coverage(prepared):
    bundle, _ = prepared
    for name in ("raw", "processed"):
        (bundle / name / "test.json").unlink()
        (bundle / name).rmdir()
    detail = demo.inspect_bundle(bundle, "civil_cases_v3")
    assert detail["indexed_documents"] == 1
    assert detail["raw"] == {"file_count": 0, "unavailable_files": 0, "index_correspondence_verified": False}
    assert detail["raw_coverage"] == "unverified"


def test_partial_empty_documents_are_reported_as_unverified_data_quality(prepared):
    bundle, _ = prepared
    with sqlite3.connect(bundle / "chroma_db/chroma.sqlite3") as conn:
        conn.execute("INSERT INTO embeddings VALUES(2,'SYNTHETIC-EMPTY','segment-new')")
    detail = demo.inspect_bundle(bundle, "civil_cases_v3")
    assert detail["indexed_rows"] == 2
    assert detail["indexed_documents"] == 1
    assert detail["missing_document_rows"] == 1
    assert detail["missing_document_ids_first_20"] == ["SYNTHETIC-EMPTY"]
    assert detail["source_verified"] is False


def test_model_manifest_missing_blob_and_embedding_lfs_are_failures(prepared):
    _, profile = prepared
    blobs = Path(profile["ollama_models_dir"]) / "blobs"
    next(blobs.iterdir()).unlink()
    (Path(profile["env"]["EMBEDDING_MODEL"]) / "model.safetensors").write_text("version https://git-lfs.github.com/spec/v1\n")
    failed = {item["name"] for item in demo.check_profile(profile)["checks"] if not item["ok"]}
    assert {"ollama_files", "embedding_files", "real_assets"} <= failed


def test_profile_path_mismatch_and_external_host_fail(prepared):
    _, profile = prepared
    profile["env"]["API_HOST"] = "0.0.0.0"
    profile["env"]["CHROMA_DB_PATH"] = "/unrelated/original"
    failed = {item["name"] for item in demo.check_profile(profile)["checks"] if not item["ok"]}
    assert {"configuration", "ports"} <= failed


@pytest.mark.parametrize("action", ["api", "frontend"])
def test_failed_preflight_never_launches_a_server(prepared, tmp_path, monkeypatch, action):
    _, profile = prepared
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    calls = []
    monkeypatch.setattr(demo.subprocess, "run", lambda command, **kwargs: calls.append(command) or SimpleNamespace(stdout="v22.0.0", returncode=0))
    assert demo.main([action, "--profile", str(path)]) == 1
    assert calls == [["node", "--version"]]


def test_frontend_target_port_only_and_project_cwd(prepared, tmp_path, monkeypatch):
    bundle, profile = prepared
    # 실제 자산이 아닌 선언된 real metadata 조건의 제어 흐름만 검사한다.
    manifest = bundle / "asset_manifest.json"
    manifest.write_text(json.dumps({"data_kind": "real", "source": "synthetic test of declared-real branch"}))
    monkeypatch.setattr(demo, "port_available", lambda host, port: port == profile["ui_port"])
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    launched = []
    def run(command, **kwargs):
        if command == ["node", "--version"]:
            return SimpleNamespace(stdout="v22.0.0", returncode=0)
        launched.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(demo.subprocess, "run", run)
    assert demo.main(["frontend", "--profile", str(path)]) == 0
    assert len(launched) == 1
    command, kwargs = launched[0]
    assert command[-4:] == ["--hostname", "127.0.0.1", "--port", "3092"]
    assert kwargs["cwd"] == tmp_path / "frontend"
    assert kwargs["env"]["UI_CASES_DATA_PATH"] == str(bundle / "ui_cases.json")


def test_bound_port_is_rejected_without_connecting():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        assert demo.port_available("127.0.0.1", occupied.getsockname()[1]) is False


@pytest.mark.asyncio
async def test_ui_uses_explicit_demo_file(tmp_path, monkeypatch):
    from app.api.routers import ui
    path = tmp_path / "ui_cases.json"
    path.write_text('[{"case_id":"SYNTHETIC-EXPLICIT","text":"합성 파일 경로 검사"}]')
    monkeypatch.setattr(ui.settings, "UI_CASES_DATA_PATH", str(path), raising=False)
    response = await ui.list_ui_cases()
    assert response["data"]["count"] == 1


def test_explicit_missing_raw_path_is_not_replaced_with_other_data(tmp_path, monkeypatch):
    from app.core import config
    (tmp_path / "raw_data").mkdir()
    missing = tmp_path / "explicit-missing-raw"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("RAW_DATA_PATH", str(missing))
    value = SimpleNamespace(
        CHROMA_DB_PATH=str(tmp_path / "chroma"), RAW_DATA_PATH=str(missing),
        INTERIM_DATA_PATH=str(tmp_path / "interim"), PROCESSED_DATA_PATH=str(tmp_path / "processed"),
        SAMPLES_DATA_PATH=str(tmp_path / "samples"), UI_CASES_DATA_PATH=str(tmp_path / "ui_cases.json"),
    )
    assert config._normalize_settings_paths(value).RAW_DATA_PATH == str(missing)


def test_invalid_profile_fails_before_any_process_or_database(tmp_path):
    path = tmp_path / "invalid-profile.json"
    path.write_text('[]')
    assert demo.main(["check", "--profile", str(path)]) == 1


def test_default_profile_preserves_current_product_features_and_roles():
    env = json.loads((Path(__file__).resolve().parents[3] / "configs/demo/local.example.json").read_text())["env"]
    assert all(env[key] == "true" for key in ("ENABLE_CIVIL_LLM_RUBRIC", "CIVIL_LLM_RUBRIC_USE_LLM_JUDGE", "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK", "PUBLIC_INSIGHT_ENABLED", "PUBLIC_INSIGHT_LLM_ENABLED"))
    assert env["PUBLIC_INSIGHT_LLM_PROVIDER"] == "fake"
    assert env["PROMETHEUS_FEEDBACK_MODEL"] == "qwen3.8:27b-q4_K_M"
    assert env["CIVIL_LLM_RUBRIC_MODEL"] == env["PROMETHEUS_REVISION_MODEL"] == env["OLLAMA_MODEL"]
    assert (env["GENERATION_NUM_PREDICT"], env["GENERATION_NUM_CTX"], env["OLLAMA_TIMEOUT"]) == ("1536", "8192", "180")
