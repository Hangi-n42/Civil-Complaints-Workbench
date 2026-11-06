# 독립 민원 워크벤치 로컬 실행

이 절차는 사용 권한을 확인한 검색 인덱스·관제 DB·시연 케이스를 새 경로에 복사해 해당 컴퓨터에서 시연하는 방법입니다. 화면과 동선을 살펴보는 데는 자산과 모델 설치가 필요 없습니다. [시연 안내](../../frontend/WORKBENCH_E2E_DEMO.md)

준비 순서는 **설치와 설정 → 자산 복사 → 읽기 점검 → API·화면 시작 → 조회 확인**입니다. 준비 점검은 파일과 SQLite 메타데이터를 읽습니다. 검색과 답변 생성은 실행한 서비스에서 별도로 확인합니다.

## 1. 설치와 전용 profile

저장소를 내려받은 뒤 루트에서 Python 3.11과 Node.js 20.9 이상을 사용합니다. 법령 자료와 긴급도 모델 파일은 Git LFS로 관리합니다. 해당 기능을 실행할 때는 Git LFS를 설치해 현재 checkout의 필요한 파일을 받습니다. 검색 인덱스·관제 DB는 아래 2단계에서 별도로 준비합니다.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci
cp -n configs/demo/local.example.json configs/demo/local.json
```

위 명령은 bash 기준이다. Windows에서는 가상환경 Python을 `.\.venv\Scripts\python.exe`로 바꾸고 profile은 파일이 없을 때만 복사한다. 기존 개인 profile은 덮어쓰지 않는다. profile은 JSON이며 `env` 값이 해당 실행 프로세스에 명시적으로 전달된다. 원본 프로젝트의 `.env`를 복사하지 않는다. API는 `127.0.0.1:8092`, Next.js는 `127.0.0.1:3092`, API 주소는 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8092`로 맞춘다. 셸의 다른 DB 설정보다 profile 값이 우선한다. 상대 경로는 이 clone의 루트 기준이다.

다음 경로는 개인 환경에 맞춰 확인한다.

- `assets_dir` 및 그 아래를 가리키는 `env`의 Chroma/Intelligence/UI 경로.
- `ollama_models_dir`: 사전에 설치된 Ollama 모델 저장 디렉터리.
- `env.EMBEDDING_MODEL`: 사전에 준비된 BGE-M3 로컬 snapshot 디렉터리. 원격 모델 ID를 넣지 않는다.
- `env.DEFAULT_CHROMA_COLLECTION`: 보존 DB의 실제 논리 collection 이름.

현재 제품의 rubric/use_llm_judge/feedback은 켠다. PublicInsight도 enabled=true와 기존 fake provider를 유지한다. 생성·구조화·grounding·rubric·revision은 Gemma, feedback은 Qwen이며 생성 1536 토큰/8192 context/180초 기준을 명시한다. 자동 스케줄러와 DB 자동 생성만 끄고, segment mode는 기존 off와 HF offline을 유지한다. 점검/복사/기동 GET은 이 설정이 켜져 있어도 모델을 호출하지 않는다. 실제 생성 endpoint 실행은 별도 단계다. 모델이나 패키지가 없으면 점검은 실패하며 자동으로 설치하지 않는다.

## 2. 새 대상에만 자료 준비

실행 DB는 Git clone에 포함되지 않는다. 기존 검색 표현·벡터·담당자 판단을 보존하려면 다른 writer가 사용하지 않는 정지 상태의 bundle을 제공한다. 원본 DB를 이 절차에서 초기화하거나 새 모델로 재구축하지 않는다.

필수 bundle 구조:

```text
source-bundle/
  asset_manifest.json
  chroma_db/                 # chroma.sqlite3, vector segment 파일, collection_aliases도 함께
  intelligence.db           # 기존 민원 관제 SQLite
  replay_events.json        # 기존 replay events/scenarios
  ui_cases.json             # 케이스 객체 배열
```

`raw/`, `processed/`는 선택 항목이다. 현재 검색 본문은 인덱스 안의 `chroma:document` metadata에서 확인한다. 외부 원문/가공 파일 누락과 인덱스에 대한 대응 미검증은 정보로 남긴다. 임의의 작은 파일을 넣어 전체 원문 준비가 끝난 것처럼 처리하지 않는다. 전체 원문 재구축은 이 절차의 완료 범위가 아니다.

manifest 예시:

```json
{
  "data_kind": "historical_preserved",
  "source": "보존된 민원 인덱스/관제 DB의 원래 경로와 snapshot 기록",
  "raw_coverage": "external raw/processed absent; index correspondence unverified"
}
```

`historical_preserved`는 과거 보존 자산이라는 선언이며 원문의 진위·현재 출처·원래 임베딩 요청의 일치나 의미 품질을 증명하지 않는다. `real`도 출처 선언으로 취급한다. `synthetic`, `mixed`, `unknown`은 실제 시연 자산 준비 통과로 인정하지 않는다. 합성 검사는 별도 임시 경로와 `synthetic` 표기로 수행한다.

준비는 대상 경로를 명시하며, 이미 존재하는 대상은 거절한다.

```bash
.venv/bin/python scripts/standalone_demo.py prepare \
  --source /path/to/quiescent-source-bundle \
  --target "$PWD/data/local_demo/assets" \
  --collection civil_cases_v3
```

복사는 alias·원문·기존 벡터·관제 판단을 함께 보존한다. 회사 지식 DB, 다른 최상위 항목, symlink, 미완료 SQLite WAL/journal, 누락된 활성 collection은 거절한다. 복사 중 오류가 나면 새 부분 대상은 남기고 실패한다. 이를 실행 대상으로 쓰지 말고 원인을 확인한 뒤 다른 새 경로에 다시 준비한다. 원본과 기존 대상은 지우지 않는다. 기존 `replay:prepare`는 DB를 초기화하는 별도 도구이므로 이 실행 절차에서 사용하지 않는다.

## 3. 읽기 사전 점검

```bash
.venv/bin/python scripts/standalone_demo.py check --profile configs/demo/local.json
```

명령은 다음을 확인하고 실패 시 exit 1을 반환한다.

- API/UI loopback 주소 일치, 서로 다른 포트와 점유 여부. `bind`만 시도하며 기존 서버에 연결하거나 종료하지 않는다.
- 실제 DB 파일·선택한 logical→active alias·차원·저장 행·비어 있지 않은 인덱스 원문 metadata·vector segment 파일·관제 이벤트.
- UI 케이스와 replay 파일, LFS 포인터만 남은 필수 자산, 회사 지식 DB 혼입.
- Ollama library 모델의 단일 OCI manifest와 참조 blob 존재/크기, 로컬 임베딩 파일 및 `hidden_size`와 저장 vector 차원 일치.
- Python/Node 버전, 필요한 Python 패키지 위치와 설치된 Next.js 진입 파일.

Ollama manifest 경로는 [공식 저장 형식](https://github.com/ollama/ollama/blob/main/manifest/paths.go)과 [config/layers 구조](https://github.com/ollama/ollama/blob/main/manifest/manifest.go)를 기준으로 한다. 현재 checker는 명시적인 `library 모델:태그`와 단일 OCI manifest만 다룬다. 다른 registry·manifest list는 성공으로 추정하지 않고 실패한다.

출력의 `preflight_passed=true`는 위 파일·설정 점검만 통과했다는 뜻이다. `runtime_verified=false`, `source_verified=false`, 모델 로딩·추론 미검증 표시는 유지한다. 차원이 같다고 원래 임베딩 모델이 같다는 뜻은 아니다. blob hash·모델 추론·Ollama 서버의 정체/응답·검색 의미 정확성은 검사하지 않는다. 현재 원본 자산이 없다면 이 단계를 실제 준비 완료로 바꾸지 않는다.

## 4. 명시적 실행

Ollama는 사용자가 사전에 준비한 로컬 서비스로 사용한다. 이미 실행 중인 서비스를 중복 기동하지 않는다. 이 스크립트는 Ollama를 시작·종료하거나 모델을 받지 않는다.

API 터미널:

```bash
.venv/bin/python scripts/standalone_demo.py api --profile configs/demo/local.json
```

화면 터미널:

```bash
.venv/bin/python scripts/standalone_demo.py frontend --profile configs/demo/local.json
```

각 실행은 자신의 대상 포트가 비어 있는지 확인한 뒤 같은 profile을 사용한다. frontend 실행 시 이미 실행한 API의 포트 점유를 오류로 취급하지 않는다. 포트 점유가 예상한 API인지까지 확인한 결과는 아니므로 API/브라우저 결과를 별도로 확인한다. Next.js는 `frontend` 디렉터리에서 직접 실행하며 replay 초기화 hook을 호출하지 않는다. API는 기존 `scripts/run_api.py`를 명시적으로 시작한다. 둘 모두 현재 터미널의 foreground 프로세스이며 종료는 사용자가 해당 터미널에서 수행한다.

## 5. 실행 확인

API `http://127.0.0.1:8092`의 아래 GET 경로에서 응답 상태를 확인합니다.

| 경로 | 확인할 내용 |
| --- | --- |
| `/api/v1/health` | API 프로세스 응답 |
| `/api/v1/ui/cases` | API에 설정한 시연 케이스 조회 |
| `/complaint-intelligence/dashboard` | 관제 DB에 저장된 결과 조회 |

화면은 `http://127.0.0.1:3092/`에서 열고, 민원 선택과 `/workbench`, `/intelligence`, `/admin`을 확인합니다. 케이스 목록은 API 실패 시 예시를 표시할 수 있으므로 케이스 API 응답도 함께 확인합니다. 이 조회 확인의 범위는 기동과 저장 결과입니다. 로컬 모델을 호출하는 검색·초안 생성과 답변 품질은 별도로 확인합니다.

[현재 API](../10_contracts/api/current_api_contract.md) · [안전한 재색인과 복구](safe_index_rebuild.md) · [검증 범위](../README.md#검증-범위)
