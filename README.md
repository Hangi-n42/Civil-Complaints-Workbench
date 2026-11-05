# 민원 워크벤치

AsterGraph `f7548838f6e3d58698d986339bbc26ef9a07e2f3`에서 분리한 민원 전용 제품입니다. 민원 선택·처리 워크벤치·관리자 통계·인텔리전스, 관련 API·스케줄러·구조화·검색·답변 엔진과 전용 자료/검사를 보존합니다. 회사 지식 워크벤치와 회사 지식 API는 제외했습니다.

## 독립 로컬 시연

실행 DB·설치 패키지·모델 cache·개인 `.env`는 clone에 포함되지 않습니다. 보존된 실제 민원 인덱스와 관제 DB가 없으면 준비 완료로 처리하지 않습니다. 합성 검사는 실제 자산과 구분합니다. 원본 DB를 초기화하거나 모델을 자동 다운로드하지 않습니다.

[독립 실행 runbook](docs/30_manuals/local_dev_runbook.md)에 설치·새 대상 bundle 복사·읽기 점검·실행 순서를 정리했습니다. 전용 profile은 `configs/demo/local.example.json`이며 API `127.0.0.1:8092`와 화면 `127.0.0.1:3092`를 사용합니다. 루트에서:

```bash
cp -n configs/demo/local.example.json configs/demo/local.json
# runbook대로 기존 자산을 새 대상에 준비하고 profile 경로를 맞춘 뒤
.venv/bin/python scripts/standalone_demo.py check --profile configs/demo/local.json
.venv/bin/python scripts/standalone_demo.py api --profile configs/demo/local.json
# 별도 터미널
.venv/bin/python scripts/standalone_demo.py frontend --profile configs/demo/local.json
```

점검은 파일·SQLite metadata를 읽으며 모델 로딩/추론·Ollama 접속·서버 초기화는 하지 않습니다. `preflight_passed`와 실제 검색/답변 생성 성공은 구분합니다. 과거 기능/시연 계약은 `docs/00_overview/complaint_system_prd.md`, `frontend/WORKBENCH_E2E_DEMO.md`를 참고하십시오.

## 독립성·이력

독립 클론이며 Git 객체와 실행 DB를 원본에 연결하지 않습니다. `split-base/astergraph-f7548838` 태그가 분리 기준입니다. 이전 커밋에는 회사 지식 코드도 남아 있지만 현재 작업 트리는 민원 기능 범위입니다. 원격 저장소는 [Hangi-n42/Civil-Complaints-Workbench](https://github.com/Hangi-n42/Civil-Complaints-Workbench)입니다. 현재 공개 여부는 저장소 설정을 기준으로 확인합니다. 기존 자동화가 실행되지 않도록 GitHub Actions는 비활성화했습니다. 과거 설계/평가 문서는 당시 기록이며 현재 검증 결과와 구분합니다.

## 로컬 복원

Git LFS를 설치한 환경에서 다음과 같이 복원합니다.

```bash
git clone https://github.com/Hangi-n42/Civil-Complaints-Workbench.git
cd Civil-Complaints-Workbench
git lfs install --local
git lfs pull
```

법령 데이터와 일부 모델 파일은 Git LFS에 보존됩니다. 포인터만 남은 필수 파일은 사전 점검에서 실패합니다. 과거 버전 전체 다운로드는 현재 시연 실행의 필수 절차가 아닙니다.

Git에 기록된 코드·자료·이력·태그를 복원합니다. 실행 DB·개인 설정·설치 패키지·빌드 캐시는 포함하지 않으며, 실행 환경은 위 안내대로 준비합니다.
