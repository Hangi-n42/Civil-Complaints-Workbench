# 평가 및 검증 안내

현재 제품 기준은 main `8e9de051311b9db2ed5186031c03547f43a607ab`입니다. 선별 회귀, 로컬 기동, 저장 벡터 검사, 합성 브라우저 확인과 별도 품질 평가의 실행 기준과 결과를 한곳에 기록합니다. [공개 검증표](../../reports/README.md#공개-검증표)

## 현재 로컬 시연 프로필

민원 QA 생성·루브릭 평가·조건부 재작성에는 `gemma4:31b-it-q4_K_M`을, 조건부 피드백에는 `qwen3.8:27b-q4_K_M`을 지정합니다. 새 행정 인사이트의 시연에는 `fake` provider를 지정합니다. 저장 결과 조회는 실행 기록에 남은 출처와 생성 방식을 표시합니다. [로컬 시연 프로필](../../configs/demo/local.example.json)

독립 기동에는 별도로 준비한 자산과 로컬 모델이 필요합니다. 사전 점검은 지정한 파일·SQLite 메타데이터·포트·설치 환경을 확인합니다. 브라우저와 API를 기동해 확인한 결과는 사전 점검과 분리해 기록합니다. [독립 로컬 실행 안내](local_dev_runbook.md)

## 답변 품질 평가

고정 근거 QA 평가 도구와 실행 기록은 별도 Draft PR에서 관리합니다. 제품 경로의 실제 추론, 사례 완료, 사람의 판정과 측정 시간을 각각 기록합니다. 부담 감소를 판단하려면 같은 범위의 수작업 기준선이 필요합니다. [Draft PR #20](https://github.com/Hangi-n42/Civil-Complaints-Workbench/pull/20) · [이슈 #16](https://github.com/Hangi-n42/Civil-Complaints-Workbench/issues/16)

재실행을 기록할 때는 실행 커밋, 입력과 자산 분류, 모델 태그와 역할, 호출 범위, 중단 조건을 먼저 고정합니다. 저장 벡터 조회, 기존 응답 재생, 합성 응답 검사와 새 모델 실행은 별개로 집계합니다. 새 질문의 검색 품질과 전체 QA 지연은 해당 경로를 실행한 결과로 측정합니다.

## 과거 평가 기록

| 평가 | 당시 기준과 해석 |
| --- | --- |
| Complaint Intelligence curated 평가 | `scripts/evaluate_complaint_intelligence_scenarios.py`를 사용했습니다. fake와 Local LLM 결과를 분리하며 기존 EXAONE 기록은 당시 모델·프롬프트 조건으로 읽습니다. |
| Complaint Intelligence holdout | `scripts/build_complaint_intelligence_holdout.py`로 입력을 만들고 `scripts/evaluate_complaint_intelligence_holdout.py`에서 처리 안정성과 정성 결과를 관찰했습니다. |
| Duplicate Merge 평가 | `scripts/evaluate_duplicate_merge_labeled_pairs.py`와 `scripts/evaluate_duplicate_merge_real_holdout.py`의 라벨 기준으로 비교했습니다. 실제 holdout의 미달 결과는 검증표에 유지합니다. |
| Retrieval v3 | `scripts/evaluate_retrieval.py`, `scripts/eval_ndcg.py`, `scripts/eval_bm25_morph.py` 등의 결과는 당시 검색 설정과 라벨 범위로 해석합니다. |
| 답변 규칙 기반 proxy | Q0~Q8 규칙 평가이며 현재 제품 QA의 모델 루브릭 평가와 별도입니다. [과거 proxy 평가 안내](llm_rubric_civil_reply_evaluation.md) |

## 공개 검증 기록 작성

합성 검사에는 임시 상태와 검사 목적에 맞춘 입력을 사용합니다. 확인한 경로, 검사 건수, 실패와 미측정 항목을 같이 기록합니다. 문서 변경은 `git diff --check`와 링크 대상 확인으로 점검합니다.

공개판에는 승인한 집계와 검수한 합성 화면을 사용합니다. 새 검증의 원시 실행 증거는 접근을 제한한 보관본에 둡니다. [PII 보호와 공개 자료 기준](../20_domains/complaint_intelligence/pii_safety_policy.md#공개-자료-검토)
