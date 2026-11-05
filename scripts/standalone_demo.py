"""독립 민원 시연의 읽기 사전점검·새 대상 복사·명시적 실행. 모델은 실행하지 않는다."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.retrieval.vectorstores.collection_alias import resolve_collection_name

BUNDLE_REQUIRED = {"asset_manifest.json", "chroma_db", "intelligence.db", "replay_events.json", "ui_cases.json"}
BUNDLE_NAMES = BUNDLE_REQUIRED | {"raw", "processed"}
CI_TABLES = {"ci_events", "ci_analysis_runs", "ci_issue_alerts", "ci_public_insights", "ci_duplicate_groups", "ci_evidence_packs", "ci_collector_checkpoints"}


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty_file(path: Path) -> None:
    require(path.is_file() and path.stat().st_size > 0, f"파일 누락/비어 있음: {path}")
    with path.open("rb") as handle:
        require(not handle.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"), f"Git LFS 포인터만 있음: {path}")


def read_json(path: Path):
    nonempty_file(path)
    return json.loads(path.read_text(encoding="utf-8"))


def read_snapshot(path: Path) -> sqlite3.Connection:
    nonempty_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        require(not sidecar.exists() or sidecar.stat().st_size == 0, f"실행 중/미완료 SQLite snapshot: {sidecar}")
    # immutable=1은 원본의 WAL/SHM 생성도 하지 않는다. 활성 DB 대신 정지된 bundle만 받는다.
    return sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)


def inspect_bundle(bundle: Path, collection_name: str) -> dict:
    require(bundle.is_dir(), f"자료 bundle 없음: {bundle}")
    names = {path.name for path in bundle.iterdir()}
    require(BUNDLE_REQUIRED <= names and names <= BUNDLE_NAMES, "필수 bundle 항목 누락/다른 자산 혼입")
    require(not any(path.is_symlink() for path in bundle.rglob("*")), "bundle 내부 symlink는 허용하지 않음")
    require(not any("knowledge" in path.name.lower() for path in bundle.rglob("*")), "회사 지식 자산이 섞인 bundle은 허용하지 않음")
    manifest = read_json(bundle / "asset_manifest.json")
    require(manifest.get("data_kind") in {"real", "historical_preserved", "synthetic", "mixed", "unknown"}, "asset_manifest.data_kind 필요")
    require(bool(str(manifest.get("source", "")).strip()), "asset_manifest.source 필요")
    source_counts = {}
    for name in ("raw", "processed"):
        files = [path for path in (bundle / name).rglob("*") if path.is_file()]
        unavailable = 0
        for path in files:
            try:
                nonempty_file(path)
            except ValueError:
                unavailable += 1
        source_counts[name] = {"file_count": len(files), "unavailable_files": unavailable, "index_correspondence_verified": False}
    ui_cases = read_json(bundle / "ui_cases.json")
    require(isinstance(ui_cases, list) and bool(ui_cases) and all(isinstance(row, dict) for row in ui_cases), "UI cases는 비어 있지 않은 객체 배열이어야 함")
    replay = read_json(bundle / "replay_events.json")
    require(isinstance(replay, dict) and bool(replay.get("events") or replay.get("scenarios")), "replay events/scenarios 없음")
    chroma_dir = bundle / "chroma_db"
    active = resolve_collection_name(chroma_dir, collection_name)
    connection = read_snapshot(chroma_dir / "chroma.sqlite3")
    try:
        row = connection.execute("SELECT id, dimension FROM collections WHERE name = ?", (active,)).fetchone()
        require(row is not None and row[1] is not None and row[1] > 0, "active collection 없음/차원 미설정")
        columns = {item[1] for item in connection.execute("PRAGMA table_info(segments)")}
        foreign_key = "collection" if "collection" in columns else "collection_id"
        require(foreign_key in columns, "Chroma segments schema 미지원")
        count = connection.execute(
            f"SELECT count(DISTINCT e.embedding_id) FROM embeddings e JOIN segments s ON e.segment_id=s.id WHERE s.{foreign_key}=?", (row[0],),
        ).fetchone()[0]
        require(count > 0, "active collection 저장 행 0개")
        documents = connection.execute(
            f"SELECT count(DISTINCT e.embedding_id) FROM embeddings e JOIN segments s ON e.segment_id=s.id JOIN embedding_metadata m ON m.id=e.id WHERE s.{foreign_key}=? AND m.key='chroma:document' AND length(trim(m.string_value))>0", (row[0],),
        ).fetchone()[0]
        require(documents > 0, "색인 원문 document metadata 없음")
        missing_documents = [item[0] for item in connection.execute(
            f"SELECT e.embedding_id FROM embeddings e JOIN segments s ON e.segment_id=s.id WHERE s.{foreign_key}=? AND NOT EXISTS (SELECT 1 FROM embedding_metadata m WHERE m.id=e.id AND m.key='chroma:document' AND length(trim(m.string_value))>0) ORDER BY e.embedding_id LIMIT 20", (row[0],),
        )]
        vectors = connection.execute(f"SELECT id, type FROM segments WHERE {foreign_key}=?", (row[0],)).fetchall()
        for segment_id, kind in vectors:
            if "vector" in str(kind):
                require((chroma_dir / str(segment_id)).is_dir() and any((chroma_dir / str(segment_id)).iterdir()), "Chroma vector segment 파일 없음")
    finally:
        connection.close()
    connection = read_snapshot(bundle / "intelligence.db")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(CI_TABLES <= tables, "민원 Intelligence schema 누락")
        event_count = connection.execute("SELECT count(*) FROM ci_events").fetchone()[0]
        require(event_count > 0, "Intelligence 이벤트 0개")
    finally:
        connection.close()
    return {"declared_data_kind": manifest["data_kind"], "source_verified": False,
            "logical_collection": collection_name, "active_collection": active,
            "indexed_rows": count, "indexed_documents": documents, "missing_document_rows": count - documents,
            "missing_document_ids_first_20": missing_documents, "embedding_dimension": row[1], "raw_coverage": manifest.get("raw_coverage", "unverified"),
            "intelligence_events": event_count, "ui_cases": len(ui_cases), **source_counts}


def inspect_ollama(models: Path, tags: list[str]) -> dict:
    for tag in sorted(set(tags)):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", tag) is not None, "명시적인 library 모델:태그 형식 필요")
        name, version = tag.split(":", 1)
        manifest = read_json(models / "manifests" / "registry.ollama.ai" / "library" / name / version)
        require(isinstance(manifest.get("config"), dict) and bool(manifest.get("layers")), "단일 OCI config/layers manifest 필요 (manifest list 미지원)")
        for layer in [manifest["config"], *manifest["layers"]]:
            digest, size = layer.get("digest", ""), layer.get("size", 0)
            require(re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is not None and isinstance(size, int) and size > 0, "잘못된 Ollama blob metadata")
            blob = models / "blobs" / digest.replace(":", "-")
            nonempty_file(blob)
            require(blob.stat().st_size == size, f"Ollama blob 크기 불일치: {blob.name}")
    return {"tags": sorted(set(tags)), "inference_verified": False, "blob_hash_verified": False, "server_contacted": False}


def inspect_embedding(path: Path) -> dict:
    for name in ("modules.json", "config.json", "tokenizer.json"):
        read_json(path / name)
    weights = list(path.rglob("*.safetensors")) + list(path.rglob("pytorch_model*.bin"))
    require(bool(weights), "로컬 임베딩 weight 파일 없음 (원격 모델 ID 사용 금지)")
    for file in weights:
        nonempty_file(file)
    return {"path": str(path), "weight_files": len(weights), "dimension": read_json(path / "config.json").get("hidden_size"), "load_verified": False}


def port_available(host: str, port: int) -> bool:
    require(host == "127.0.0.1" and 1 <= port <= 65535, "127.0.0.1과 유효 포트만 허용")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def check_profile(profile: dict, action: str = "check") -> dict:
    require(isinstance(profile, dict) and isinstance(profile.get("env"), dict), "profile과 env는 JSON 객체여야 함")
    checks = []

    def check(name, operation):
        try:
            detail = operation()
            checks.append({"name": name, "ok": True, "detail": detail})
            return detail
        except Exception as exc:
            checks.append({"name": name, "ok": False, "reason": str(exc)})
            return None

    env = profile.get("env", {})
    bundle = project_path(profile["assets_dir"])
    def configuration():
        expected = {"CHROMA_DB_PATH": bundle / "chroma_db", "COMPLAINT_INTELLIGENCE_DB_PATH": bundle / "intelligence.db",
                    "RAW_DATA_PATH": bundle / "raw", "PROCESSED_DATA_PATH": bundle / "processed", "UI_CASES_DATA_PATH": bundle / "ui_cases.json"}
        require(all(project_path(env.get(key, "")) == path for key, path in expected.items()), "profile env 경로는 지정한 bundle과 같아야 함")
        require(env.get("COMPLAINT_INTELLIGENCE_AUTO_CREATE_DB") == "false" and env.get("COMPLAINT_INTELLIGENCE_SCHEDULER_ENABLED") == "false", "DB 자동 생성/스케줄러는 꺼야 함")
        require(env.get("HF_HUB_OFFLINE") == "1" and env.get("TRANSFORMERS_OFFLINE") == "1", "offline 모델 설정 필요")
        url = urlparse(env.get("OLLAMA_BASE_URL", ""))
        require(url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"} and url.port is not None, "Ollama는 명시적 loopback HTTP 주소 필요")
        require(env.get("NEXT_PUBLIC_API_BASE_URL") == f"http://127.0.0.1:{int(env['API_PORT'])}", "FE/API 주소 불일치")
        require(all(env.get(key) == "true" for key in ("ENABLE_CIVIL_LLM_RUBRIC", "CIVIL_LLM_RUBRIC_USE_LLM_JUDGE", "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK", "PUBLIC_INSIGHT_ENABLED", "PUBLIC_INSIGHT_LLM_ENABLED")), "현재 제품의 rubric/judge/feedback/PublicInsight 기능을 유지해야 함")
        require(env.get("PUBLIC_INSIGHT_LLM_PROVIDER") == "fake" and env.get("REQUEST_SEGMENT_LLM_MODE") == "off", "현재 PublicInsight fake provider와 segment off 기준 필요")
        require(env.get("GENERATION_NUM_PREDICT") == "1536" and env.get("GENERATION_NUM_CTX") == "8192" and env.get("OLLAMA_TIMEOUT") == "180", "현재 제품 생성 1536/8192/180 기준 필요")
        return {"assets_dir": str(bundle), "generation_num_predict": int(env["GENERATION_NUM_PREDICT"]),
                "generation_num_ctx": int(env["GENERATION_NUM_CTX"]), "ollama_timeout": int(env["OLLAMA_TIMEOUT"]),
                "enabled_features": {key: env[key] for key in ("ENABLE_CIVIL_LLM_RUBRIC", "CIVIL_LLM_RUBRIC_USE_LLM_JUDGE", "ENABLE_PROMETHEUS_RUBRIC_FEEDBACK", "PUBLIC_INSIGHT_LLM_ENABLED")},
                "public_insight_provider": env["PUBLIC_INSIGHT_LLM_PROVIDER"]}

    check("configuration", configuration)
    def ports():
        api, ui = int(env["API_PORT"]), int(profile["ui_port"])
        require(api != ui and env["API_HOST"] == "127.0.0.1", "API/UI loopback 포트 분리 필요")
        api_free, ui_free = port_available("127.0.0.1", api), port_available("127.0.0.1", ui)
        require((action == "frontend" or api_free) and (action == "api" or ui_free), "실행 대상 포트 점유 중 (기존 프로세스를 종료하지 않음)")
        return {"api": api, "ui": ui, "api_port_free": api_free, "ui_port_free": ui_free, "existing_servers_contacted": False}
    check("ports", ports)
    assets = check("assets", lambda: inspect_bundle(bundle, env["DEFAULT_CHROMA_COLLECTION"]))
    check("real_assets", lambda: require(assets is not None and assets["declared_data_kind"] in {"real", "historical_preserved"}, "실제 자산 준비 미확인: synthetic/mixed/unknown은 실행 준비 완료로 인정하지 않음"))
    check("ollama_files", lambda: inspect_ollama(project_path(profile["ollama_models_dir"]), [
        env["OLLAMA_MODEL"], env["STRUCTURING_MODEL"], env["CIVIL_STRUCTURING_MODEL"],
        env.get("GROUNDING_FILTER_MODEL") or env["OLLAMA_MODEL"], env["CIVIL_LLM_RUBRIC_MODEL"],
        env["PROMETHEUS_FEEDBACK_MODEL"], env["PROMETHEUS_REVISION_MODEL"] or env["OLLAMA_MODEL"],
    ]))
    embedding = check("embedding_files", lambda: inspect_embedding(project_path(env["EMBEDDING_MODEL"])))
    check("embedding_dimension", lambda: require(assets is not None and embedding is not None and embedding["dimension"] == assets["embedding_dimension"], "임베딩 hidden_size와 저장 vector 차원 불일치/미확인"))
    def dependencies():
        require(sys.version_info[:2] == (3, 11), "Python 3.11 필요")
        for module in ("fastapi", "uvicorn", "chromadb", "sentence_transformers"):
            require(importlib.util.find_spec(module) is not None, f"설치 패키지 누락: {module}")
        node = shutil.which("node", path=env.get("PATH"))
        require(node is not None, "Node.js 없음")
        version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
        require(tuple(int(value) for value in version.lstrip("v").split(".")[:2]) >= (20, 9), "Node.js >=20.9 필요")
        require((ROOT / "frontend/node_modules/next/dist/bin/next").is_file(), "frontend npm ci 필요")
        return {"python": sys.version.split()[0], "node": version}
    check("dependencies", dependencies)
    return {"preflight_passed": all(item["ok"] for item in checks), "runtime_verified": False, "checks": checks}


def prepare_bundle(source: Path, target: Path, collection: str) -> dict:
    require(not source.is_symlink(), "source symlink 금지")
    source, target = source.resolve(), target.resolve()
    require(source != target and source not in target.parents and target not in source.parents, "원본과 분리된 새 target 필요")
    require(not target.exists(), "기존 target은 덮어쓰지 않음")
    before = inspect_bundle(source, collection)
    inventory = {str(path.relative_to(source)): (path.stat().st_size, path.stat().st_mtime_ns) for path in source.rglob("*") if path.is_file()}
    # copytree는 새 target만 만든다. 실패한 새 target도 자동 삭제하지 않는다.
    shutil.copytree(source, target)
    require(inventory == {str(path.relative_to(source)): (path.stat().st_size, path.stat().st_mtime_ns) for path in source.rglob("*") if path.is_file()}, "복사 도중 source 변경: 새 target을 사용하지 말 것")
    after = inspect_bundle(target, collection)
    require(before == after, "복사 후 metadata 불일치")
    return {"copied": True, "target": str(target), "assets": after, "runtime_ready": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare", "api", "frontend"))
    parser.add_argument("--profile", type=Path, default=ROOT / "configs/demo/local.example.json")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--collection", default="civil_cases_v3")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            require(args.source is not None and args.target is not None, "prepare는 --source와 새 --target을 명시해야 함")
            print(json.dumps(prepare_bundle(args.source, args.target, args.collection), ensure_ascii=False, indent=2))
            return 0
        profile = read_json(args.profile.resolve())
        result = check_profile(profile, args.action)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["preflight_passed"]:
            return 1
        if args.action == "check":
            return 0
        env = {**os.environ, **{key: str(value) for key, value in profile["env"].items()}}
        for key in ("CHROMA_DB_PATH", "COMPLAINT_INTELLIGENCE_DB_PATH", "RAW_DATA_PATH", "PROCESSED_DATA_PATH", "SAMPLES_DATA_PATH", "UI_CASES_DATA_PATH", "EMBEDDING_MODEL"):
            env[key] = str(project_path(env[key]))
        if args.action == "api":
            command = [sys.executable, str(ROOT / "scripts/run_api.py")]
        else:
            command = [shutil.which("node", path=env.get("PATH")), str(ROOT / "frontend/node_modules/next/dist/bin/next"), "dev", "--webpack", "--hostname", "127.0.0.1", "--port", str(profile["ui_port"])]
        return subprocess.run(command, cwd=ROOT if args.action == "api" else ROOT / "frontend", env=env, check=False).returncode
    except (OSError, ValueError, KeyError, sqlite3.Error, TypeError) as exc:
        print(json.dumps({"preflight_passed": False, "runtime_verified": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
