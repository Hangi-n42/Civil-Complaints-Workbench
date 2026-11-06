# 민원 워크벤치 기술 스택

- 문서 버전 v2.1
- 현행 선언과 실행 profile 정적 대조 2026-11-06 · `8e9de051`

구동과 자료 준비는 [독립 실행 안내](../30_manuals/local_dev_runbook.md)를 따릅니다. 아래 버전의 근거는 의존성 선언 파일입니다.

## 현재 의존성

| 영역 | 선언·개발 기준 | 기준 파일 |
|---|---|---|
| Python | 독립 실행 점검 기준 3.11 | `scripts/standalone_demo.py`, `requirements.txt` |
| API·검증 | FastAPI 0.115.12, Uvicorn 0.35.0, Pydantic 2.11.7 | `requirements.txt` |
| 검색 | ChromaDB 1.5.5, bm25s 0.3.9, kiwipiepy 0.23.1 | `requirements.txt` |
| 임베딩 | sentence-transformers 3.4.1, transformers 4.46.3, torch 2.5.1 | `requirements.txt` |
| 수치·데이터 | NumPy 1.26.4, pandas 2.2.3 | `requirements.txt` |
| LLM 접속 | Ollama Python 0.6.1, httpx 0.28.1; Ollama 서버는 외부 프로그램 | `requirements.txt`, 설정 |
| FE | Next.js 16.2.3, React/React DOM 19.2.4 | `frontend/package.json` |
| FE UI | Tailwind ^4, Leaflet ^1.9.4, react-leaflet ^5.0.0 | 동일; 정확한 해석 버전은 lock 파일 |
| 기존 UI 의존성 | Streamlit 1.64.0 | `app/ui`, `requirements.txt` |
| 확인 도구 | pytest 8.3.5, Vitest ^4.1.9 | Python/FE 선언 파일 |

독립 실행 점검은 Node.js 20.9 이상과 설치된 Next.js 진입 파일을 확인합니다.

## 현재 실행 경계

1. `app/ingestion`, `app/structuring` 및 `app/structuring/pii`: 원천 입력·PII 처리·구조화.
2. `app/retrieval`: Chroma dense와 BM25를 결합한 hybrid 검색. `RETRIEVAL_STRATEGY` 기본값은 `hybrid`다. API는 분석·라우팅 정보를 남기되 검색 `top_k`는 요청값, snippet 1100, chunk policy balanced로 고정하며, 필터가 있으면 서비스의 hybrid 분기를 사용하지 않는다.
3. `app/generation`: 근거 기반 생성·정규화·인용 검증·재작성 흐름.
4. `app/evaluation/civil_llm_rubric.py`: 운영 Q0~Q7 평가. 현재 그룹은 Q2 / Q3·Q4·Q5 / Q1·Q7 / Q6 / Q0의 **5회**이며 같은 요청의 Q2 재사용 시 재평가 모델 호출은 4회다. 실제 성공 호출 수는 오류·설정에 따라 달라질 수 있다.
5. `app/complaint_intelligence`는 급증 알림, 기관 인사이트, 중복 민원 후보와 검토 상태를 SQLite read-model로 관리합니다.
6. `frontend`는 케이스 진입, Workbench, Intelligence, 관리자 화면을 제공합니다. UI 케이스 API는 `app/ui/services/ui_case_adapter.py`를 사용합니다.

주요 라우터는 `app/api/main.py`에서 등록합니다. [현재 API 계약](../10_contracts/api/current_api_contract.md)에서 화면과 호출 경로를 확인합니다.

## 모델과 환경 설정

[독립 실행 profile](../../configs/demo/local.example.json)은 생성·구조화·grounding filter·rubric·재작성에 `gemma4:31b-it-q4_K_M`을 설정합니다. 낮은 rubric 점수에 따른 조건부 피드백에는 `qwen3.8:27b-q4_K_M`을 사용합니다. PublicAgencyInsight는 별도의 `fake` provider를 사용합니다. 임베딩은 사전에 준비한 BGE-M3 로컬 snapshot을 CPU에서 사용합니다.

독립 실행은 API `8092`와 UI `3092`에 같은 profile을 적용합니다. 직접 개발 기동에서는 API 포트 기본값이 `8000`이고 FE API 주소 기본값은 `http://127.0.0.1:8001`이므로 `NEXT_PUBLIC_API_BASE_URL`을 맞춥니다. 루트 `.env`는 `app/core/config.py`가 읽고 이미 지정된 환경변수가 우선합니다.

`npm run dev`는 Next.js를 시작합니다. replay DB 초기화는 별도 `replay:prepare` 명령의 동작입니다. 보존 자산을 사용한 독립 시연은 [실행 안내](../30_manuals/local_dev_runbook.md)의 복사 절차를 따릅니다. 이전 EXAONE·Qwen 선택은 [ADR](adr_architecture_decision_record.md)에 당시 조건과 함께 기록돼 있습니다.

## 저장소와 문서 기준

| 위치 | 용도 |
|---|---|
| `data/raw` 또는 기존 `data/raw_data` | 원천 입력 |
| `data/processed` | 가공 민원 입력 |
| `data/chroma_db` | 기본 벡터 저장소 |
| `data/complaint_intelligence` | 관제 seed와 SQLite 데이터 |
| `data/evaluation` | 평가 과제·qrels 등 |
| `reports` | 스크립트의 현재 출력 경로; 과거 실험 기록은 문서 지도에서 별도 확인 |

제품 범위는 [민원 PRD](complaint_system_prd.md), 구성과 진입점은 [아키텍처](complaint_system_architecture.md)에서 확인합니다.
