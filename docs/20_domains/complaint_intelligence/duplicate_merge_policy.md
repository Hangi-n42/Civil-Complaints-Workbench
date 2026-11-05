# Duplicate Merge Recommendation 정책

- 문서 상태: canonical
- 최종 확인일: 2026-06-21
- 기준 코드:
  - `app/complaint_intelligence/duplicate_merger/candidate_generator.py`
  - `app/complaint_intelligence/duplicate_merger/scoring.py`
  - `app/complaint_intelligence/duplicate_merger/merge_verifier.py`
  - `app/complaint_intelligence/duplicate_merger/draft_payload.py`
  - `app/complaint_intelligence/duplicate_merger/reply_context.py`
  - `app/complaint_intelligence/duplicate_merger/reply_safety.py`
  - `app/complaint_intelligence/duplicate_merger/schemas.py`
- 관련 문서:
  - `docs/10_contracts/data/current_data_contract.md`
  - `docs/10_contracts/frontend/intelligence_fe_contract.md`

## 목적

Duplicate Merge Recommendation Layer는 의미와 위치가 유사한 민원을 담당자가 함께 검토할 수 있도록 후보 그룹으로 제안합니다.

중요:

- candidate는 실제 민원 병합이 아닙니다.
- confirmed도 자동 발송이나 외부 민원 상태 변경이 아닙니다.
- 담당자 검토와 action gate를 위한 read-model입니다.

## 생성 흐름

```text
ComplaintIntelligenceEvent[]
  -> pair scoring
  -> location state 검증
  -> request type 분류
  -> MergeVerifier risk flag
  -> representative 선택
  -> DuplicateMergeRecord(candidate)
```

## scoring과 hard gate

scoring은 유사도를 계산합니다.

- semantic similarity
- entity/location overlap
- request type
- time window
- structured elements

hard gate는 자동 병합을 막아야 하는 위험을 판단합니다.

- 위치 충돌
- 요청 유형 불일치
- 안전/긴급 위험
- 법적 권리/기한 위험
- PII 위험
- 근거 부족

위험하다고 항상 group 생성을 막는 것은 아닙니다. 같은 위치/시설/주제에서 안전 이슈와 절차 문의가 함께 들어오면 후보는 보여주되 blocker로 confirm/draft reply를 막는 방향이 우선입니다.

## risk_flags

`risk_flags`는 담당자가 병합 전 확인해야 하는 경고입니다.

- `info`: 참고
- `warning`: 주의
- `blocker`: 자동 confirm 또는 draft reply 차단

## allowed_actions / blocked_actions

FE는 버튼 노출을 status만으로 판단하지 말고 반드시 `allowed_actions`, `blocked_actions`를 사용해야 합니다.

- candidate + blocker 없음: confirm/split/reject
- candidate + blocker 있음: split/reject
- confirmed: split/draft_reply
- split/rejected: action 없음

## 재분석과 담당자 판단 보존

- 동일 구성원의 재분석은 저장된 confirmed/split/rejected 레코드 전체를 보존하고 그 저장값을 반환합니다. candidate만 새 계산 결과로 갱신합니다.
- 검토된 구성원의 점수·위험·대표 선정·초안에 쓰는 입력(접수 시각, 마스킹 본문, 위치, 부서, 구조화 텍스트, 요청, 개체, 담당 업무, 분류, PII 상태)이 바뀌면 일반 분석과 중복 분석 모두 `409 DUPLICATE_GROUP_REVIEW_REQUIRED`로 중단합니다. 기존 판단과 이벤트를 덮어쓰지 않습니다.
- dashboard/public-insights 분석과 collector poll도 같은 보호를 적용합니다. 스케줄러는 `ran=false, reason=DUPLICATE_GROUP_REVIEW_REQUIRED`로 실패를 기록하며 분석 최소 건수 미달 시에도 근거를 덮거나 watermark/checkpoint를 진행하지 않습니다.
- 처리 상태, pipeline/prompt/model 버전, 현재 중복 점수에서 사용하지 않는 저장 embedding, 구조화 요소의 confidence/span/status, 분석 실행의 source/as_of 등 위 판단 입력 외 메타데이터 변경은 허용합니다.
- 구성원이 추가·제외되면 구성원 ID에 따른 별도 candidate가 생성되며 이전 그룹의 판단을 승계하지 않습니다. 이전 그룹과 근거는 남습니다.
- 변경 입력을 재검토하는 별도 버전/승인 UI는 이 정책에서 구현하지 않습니다. 같은 민원 ID의 검토 근거를 자동 교체하지 않으며, 변경된 사건은 별도 버전 ID로 전달해 새 후보로 검토해야 합니다.

## draft-reply와 reply-draft

- `draft-reply`: confirmed 그룹에서만 BE3 전달용 payload 생성
- `reply-draft`: confirmed 그룹에서만 실제 답변 초안 생성

두 endpoint 모두 candidate 상태에서 호출하면 안 됩니다.

## raw body 사용 금지

중복 병합 scoring과 draft payload는 PII-safe structured data와 masked text를 우선합니다. raw body를 scoring에 새로 추가하는 방식은 금지합니다.
