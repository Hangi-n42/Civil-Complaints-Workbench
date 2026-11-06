# 민원 워크벤치 구현 아키텍처

- 문서 버전 v2.1
- 현행 코드 정적 대조 2026-11-06 · `8e9de051`
- 기준 코드:
  - `app/api/main.py`
  - `app/api/routers/retrieval.py`
  - `app/api/routers/generation.py`
  - `app/api/routers/complaint_intelligence.py`
  - `app/complaint_intelligence/service.py`
  - `frontend/app/workbench/page.tsx`
  - `frontend/app/intelligence/page.tsx`
- 관련 문서:
  - `docs/10_contracts/api/current_api_contract.md`
  - `docs/20_domains/complaint_intelligence/README.md`
  - `docs/30_manuals/local_dev_runbook.md`

## 전체 경계

독립 실행은 [전용 profile](../../configs/demo/local.example.json)으로 새 대상에 복사한 검색 인덱스·관제 SQLite·화면 케이스를 사용합니다. API는 `127.0.0.1:8092`, Next.js는 `127.0.0.1:3092`에서 실행합니다. 자료 준비와 기동 순서는 [독립 실행 안내](../30_manuals/local_dev_runbook.md)에 있습니다.

| 화면 | 진입점 | 주요 연결 |
|---|---|---|
| 민원 선택 | `/` | `/api/v1/ui/cases`의 케이스를 선택해 Workbench로 이동 |
| Workbench | `/workbench` | 유사 사례 검색, SSE 초안 생성, 브라우저 편집본 복원 |
| 민원 관제 | `/intelligence` | 저장된 dashboard와 중복 후보 조회, 담당자 검토 동작 |
| 관리자 통계 | `/admin` | `/api/v1/admin/overview`로 활성 컬렉션 통계 조회 |

화면 진입점은 `frontend/app/`에 있고, API는 `scripts/run_api.py`에서 `app.api.main:app`을 시작합니다. [폴더 구조](folder_structure.md)에 코드 위치를 정리합니다.

```text
원천/가공 민원 데이터
  ├─ ingestion/structuring
  │    └─ observation/result/request/context 4요소, PII-safe structured data
  ├─ RAG/Search/QA 메인 파이프라인
  │    ├─ /api/v1/search
  │    ├─ /api/v1/qa
  │    └─ Workbench UI
  └─ Complaint Intelligence sidecar
       ├─ IssueAlert
       ├─ PublicAgencyInsight
       ├─ Duplicate Merge Recommendation
       ├─ SQLite/InMemory repository
       └─ Intelligence dashboard UI
```

## 메인 RAG/QA 파이프라인

메인 파이프라인은 개별 민원에 대한 검색과 답변 생성을 담당합니다.

```text
query/case
  -> Topic/Complexity/Request Segment 분석
  -> 요청 분석·routing hint/trace
  -> 설정·필터에 따른 ChromaDB/Hybrid retrieval
  -> context mapping
  -> PromptFactory
  -> GenerationService
  -> response normalizer/citation validation
  -> Workbench 표시
```

주요 endpoint

- `POST /api/v1/search`
- `POST /api/v1/qa`
- `POST /api/v1/qa/stream`

Workbench는 검색어에 맞는 근거와 라우팅을 초안 생성에 사용합니다. 브라우저는 민원 전환 전의 응답이 현재 편집창에 들어가지 않도록 요청을 구분하고, 민원별 초안과 편집본을 로컬 저장소에 보관합니다. [FE-BE 계약](../10_contracts/frontend/current_fe_contract.md)에서 저장 키와 SSE 대체 경로를 확인합니다.

## Complaint Intelligence sidecar

Complaint Intelligence는 RAG/QA 요청 처리 경로에 직접 끼어들지 않는 sidecar입니다. 분석 결과는 repository에 저장되고 FE는 dashboard read-model을 조회합니다.

```text
ComplaintIntelligenceEvent[]
  -> ComplaintIntelligenceService.run_analysis
  -> IssueDetectionEngine
  -> PublicAgencyInsightEngine
  -> EvidencePack 저장
  -> IssueAlert/PublicAgencyInsight 저장
  -> dashboard read-model 조회
```

주요 endpoint

- `POST /complaint-intelligence/run-analysis`
- `GET /complaint-intelligence/dashboard`
- `POST /complaint-intelligence/dashboard/run-analysis`
- `GET /complaint-intelligence/issue-alerts`
- `GET /complaint-intelligence/public-insights`
- `GET /complaint-intelligence/public-insights/{insight_id}/evidence-pack`

조회 응답의 `operation=stored_read`와 분석 응답의 `operation=new_analysis`로 실행을 구분합니다. `provenance_runs`에는 최근 최대 5개의 저장된 실행 출처가 담깁니다. 시나리오 시각·대표 위치와 생성 방식은 실행 단위로 표시합니다. 각 카드와 실행 기록의 대응은 미확인입니다. [관제 FE 계약](../10_contracts/frontend/intelligence_fe_contract.md)에 표시 규칙이 있습니다.

## Duplicate Merge Recommendation Layer

중복 병합 레이어는 유사 민원 묶음을 담당자에게 제안하는 read-model입니다. 실제 민원 DB의 상태를 직접 바꾸지 않습니다.

```text
ComplaintIntelligenceEvent[]
  -> DuplicateCandidateGenerator
  -> scoring + MergeVerifier
  -> DuplicateMergeRecord(candidate)
  -> 담당자 action: confirm/split/reject
  -> confirmed에서만 draft-reply 또는 reply-draft 허용
```

핵심 원칙:

- blocker risk가 있으면 confirm과 draft reply를 막습니다.
- candidate는 제안 상태일 뿐 실제 병합이 아닙니다.
- confirmed도 내부 read-model 상태이며 자동 발송이 아닙니다.

## FE Intelligence Dashboard

`frontend/app/intelligence/page.tsx`는 다음 데이터를 함께 사용합니다.

- dashboard summary
- issue alert cards
- public insight cards
- duplicate group triage
- hotspot map
- evidence pack drawer

FE는 LLM을 직접 호출하지 않습니다. 저장된 read-model을 조회하고, 필요한 담당자 action만 API로 요청합니다.

## 저장소

Complaint Intelligence repository는 interface로 분리되어 있습니다.

- `InMemoryComplaintIntelligenceRepository`: 테스트·개발용 메모리 저장소
- `SQLiteComplaintIntelligenceRepository`: 로컬 demo/운영 준비용 기본 영속 저장소

향후 Postgres 같은 운영 DB로 전환할 때는 repository interface를 기준으로 교체합니다.

## 평가와 replay

독립 실행은 profile에 지정된 보존 자료 묶음을 사용합니다. 개발용 재생에는 관제·중복 검토 시나리오 입력을 사용하고, 평가는 선별 사례와 별도 holdout으로 나눕니다. 자료 준비 조건과 결과 해석은 [재생 안내](../30_manuals/complaint_intelligence_demo_replay.md)와 [평가 안내](../30_manuals/evaluation_runbook.md)에 있습니다.

평가 결과는 `reports/`에 저장합니다. PR에는 최종 리포트 중심으로 포함하고, raw response/prompt/checkpoint는 포함하지 않는 것이 기본 원칙입니다.

## 검색과 평가의 실행 경계

- `app/retrieval/service.py`에서 hybrid는 선택 전략이 hybrid이고 필터가 없을 때 사용된다. routing 분석만으로 모든 요청이 다른 랭킹 전략을 사용하는 것으로 해석하지 않는다.
- 독립 실행 profile은 Gemma 생성·평가·재작성과 조건부 Qwen 피드백을 설정합니다. 자동 관제 스케줄러를 끄고 PublicAgencyInsight는 `fake` provider를 사용합니다. [기술 스택](dev_stack.md)에 구성 근거가 있습니다.
- `app/evaluation/civil_llm_rubric.py`의 호출 그룹은 Q2 / Q3·Q4·Q5 / Q1·Q7 / Q6 / Q0으로 최초 5회이며 같은 요청에서 Q2를 재사용한 재평가는 4회다. Q0~Q7 응답 점수 구조는 유지된다.

2026-09-26의 EXAONE 생성·재작성과 Qwen 4B 온라인 평가 선택은 [이전 ADR](adr_architecture_decision_record.md)에 당시 기록으로 남깁니다. 이 문서의 확인 범위는 라우터·실행 profile·FE 소스의 정적 대조입니다.
