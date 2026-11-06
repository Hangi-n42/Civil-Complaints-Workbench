# 민원 워크벤치 폴더 구조

- 문서 버전 v2.2
- 현행 경로 대조 2026-11-06 · `8e9de051`

| 경로 | 역할·현재 상태 |
|---|---|
| `app/api/` | FastAPI 진입점, 라우터, 요청·응답 스키마 |
| `app/retrieval/`, `app/generation/`, `app/evaluation/` | 검색·답변 생성·평가 |
| `app/complaint_intelligence/` | 민원 관제·중복 판단 |
| `app/ui/` | 기존 Streamlit 코드와 현재 케이스 API가 사용하는 데이터 어댑터 |
| `frontend/app/`, `frontend/lib/` | Next.js 화면과 API 변환·브라우저 저장 |
| `configs/demo/`, `scripts/standalone_demo.py` | 독립 실행 profile과 자료 복사·점검·기동 |
| `scripts/` | 전처리, 색인, 관제 재생, 평가 도구 |
| `data/` 및 설정된 Chroma 디렉터리 | 로컬 데이터·인덱스. 실제 경로는 설정과 실행 안내 확인 |
| `docs/00_overview/` | 민원 PRD·아키텍처·스택과 이전 ADR |
| `docs/10_contracts/`, `docs/20_domains/` | 민원 계약과 기능별 정책 |
| `docs/30_manuals/` | 실행·운영 안내 |
| `docs/90_archive/` | 종료된 계획·이전 문서 감사 |
| `docs/90_archive/complaint_system/` | 과거 민원 ADR v2.2·UX 시나리오·검색 선정서·MVP/WBS |
| `experiments/<module>/` | 모듈별 과거 실험 보고서 |
| `reports/` | 기존 스크립트 산출물·평가 절차 |

화면과 API의 연결은 [아키텍처](complaint_system_architecture.md), 자료의 실행 경로는 [독립 실행 안내](../30_manuals/local_dev_runbook.md)에 있습니다. 분리 전 K2와 회사 지식 경로는 `split-base/astergraph-f7548838` 태그의 작업 트리와 [이전 ADR](adr_architecture_decision_record.md)에서 확인합니다.

## 2026-09-28 불필요한 추적 파일 정리

루트 `schemas/`, `tests/fixtures/`, `.tmp_issue_bodies/`와 `logs/`에 남아 있던 과거 추적 파일을 삭제했다. 코드·테스트에서 해당 파일을 읽는 참조는 발견하지 못했다. `app/api/schemas/`, 각 모듈의 `schemas.py`, `app/tests/fixtures/`는 별개이며 유지한다. `logs/`는 실행 시 다시 만들어지는 로컬 출력 경로로 계속 사용하며 기존 Git 제외 규칙을 유지한다.

2026-09-28: 개인정보 파이프라인·ko-pii 어댑터·후검사·위험 정책을 `app/structuring/pii/`로 통합했다. ingestion과 관련 테스트는 새 경로를 사용하며 비어 있는 `src` 패키지를 제거했다. 처리 규칙은 변경하지 않았다.
