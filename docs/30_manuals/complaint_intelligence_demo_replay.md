# Complaint Intelligence Demo/Replay Runbook

시연에서 기존 관제 결과를 읽을 때는 [독립 실행 안내](local_dev_runbook.md)의 복사 자산을 사용합니다. 아래는 관리자가 재생 자료를 다시 구성할 때 참고하는 절차입니다.

`npm run replay:prepare`와 real replay 준비 스크립트는 대상 DB를 초기화합니다. 재구성할 때는 스크립트의 `--help`에서 경로 인자를 확인하고 DB·생성 seed·보고서 대상을 별도 작업 경로로 지정합니다. 아래 기본 경로는 당시 구성의 기록입니다. 기존 DB의 검토·상태를 보존해야 하는 경우 기본 명령을 실행하지 않습니다.

- 문서 상태: runbook
- 최종 확인일: 2026-06-21
- 기준 코드:
  - `scripts/build_complaint_intelligence_demo_seed.py`
  - `scripts/seed_complaint_intelligence_demo.py`
  - `scripts/prepare_complaint_intelligence_real_replay.py`
  - `app/complaint_intelligence/service.py`
  - `app/api/routers/complaint_intelligence.py`
- 관련 문서:
  - `docs/50_issues/complaint_intelligence_demo_seed.md`
  - `docs/10_contracts/frontend/intelligence_fe_contract.md`

## 목적

FE Intelligence dashboard가 빈 화면으로 보이지 않도록, 실제 분석 파이프라인을 통과한 IssueAlert/PublicAgencyInsight/Duplicate Merge read-model을 준비합니다.

중요:

- mock 카드나 하드코딩 dashboard 응답을 만들지 않습니다.
- 실제 `ComplaintIntelligenceService.run_analysis(...)` 경로를 사용합니다.
- demo seed와 real replay seed를 구분합니다.
- 분석 파이프라인 실행과 실제 모델 호출은 구분합니다. 저장된 실행 기록의 provider 클래스·성공/대체 생성 횟수로 생성 방식을 표시합니다.

## Demo seed

입력/출력:

- seed: `data/demo/complaint_intelligence_demo_events.json`
- build report: `reports/complaint_intelligence_demo_seed_build_report.json`
- run report: `reports/complaint_intelligence_demo_seed_run_report.json`

생성:

```powershell
civil\Scripts\python.exe scripts\build_complaint_intelligence_demo_seed.py
```

적재/분석:

```powershell
civil\Scripts\python.exe scripts\seed_complaint_intelligence_demo.py
```

## Real replay seed

입력/출력:

- seed: `data/complaint_intelligence/complaint_intelligence_real_replay_events.json`
- build report: `reports/complaint_intelligence_real_replay_seed_build_report.json`
- run report: `reports/complaint_intelligence_real_replay_seed_run_report.json`

준비:

```powershell
civil\Scripts\python.exe scripts\prepare_complaint_intelligence_real_replay.py
```

실행 스크립트는 현재 repo의 실제 옵션을 확인한 뒤 사용합니다.

## Dashboard 확인

Backend 실행 후:

```http
GET /complaint-intelligence/dashboard
GET /complaint-intelligence/issue-alerts
GET /complaint-intelligence/public-insights
GET /complaint-intelligence/duplicate-groups
```

FE 확인:

```text
/intelligence
```

### 출처와 실행 표시 읽기 (2026-11-05)

화면의 `저장 결과 다시 조회`는 GET 조회입니다. 새 분석을 실행하거나 원본 민원 접수·현장 감지를 수행하지 않습니다. API의 `operation=stored_read`와 `new_analysis`가 저장 조회와 명시적 분석 실행 응답을 구분합니다.

`자료 출처와 실행 구분`은 최근 실행 기록 최대 5개의 저장된 메타데이터를 보여줍니다. 원문 출처 파일명·출처 정책은 입력 준비자의 선언이며 제품이 원문 사실이나 출처 진위를 자동 검증한 결과가 아닙니다. 누적 저장된 각 카드와 특정 실행 사이의 연결을 보증하지 않습니다.

- 실제 자료 발췌도 replay에서는 접수 시각을 시나리오 기준으로 배치할 수 있습니다. 분석 기준 시각과 실행 기록 시각을 나누어 읽습니다.
- 이번 준비 명령이 시나리오를 새로 만든 경우에는 배치 좌표·템플릿 구조화를 표시합니다. 기존 seed 재사용(`--skip-build` 또는 빈 입력 fallback)은 원본 좌표나 구조화 근거를 추정하지 않습니다.
- `모의 생성(fake)`는 실제 모델 추론을 실행한 결과가 아닙니다. `로컬 모델 호출 결과`와 `규칙/템플릿 대체 생성`은 저장된 성공·대체 생성 기록에 따라 나누어 표시하며, 생성 내용의 품질을 보증하지 않습니다.
- 메타데이터가 없는 기존 실행은 `미확인`입니다. 현재 provider 설정, seed 파일, source_name으로 과거 실행의 출처나 생성 방식을 소급 채우지 않습니다. 중복 후보 분석처럼 생성 지표를 남기지 않는 실행도 생성 방식은 미확인으로 표시합니다.
- 지도는 저장된 분석 중심 또는 시연용 행정구역 대표 위치입니다. center가 없으면 프런트에서 대표 위치를 표시할 수 있으며, 이 대체 표시는 실제 민원 주소 좌표가 아닙니다. 원과 반경도 실제 발생 범위를 확인한 값이 아닙니다. 로컬 후보 확정·추천·초안은 실제 행정 시스템 병합, 회신 발송, 행정 처리 완료를 뜻하지 않습니다.

직접 API 분석을 호출할 때는 준비한 입력에서 확인된 `source_provenance`만 전달합니다. 생략한 정보는 미확인으로 남습니다. 제공된 출처 메타데이터는 실행 기록에 저장되고 `/complaint-intelligence/analysis-runs`와 dashboard 조회에 그대로 사용됩니다. 기존 원문·근거·확정 상태는 이 표시를 위해 변경하지 않습니다.

## DB 주의사항

demo/basic DB와 real replay DB를 섞지 마십시오.

- demo seed 검증: demo source_name과 demo DB path를 사용
- real replay 검증: real replay source_name과 별도 DB path 사용 권장

SQLite 파일은 로컬 산출물입니다. PR에 포함하기 전 PII와 용량 정책을 확인합니다.

## PII 확인

seed와 report에는 raw PII가 없어야 합니다. 최소한 전화번호, 상세 주소 패턴을 검색합니다.

```powershell
rg "010-|[0-9]{2,3}-[0-9]{3,4}-[0-9]{4}" data reports
```

## 흔한 실패

- alert/insight가 0건: threshold, recent window, baseline 조건 확인
- dashboard가 비어 있음: seed script가 같은 DB path를 사용했는지 확인
- EvidencePack이 없음: PublicAgencyInsight 생성/저장 단계와 repository 상태 확인
