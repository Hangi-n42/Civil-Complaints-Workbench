# 기존 검색 자료를 보존하는 전체 재색인

## 동작과 성공 경계

`POST /api/v1/index`의 `action=bulk`는 정규화·개인정보 정책·빈 텍스트 검사를
마친 뒤 유효 문서가 0개이면 `400 BAD_REQUEST`를 반환한다. 기존 컬렉션은
유지한다. 성공은 기존과 같이 `202`와 `indexed_count`, `index_name`, `records`를
반환하며 저장 실패는 `500 PROCESSING_ERROR`로 구분한다. `incremental`은
현재 사용 중인 컬렉션에 추가/수정한다.

bulk 자료는 새 `rebuild_<UUID>` 물리 컬렉션에 Chroma의 최대 배치 크기로
저장한다. 저장한 ID 집합과 건수를 확인한 뒤 논리 이름의 활성 포인터를
한 번 교체한다. 임베딩·중간 배치·검증·포인터 저장 실패, commit 전 전달된
취소 예외는 기존 포인터를 유지한다. 실패한 bulk 임시 컬렉션은 정리를
시도하며, 정리가 실패해도 기존 자료는 삭제하지 않는다.

Chroma 1.5.5의 이름 변경 API는 두 이름의 원자 교환을 제공하지 않는다.
따라서 `CHROMA_DB_PATH/collection_aliases/<논리이름 SHA256>.json`에
`logical_name`, `active_name`, `previous_name`을 기록한다. 같은 디렉터리의
임시 파일을 flush/fsync한 뒤 실행하는 `os.replace`가 **사용 대상 전환의
commit 경계**다. 기존 물리 컬렉션은 삭제하지 않는다. 포인터가 없는 기존
DB는 이전 이름을 그대로 사용한다. 포인터 손상이나 누락된 활성 컬렉션은
빈 컬렉션을 새로 만들어 숨기지 않고 오류로 처리한다.

이는 지원 reader가 한 번 읽는 활성 이름의 원자 교체다. Chroma DB 전체
트랜잭션, 여러 writer의 격리, 이미 진행 중인 검색 요청 전체의 snapshot,
전원 장애 이후 파일 시스템 내구성까지 보장하는 의미는 아니다. HTTP 연결
취소가 서버의 동기 저장 중단을 보장하지 않는다. commit 이후 응답이
유실되면 전환은 이미 완료됐을 수 있으므로 활성 이름을 확인해야 한다.

## CLI 전체 재구축

`scripts/build_index.py --rebuild`는 전체 작업용 임시 컬렉션 이름을 로그에
남기고 모든 REST 배치를 그곳에 incremental로 저장한다. 모든 배치가
성공하고 입력 파일/구조화 오류가 없을 때만 마지막에
`POST /api/v1/index/publish`를 호출한다. 개인정보 검토/격리 제외는 기존
정책대로 유지한다. 유효 자료가 0개이거나 저장 응답의 일부 실패가 있으면
발행하지 않는다. 두 번째 이후 배치의 실패·취소도 기존 사용 대상을 유지한다.
CLI는 성공 응답의 누적 저장 건수를 `expected_count`로 전달한다. 발행 직전
실제 저장 건수가 다르면 전환하지 않는다. 서로 다른 배치의 동일 ID 또는
정규화 후 충돌한 ID가 upsert로 덮어써져 자료가 줄어든 경우도 거절한다.

`--no-rebuild`와 `--resume`은 기존과 같이 현재 컬렉션에 incremental로
추가한다. 여러 요청에 걸친 incremental 작업 자체는 원자적이지 않다.
재구축 실패 후 남은 임시 컬렉션은 조사/복구를 위해 보존하며, 해당 이름으로
`--collection-name rebuild_<UUID> --resume --no-rebuild`를 사용해 완료할 수
있다. 원래 논리 이름으로 resume하면 이전 정상 자료에 추가하는 작업이 된다.

완료 여부와 저장 ID를 확인한 뒤 다음 요청으로 명시적으로 발행한다.

```json
POST /api/v1/index/publish
{
  "collection_name": "civil_cases_v1",
  "staged_collection_name": "rebuild_<로그에 기록된 UUID>"
}
```

`staged_collection_name`은 논리 alias를 다시 해석하지 않는 **물리 이름**이다.
발행은 해당 물리 컬렉션의 존재와 0보다 큰 건수를 확인한다. 이 관리 API는
원천 자료 전체의 완전성을 자동 판정하지 않으므로 CLI 외부 호출자는 모든
배치의 성공을 먼저 확인해야 한다. 선택 필드 `expected_count`를 주면 실제
건수 일치도 검사한다. raw 물리 컬렉션 복구처럼 예상 건수를 별도로 지정하지
않는 기존 호출도 유지한다. 같은 대상을 다시 발행해도 이전 이름의
복구 정보는 덮어쓰지 않는다.

## 명시적 복구와 운영 범위

`scripts/reindex_be1_direct.py --rebuild`도 전체 staging 저장·실제 건수 검증 뒤에
한 번 발행한다. 입력 읽기·구조화·어느 배치라도 실패하면 기존 사용 대상을 유지한다.
관리용 import/overlay/repair의 교체도 새 물리 컬렉션의 저장을 끝낸 뒤 전환한다.
조회·export·검사·resume·관리 API는 논리 이름을 현재 활성 물리 이름으로 해석한다.
기존 평가 스크립트는 읽는 이름만 이 공통 해석에 맞췄으며 과거 평가 자료·수치나
계산 로직을 갱신하거나 실험을 다시 실행하지 않았다.

API writer는 단일 `get_retrieval_service()` 인스턴스에서 직렬로 실행한다.
CLI도 이 서버를 통해 쓴다. 동시에 다른 서버 worker/프로세스/직접 Chroma
클라이언트가 쓰는 경로의 잠금과 incremental 충돌 처리는 지원하지 않는다.
그 경로의 쓰기를 중지한 뒤 한 writer로 실행한다. 직접 도구에서 논리
이름을 받을 때는 `resolve_collection_name(persist_directory, collection_name)`으로
활성 이름을 해석한다. 외부 Chroma 클라이언트가 원래 이름만 조회하면 보존된
이전 자료를 볼 수 있다. 기존 물리 이름으로 접근하는 방식은 복구에 사용한다.

실패 전 사용하던 물리 이름은 포인터의 `previous_name`에서 확인한다.
먼저 writer를 멈추고 해당 물리 컬렉션의 존재·건수를 검증한 뒤 관리 API로
그 이름을 다시 발행한다.

```json
POST /api/v1/index/publish
{
  "collection_name": "civil_cases_v1",
  "staged_collection_name": "이전 물리 컬렉션 이름"
}
```

API를 사용하지 않고 직접 복구할 때도 이전 물리 이름을 지정한다.

```python
import chromadb
from app.retrieval.vectorstores.collection_alias import activate_collection

persist = "data/chroma_db"
logical = "civil_cases_v1"
previous = "civil_cases_v1"  # 해당 포인터의 previous_name 확인 후 지정
collection = chromadb.PersistentClient(path=persist).get_collection(previous)
assert collection.count() > 0
activate_collection(persist, logical, previous)
```

직접 복구 뒤에는 API 프로세스를 재시작해 기존 검색/관리 캐시를 비운다.
이전/실패 물리 컬렉션의 자동 삭제는 하지 않는다. 충분한 디스크 공간이
필요하며, DB를 복사할 때 `collection_aliases` 디렉터리도 함께 보존한다.
