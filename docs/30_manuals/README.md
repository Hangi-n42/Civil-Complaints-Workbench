# 실행과 검증 안내

| 작업 | 문서 |
| --- | --- |
| 준비한 자산으로 로컬 시연 실행 | [독립 실행 안내](local_dev_runbook.md) · [접속 범위](local_only_demo.md) |
| 화면의 사용 순서 확인 | [민원 워크벤치 시연](../../frontend/WORKBENCH_E2E_DEMO.md) |
| 관제·중복 후보의 재생 자료 관리 | [관제 replay 관리](complaint_intelligence_demo_replay.md) · [중복 민원 시연](duplicate_merge_demo_flow.md) |
| 검색 색인 변경과 복구 | [안전한 재색인](safe_index_rebuild.md) · [ChromaDB LFS 정책](chromadb_lfs_policy.md) |
| 검증과 평가 이력 확인 | [평가 안내](evaluation_runbook.md) · [검증 범위](../README.md#검증-범위) |

시연을 시작할 때는 독립 실행 안내의 자산 복사와 사전 점검을 사용합니다. 관제 replay 재구성과 색인 변경은 각각의 유지보수 절차에서 대상을 확인한 뒤 진행합니다.

저장소의 Actions는 2026-11-06 기준 비활성 상태입니다. 저장된 Python Syntax Smoke 워크플로는 `compileall`로 Python 문법을 확인하는 설정입니다. [워크플로 정의](../../.github/workflows/deploy-production.yml)
