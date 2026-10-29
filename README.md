# 민원 워크벤치 (독립 보존 저장소)

AsterGraph `f7548838f6e3d58698d986339bbc26ef9a07e2f3`에서 분리한 민원 전용 제품입니다. 민원 선택·처리 워크벤치·관리자 통계·인텔리전스, 관련 API·스케줄러·구조화·검색·답변 엔진과 전용 자료/검사를 보존합니다. 회사 지식 워크벤치와 회사 지식 API는 제외했습니다.

## 실행

Python 환경에서 `pip install -r requirements.txt`, 프런트엔드에서 `npm ci`로 의존성을 설치합니다. `.env.example`을 참고해 이 저장소 전용 DB·포트를 설정하십시오. 원본 프로젝트의 실행 DB나 `.env`는 복사하지 않았습니다.

- API: `python scripts/run_api.py` (`API_PORT`로 별도 포트 지정)
- 화면: `cd frontend && npm run dev -- --port 3092`
- 과거 시연/업무 계약: `docs/00_overview/complaint_system_prd.md`, `frontend/WORKBENCH_E2E_DEMO.md`

## 독립성·이력

독립 클론이며 Git 객체와 실행 DB를 원본에 연결하지 않습니다. `split-base/astergraph-f7548838` 태그가 분리 기준입니다. 이전 커밋에는 회사 지식 코드도 남아 있지만 현재 작업 트리는 민원 기능 범위입니다. 원격 저장소는 [Hangi-n42/Civil-Complaints-Workbench](https://github.com/Hangi-n42/Civil-Complaints-Workbench)이며 비공개로 보존합니다. 기존 자동화가 실행되지 않도록 GitHub Actions는 비활성화했습니다. 과거 설계/평가 문서는 당시 기록이며 현재 검증 결과와 구분합니다.

## 로컬 복원

```bash
git clone https://github.com/Hangi-n42/Civil-Complaints-Workbench.git
```

Git에 기록된 코드·자료·이력·태그를 복원합니다. 실행 DB·개인 설정·설치 패키지·빌드 캐시는 포함하지 않으며, 실행 환경은 위 안내대로 준비합니다.
