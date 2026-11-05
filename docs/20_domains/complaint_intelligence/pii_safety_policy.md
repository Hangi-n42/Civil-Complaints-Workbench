# Complaint Intelligence PII 보호 정책

- 문서 상태: canonical
- 최종 확인일: 2026-06-21
- 기준 코드:
  - `app/complaint_intelligence/pii.py`
  - `app/complaint_intelligence/schemas.py`
  - `app/complaint_intelligence/public_insights/evidence_pack.py`
  - `app/complaint_intelligence/duplicate_merger/draft_payload.py`
- 관련 문서:
  - `docs/10_contracts/data/current_data_contract.md`

## 기본 원칙

Complaint Intelligence는 민원 데이터를 관제/인사이트 용도로 재가공하므로 PII 노출 위험이 큽니다. 따라서 raw body를 외부 계약이나 리포트에 노출하지 않고, PII-safe 구조화 결과와 masked text를 우선 사용합니다.

## 입력 단계

`ComplaintIntelligenceEvent`는 다음 필드를 validator에서 마스킹합니다.

- `title`
- `body`
- `masked_text`
- `answer`
- `feedback`
- `reviewer_feedback`
- `structured_elements`
- `request_segments`
- `responsible_unit`
- `entity_texts`
- `civil_category`
- `urgency`
- `risk_level`

PII가 감지되면 다음 필드를 갱신합니다.

- `pii_detected`
- `pii_labels`
- `pii_status`

## EvidencePack

EvidencePack은 LLM에 전달되는 유일한 근거 입력입니다.

금지:

- raw complaint body
- 전화번호
- 상세 주소
- 주민 식별 가능 정보
- prompt 전체 dump

허용:

- `masked_text`
- PII-safe `structured_elements`
- region/department/status 등 운영 메타데이터
- evidence id

## Duplicate Merge

중복 병합은 PII-safe summary와 structured elements를 사용합니다. confirmed 그룹의 draft payload도 raw body를 담지 않아야 합니다.

## FE 표시

FE는 다음을 표시하지 않습니다.

- raw body
- 상세 주소
- 전화번호
- 원본 prompt
- raw LLM response

Evidence는 masked preview만 표시합니다.

## 직접 검색·QA의 일반 로그

- `/api/v1/search`, `/api/v1/qa`, `/api/v1/qa/stream`과 자동검색 프롬프트 경로는 query/derived_query/search_query 원문을 기록하지 않습니다. 입력은 검색·생성 계약에 따라 그대로 전달하며 이 로그 정책에서 별도로 마스킹하거나 의미를 바꾸지 않습니다.
- 시작 로그는 입력 길이·검색 건수/컨텍스트 수를 기록합니다. API 로그의 request ID·endpoint·latency·error code·retryable과 재시도 횟수, upstream 상태·단계·예외 종류로 진단합니다.
- 예외 메시지와 traceback에는 원문이 섞일 수 있어 일반 로그에 출력하지 않습니다. 검색 필터도 값을 기록하지 않고 개수만 남깁니다. SSE의 요청 ID는 진입 시 한 번 정해 오류 로그와 응답에서 공유합니다.
- 구조화 전 개인정보 마스킹과 검색 자료의 PII 제외 정책은 별도 보호 경계로 유지합니다. 이 정책은 기존 로그의 소급 삭제나 API 응답·브라우저 저장소 전체의 개인정보 검토를 포함하지 않습니다.

## reports/PR 산출물

PR에 포함하면 안 되는 산출물:

- raw LLM response
- prompt dump
- scenario별 checkpoint raw file
- PII 가능성이 있는 debug log

포함 가능한 산출물:

- 최종 summary report
- 사람이 읽는 markdown report
- PII 검사를 통과한 JSON report

## synthetic / real replay / evaluation 데이터

- synthetic: 실제 원문이 아닌 테스트 fixture. 사용 시 명시합니다.
- demo seed: FE demo를 위한 replay timeline 데이터.
- real replay: 실제 processed/raw 기반 데이터를 replay timeline으로 재배치한 데이터.
- evaluation: curated scenario 또는 holdout 평가 데이터.

모든 경우 PII-safe 검사를 전제로 합니다.
