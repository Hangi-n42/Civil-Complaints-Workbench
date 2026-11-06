# 공개 검증 기록

제품 기준은 main `8e9de051311b9db2ed5186031c03547f43a607ab`입니다. 과거 실행 결과는 당시 모델·자료·설정으로 읽습니다. 이 문서의 결과는 검증별로 확인한 실행 범위의 집계입니다. [기준 커밋](https://github.com/Hangi-n42/Civil-Complaints-Workbench/commit/8e9de051311b9db2ed5186031c03547f43a607ab)

## 공개 검증표

| 검증 | 실행 기준 | 관찰 결과와 범위 | 관련 구현·기준 |
| --- | --- | --- | --- |
| 병합 main 선별 회귀 | `8e9de051` | 백엔드 493개 통과, 기존 XPASS 1개. 품질 평가 도구를 제외한 선별 회귀입니다. | [로컬 접속 PR #17](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/17) · [독립 실행 PR #19](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/19) · [출처 표시 PR #18](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/18) |
| 독립 로컬 기동 | `3455724`, 보존 자산의 별도 복사본 | 설치된 환경에서 API와 Next를 기동했습니다. `/health` 등 API 4개와 화면 4개가 HTTP 200을 반환했고 대기 케이스 8개를 조회했습니다. | [PR #19](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/19) · [로컬 실행 안내](../docs/30_manuals/local_dev_runbook.md) |
| 저장 벡터 확인 | 보존 인덱스 준비본, 실행 커밋 미기록 | 사전 점검 메타데이터에서 저장 행 9,132개, 본문이 있는 행 9,130개, 빈 본문 2개를 확인했습니다. 기존 벡터 질의 1회에서 결과 3개를 받았습니다. 원래 임베딩 구성과 원천 대응은 미확인입니다. | [PR #19](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/19) |
| 인텔리전스 화면 | 합성 입력·임시 SQLite·fake provider, 실행 커밋 미기록 | API에서 새 분석 POST와 저장 조회 GET을 구분했습니다. 브라우저에서 저장 재조회, 재생 시각, 모의 생성, 시연용 지도 위치와 좌표·구조화 미확인 표시를 확인했습니다. | [PR #18](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/18) |
| 중복 민원 실제 holdout | `5cca9d4` 기반 별도 재실행 | 3개 통과, 1개 실패. recall 0.9333으로 기준 0.99에 미달했습니다. | [holdout 검사 기준](../app/tests/unit/test_duplicate_merger_real_holdout.py) |
| 답변 품질 평가 | 별도 Draft PR, 후속 실행 `4fac57d8` | 첫 Gemma 응답 직후 macOS 메모리 압력이 정상(1)에서 경고(2)로 바뀌어 중단했습니다. 모델 호출 1회, 완결 QA 0/2, Qwen 호출 0회, 사람 검토 0건입니다. 답변 품질·전체 QA 지연·사람 검토 부담은 미측정입니다. | [Draft PR #20](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/20) · [이슈 #16](https://github.com/Hangi-n42/Civil-Complaints-Workbench/issues/16) |

## 심사 자료에 붙일 기록

실행일·커밋·환경, 사용한 화면과 API, 실제·보존·재생·합성 자료의 범위, 생성 방식, 통과·실패·미측정 항목을 남깁니다. 반복 확인에는 같은 입력 범위와 실행 조건을 사용합니다. [평가 및 검증 안내](../docs/30_manuals/evaluation_runbook.md)

이 디렉터리에는 과거 실행 산출물도 보관되어 있습니다. 심사 자료에는 출처와 공개 권한을 확인한 집계·도식·합성 화면을 선정합니다. 새 실행의 원시 산출물은 접근을 제한한 보관본에서 관리합니다. [PII 보호와 공개 자료 기준](../docs/20_domains/complaint_intelligence/pii_safety_policy.md#공개-자료-검토)
