# 현재 FE-BE 계약

- 문서 상태: canonical
- API 주소·SSE·브라우저 저장 대조 2026-11-06 · `8e9de051`
- 기준 코드:
  - `frontend/lib/api.ts`
  - `frontend/lib/caseStatus.ts`
  - `frontend/app/page.tsx`
  - `frontend/app/workbench/page.tsx`
  - `app/api/routers/retrieval.py`
  - `app/api/routers/generation.py`
- 관련 문서:
  - `docs/60_specs/ui_workbench_spec.md`
  - `docs/10_contracts/api/current_api_contract.md`

## 기본 API base

독립 실행은 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8092`를 profile에서 지정합니다. 직접 개발 기동에서는 FE의 기본 API 주소가 `http://127.0.0.1:8001`, API 코드의 기본 포트가 `8000`이므로 실행 설정에서 맞춥니다. 준비·기동은 [독립 실행 안내](../../30_manuals/local_dev_runbook.md)에 있습니다.

## Workbench Search 흐름

FE 함수:

- `searchCasesApi`

Backend endpoint:

- `POST /api/v1/search`

FE 요청 필드:

- `complaintId`
- `query`
- `topK`
- `filters`
- `caseContext`

FE가 기대하는 응답:

- `query`
- `retrievedDocs`
- `results`
- `searchResults`
- `routingTrace`
- `routingHint`
- `strategyId`
- `routeKey`

Backend snake_case 응답은 `frontend/lib/api.ts`에서 camelCase 형태로 매핑됩니다.

검색과 초안 요청은 브라우저의 민원 ID와 요청 객체를 함께 확인합니다. 민원 전환과 새 요청에서 이전 요청을 취소하고, 현재 민원의 최신 요청에 해당하는 응답만 화면과 저장 상태에 반영합니다.

## Workbench QA 흐름

Workbench는 `streamQaApi`로 `POST /api/v1/qa/stream`을 호출합니다. `stage`로 진행도를 표시하고 `done`의 QA 응답을 사용합니다. 스트림을 열 수 없거나 `done` 없이 종료되면 `runQaApi`로 `POST /api/v1/qa`를 호출합니다. 명시적인 `error` 이벤트는 화면의 오류 상태로 전달합니다. 취소한 요청의 대체 호출도 차단합니다.

FE 요청 필드:

- `complaintId`
- `query`
- `routingHint`
- `routingTrace`
- `searchResults`

현재 검색 입력과 일치하는 검색 번들의 `routingHint`, `routingTrace`, 근거를 전달합니다. 해당 번들이 없으면 검색을 먼저 실행합니다. 민원 ID와 `routingHint`의 필수 조건은 [QA API 계약](../api/current_api_contract.md#post-apiv1qa)에 있습니다.

FE가 기대하는 응답:

- `answer`
- `citations`
- `limitations`
- `structuredOutput`
  - `summary`
  - `actionItems`
  - `requestSegments`
  - `segmentAnswers`

## 민원별 브라우저 저장

`workbench-last-draft:<encodeURIComponent(complaintId)>`에 `complaintId`, `draft`, `editorValue`, `searchQuery`, `searchBundle`, `draftQueryHash`를 저장합니다. 생성본과 편집본을 각각 복원하며, 이전 공통 키는 저장된 초안의 민원 ID가 일치할 때 읽습니다. 저장에 실패하면 편집값을 화면에 유지하고 경고를 표시합니다.

현재 검색 결과의 질의 해시가 저장된 초안의 질의 해시와 다르면 재생성 안내를 표시합니다. `case-status-overrides`의 검토중·처리완료는 브라우저의 작업 상태입니다. 공식 회신과 행정 처리는 담당자가 기존 업무 체계에서 수행합니다.

## 오류와 대체 데이터

케이스 목록은 API 실패나 빈 응답에서 mock 목록을 사용할 수 있습니다. Workbench 검색·초안은 API client가 함께 반환한 `error`를 확인해 화면에 오류를 표시합니다.

## 문서화 원칙

- 현재 호출 경로는 `/api/v1/search`, `/api/v1/qa`, `/api/v1/qa/stream`, `/complaint-intelligence/...`입니다.
- 과거 `web/`, Streamlit, old `/search` 표현은 historical 자료에서만 사용합니다.
