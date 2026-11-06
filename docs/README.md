# 민원 워크벤치 문서

민원 선택·처리 화면에서 유사 사례 근거와 초안을 검토하고, 관제 화면에서 이슈와 중복 후보를 살펴볼 수 있습니다.

| 독자 | 먼저 볼 문서 | 확인할 내용 |
| --- | --- | --- |
| 심사위원·외부 독자 | [시연 안내](../frontend/WORKBENCH_E2E_DEMO.md) · [검증 범위](#검증-범위) | 화면의 사용 순서, 어떤 자료를 사용했는지, 실제로 확인한 결과 |
| 직접 실행할 사람 | [로컬 실행 안내](30_manuals/local_dev_runbook.md) · [로컬 접속 범위](30_manuals/local_only_demo.md) | 별도 준비 자산, 사전 점검, 시작과 조회 |
| 개발·유지보수 담당자 | [제품과 설계](00_overview/README.md) · [API·데이터·화면 계약](10_contracts/README.md) | 구현 경로, 데이터와 저장 범위, 계약 |
| 관제 자료를 준비할 사람 | [관제 문서](20_domains/complaint_intelligence/README.md) · [자료와 개인정보 처리](20_domains/complaint_intelligence/pii_safety_policy.md) | 이슈·중복 판단, 출처 표시, 자료 사용과 공개 범위 |

## 검증 범위

| 확인한 내용 | 사용한 자료와 범위 |
| --- | --- |
| 해당 컴퓨터에서만 접속 | API와 화면을 루프백 주소로 기동하고 비로컬 Host·Origin 요청을 차단하는 동작을 확인했습니다. [접속 제한 PR #17](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/17) |
| 독립 기동과 조회 | 보존 자료의 복사본으로 로컬 API·화면을 기동하고, 기존 저장 벡터로 조회했습니다. [독립 실행 PR #19](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/19) |
| 관제 화면의 표시 | 합성 입력, 임시 DB, 모의 생성으로 저장 조회와 시연 출처 표시를 브라우저에서 확인했습니다. [시연 표시 PR #18](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/18) |
| 민원 답변 품질 | 고정 근거 평가가 첫 초안 생성 응답 직후 중단돼 최종 QA 완료는 0/2건입니다. 사람의 수정 시간은 미측정입니다. [품질 평가 이슈 #16](https://github.com/Hangi-n42/Civil-Complaints-Workbench/issues/16) |

[평가 절차와 기록](30_manuals/evaluation_runbook.md)은 사용한 입력과 실행 방법을 확인하는 경로입니다.

## 이력과 문서 관리

민원 전용 저장소의 분리 기준은 Git 태그 `split-base/astergraph-f7548838`입니다. 현재 실행의 기준은 [로컬 실행 안내](30_manuals/local_dev_runbook.md)와 [실행 매뉴얼 목차](30_manuals/README.md)입니다. 주차별 계약, 분리 전 설계와 실험은 작성 시점의 기록입니다. `90_archive/`, `experiments/`, `reports/`에 남은 결과를 인용할 때는 당시 코드·자료·실행 조건을 함께 확인합니다.
