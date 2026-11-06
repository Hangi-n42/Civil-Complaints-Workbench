# Complaint Intelligence PII 보호 정책

- 인텔리전스의 근거 보호와 시연·평가 자료의 공개 기준을 다룹니다.
- 공개 자료 기준을 2026-11-06에 보완했습니다.
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

공공기관 인사이트 생성에는 마스킹한 EvidencePack을 근거로 사용합니다.

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

## 인텔리전스 근거 표시

인텔리전스 화면의 근거는 마스킹한 미리보기로 표시합니다. 다음 항목은 근거 화면의 노출 제한 대상입니다.

- raw body
- 상세 주소
- 전화번호
- 원본 prompt
- raw LLM response

## 직접 검색·QA의 일반 로그

- `/api/v1/search`, `/api/v1/qa`, `/api/v1/qa/stream`과 자동검색 프롬프트 경로는 query/derived_query/search_query 원문을 기록하지 않습니다. 입력은 검색·생성 계약에 따라 그대로 전달하며 이 로그 정책에서 별도로 마스킹하거나 의미를 바꾸지 않습니다.
- 시작 로그는 입력 길이·검색 건수/컨텍스트 수를 기록합니다. API 로그의 request ID·endpoint·latency·error code·retryable과 재시도 횟수, upstream 상태·단계·예외 종류로 진단합니다.
- 예외 메시지와 traceback에는 원문이 섞일 수 있어 일반 로그에 출력하지 않습니다. 검색 필터도 값을 기록하지 않고 개수만 남깁니다. SSE의 요청 ID는 진입 시 한 번 정해 오류 로그와 응답에서 공유합니다.
- 구조화 전 개인정보 마스킹과 검색 자료의 PII 제외 정책은 별도 보호 경계로 유지합니다. 이 정책은 기존 로그의 소급 삭제나 API 응답·브라우저 저장소 전체의 개인정보 검토를 포함하지 않습니다.

## reports/PR 산출물

새 검증의 원시 모델 응답, 전체 프롬프트, 시나리오별 체크포인트와 디버그 로그는 접근을 제한한 보관본에 둡니다. 공개 리포트와 PR에는 아래 기준을 충족한 집계·도식·검수된 합성 화면을 사용합니다. [공개 검증표](../../../reports/README.md#공개-검증표)

## 공개 자료 검토

| 표기 | 확인할 내용 |
| --- | --- |
| 실제 | 실제 민원에서 수집하거나 발췌한 자료입니다. 원천과 제공 범위의 확인 상태를 기록합니다. |
| 보존 | 기존에 저장한 인덱스·DB·실행 결과의 복사본입니다. 원래 자료와의 대응이나 임베딩 설정에 기록이 없으면 미확인으로 표시합니다. |
| 재생 | 입력을 시나리오에 맞춰 재배치한 자료입니다. 출처와 별개로 재생한 시각, 좌표, 구조화 필드의 범위를 표시합니다. |
| 합성 | 테스트나 시연을 위해 만든 입력입니다. |

자료 분류와 별도로 실제 모델, fake provider, 템플릿 등 생성 방식을 기록합니다. 평가에는 입력 범위, 라벨 기준, 확인 상태를 남깁니다. 출처나 변형 근거가 비어 있는 기록은 미확인으로 유지합니다.

공개할 파일마다 제공처, 원천과 파생물의 사용 조건, 공개·재배포 허용 범위를 확인합니다. 확인을 마친 자료를 공개 목록에 넣습니다. 본문·첨부·캡처·로그·파일 메타데이터를 함께 검토하고, 사건 식별자나 정확한 시각·상세 위치를 다른 정보와 결합해 재식별할 수 있는지도 살핍니다. 자동 마스킹과 패턴 검사는 검토 후보를 찾는 데 사용합니다.

저장소의 공개 범위를 점검할 때는 현재 추적 파일, 과거 커밋, 브랜치·태그, LFS와 PR·릴리스 첨부를 구분해 확인합니다. [GitHub의 민감 자료 정리 안내](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
