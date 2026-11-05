"""로컬 Chroma 논리 이름을 보존하는 작은 활성 컬렉션 포인터."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def get_active_collection(client, collection_name: str):
    """기존 로컬 Chroma 관리/평가 reader가 논리 이름으로 조회할 때 사용한다."""
    persist_directory = client.get_settings().persist_directory
    return client.get_collection(resolve_collection_name(persist_directory, collection_name))


def _alias_path(persist_directory: str | Path, collection_name: str) -> Path:
    key = hashlib.sha256(collection_name.encode("utf-8")).hexdigest()
    return Path(persist_directory) / "collection_aliases" / f"{key}.json"


def has_collection_alias(persist_directory: str | Path, collection_name: str) -> bool:
    """원래 물리 이름으로 복구된 포인터도 기존 DB 생성 경로와 구분한다."""
    return _alias_path(persist_directory, collection_name.strip()).exists()


def resolve_collection_name(persist_directory: str | Path, collection_name: str) -> str:
    """포인터가 없는 기존 DB는 원래 이름을 사용하고 손상된 포인터는 거절한다."""
    collection_name = collection_name.strip()
    path = _alias_path(persist_directory, collection_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return collection_name
    if payload.get("logical_name") != collection_name or not isinstance(payload.get("active_name"), str):
        raise ValueError(f"잘못된 컬렉션 포인터: {path}")
    if not payload["active_name"].strip():
        raise ValueError(f"비어 있는 컬렉션 포인터: {path}")
    return payload["active_name"]


def activate_collection(persist_directory: str | Path, collection_name: str, physical_name: str) -> None:
    """검증이 끝난 이름만 발행한다. 기존 물리 컬렉션은 삭제하지 않는다."""
    collection_name = collection_name.strip()
    path = _alias_path(persist_directory, collection_name)
    previous_name = resolve_collection_name(persist_directory, collection_name)
    if previous_name == physical_name:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with pending.open("x", encoding="utf-8") as handle:
            json.dump({"logical_name": collection_name, "active_name": physical_name,
                       "previous_name": previous_name}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        # 단일 로컬 파일 교체가 commit 경계다. 동시 writer는 호출자가 직렬화한다.
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
