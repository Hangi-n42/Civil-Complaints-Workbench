# 계약 문서

- 문서 상태: canonical
- 문서 버전 v2.1
- 현행 대조 2026-11-06 · `8e9de051`
- 대조 범위는 로컬 기동·조회, Workbench SSE·저장, 관제 실행 출처입니다.
- 기준 코드:
  - `app/api/routers/retrieval.py`
  - `app/api/routers/generation.py`
  - `app/api/routers/complaint_intelligence.py`
  - `app/api/schemas/`
  - `app/complaint_intelligence/schemas.py`
  - `app/complaint_intelligence/duplicate_merger/schemas.py`
  - `frontend/lib/api.ts`
- 관련 문서:
  - `docs/60_specs/api_interface_spec.md`
  - `docs/60_specs/data_schema_spec.md`
  - `docs/60_specs/ui_workbench_spec.md`

## 디렉터리 역할

`docs/10_contracts`는 현재 살아있는 API, 데이터, FE-BE 계약의 기준 위치입니다. FE 담당자는 이 디렉터리를 먼저 보고 endpoint, 상태, 필드 계약을 확인합니다. BE 담당자는 Pydantic schema와 서비스 정책이 외부에 어떤 형태로 노출되는지 확인합니다.

## 권장 읽는 순서

1. [현재 API 계약](api/current_api_contract.md)에서 조회·검색·분석 진입점을 확인합니다.
2. [데이터 계약](data/current_data_contract.md)에서 입력·저장 결과·출처 필드를 확인합니다.
3. [Workbench FE-BE 계약](frontend/current_fe_contract.md)에서 검색·SSE·브라우저 저장을 확인합니다.
4. [관제 FE 계약](frontend/intelligence_fe_contract.md)에서 저장 조회·새 분석·출처 표시를 확인합니다.

## historical 문서 처리

`interfaces/week*`, `api/old_api_spec.md`, `schema/old_schema_contract.md`는 주차별 또는 과거 기준 계약입니다. 새 구현 판단의 기준으로 쓰지 말고, 변경 배경을 확인하는 historical 자료로 봅니다.

## `60_specs`와의 관계

`docs/60_specs`의 API, 데이터, UI 사양 중 현재 코드와 맞는 내용은 이 디렉터리로 흡수했습니다. `60_specs`는 source-spec으로 보존합니다.

## 실행과 이력

독립 실행의 자료와 주소는 [로컬 실행 안내](../30_manuals/local_dev_runbook.md)에 있습니다. 과거 평가 결과는 [모듈별 실험](../../experiments/README.md)에서 당시 조건과 함께 확인합니다.
