# 현재 민원 모델 구성의 고정 근거 QA 평가

이 도구는 Gemma 생성·평가·재작성과 조건부 Qwen 피드백을 쓰는 **현재 제품 기준선**을 관찰한다. 모델 선택 실험이나 프롬프트 개선 작업이 아니다. 실제 `POST /api/v1/qa` 라우터와 `GenerationService`를 ASGI에서 호출하며 앱 시작·브라우저·검색·임베딩 평가는 포함하지 않는다.

## 기존 근거와 남은 측정

기존 [두 모델 선정 기록](llm_two_model_selection.md)은 노출된 개발 사례 비교다. 단일 실제 흐름의 13호출·493.710초와 현재 조치 약속·미지원 부서 차이 단정·평가 오탐을 보존한다. 이후 좁은 평가 문구 확인을 최종 생성 전체 흐름의 개선이나 사람 검토 부담 감소로 해석하지 않는다. `scripts/evaluate_qa.py`는 TODO인 별도 스크립트이며 이 도구의 실행기로 쓰지 않는다.

`data/evaluation/current_stack_quality_v1.json`에는 과거 개발 자료에서 고정한 두 입력이 있다. 복합 네 요청과 일정 미확정 사례의 **질문·검색 근거만** 모델 입력으로 보낸다. 과거 생성 답변·정답·채점 의견·사람 검토 체크리스트는 요청에 넣지 않는다. 새로운 holdout이나 전체 품질의 대표 표본이 아니다.

두 번째는 과거 `promise_bad` 테스트용 업무 안내 fixture다. 두 요청의 ID·routing trace와 두 번째의 필수 score 값은 평가용 고정 입력이며 새 검색/라우팅 측정값이 아니다. 첫 번째의 근거 score만 과거 저장 검색 결과에서 가져온다.

기존 중복 민원 real-holdout recall `0.9333 < 0.99` 실패를 별도로 유지한다. 현재 평가로 고친 결과가 아니다. 원천 자료 9,132건 중 본문 빈 2건이라는 기존 검색 자료 발견도 고정 근거 QA로 해결·재측정한 것이 아니다.

## 준비와 승인 경계

`--inventory`는 로컬 모델 서버의 읽기 전용 목록과 물리 메모리를 기록한 JSON이다. 다음 Mac 예시는 모델을 로드하지 않고 `/api/tags`, `/api/ps`만 조회한다. 출력 파일은 기존 파일을 덮어쓰지 않는다. 다른 환경의 메모리 수집은 별도 확인해야 한다.

```sh
python - <<'PY'
import json, subprocess, urllib.request
from pathlib import Path
inventory = {}
for key in ("tags", "ps"):
    with urllib.request.urlopen("http://127.0.0.1:11434/api/" + key, timeout=5) as response:
        inventory[key] = json.load(response)["models"]
inventory["memory"] = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
path = Path("reports/current-stack-inventory.json")
path.parent.mkdir(exist_ok=True)
with path.open("x", encoding="utf-8") as output:
    json.dump(inventory, output, ensure_ascii=False, indent=2)
PY
```

```sh
PROMETHEUS_REVISION_MODEL=gemma4:31b-it-q4_K_M \
OLLAMA_BASE_URL=http://localhost:11434 \
python scripts/evaluate_current_stack_quality.py prepare \
  --inventory /path/to/model-inventory-final-readonly.json \
  --output /path/to/quality/manifest.json
```

준비는 네트워크를 차단하고 모델을 호출하지 않는다. 입력·설정·코드 hash, 모델 digest, 렌더한 생성 기본 프롬프트 길이와 예산을 고정한다. 법령 사전 준비 상태도 모델/DB 접근 없이 확인한다. 법령/임베딩 경계가 필요하면 중단 사유를 기록한다.

재작성 모델은 기본값 `""`의 Gemma fallback을 쓰지 않고 canonical local profile와 같은 Gemma 명시값으로 고정한다. 모델·프롬프트를 변경하는 설정이 아니다. 관련 설정과 원시 URL도 실행 시 일치해야 한다. 핵심 파일별 hash 외에 간접 호출을 포함한 제품 Python 전체 hash와 HTTP/API 패키지 버전을 묶는다.

라이브 실행은 별도 명시 승인 뒤 소유자가 만든 JSON이 필요하다. 도구는 승인 파일을 만들지 않는다. `approved=true`, 정확한 `manifest_sha256`, `max_model_calls=28`, 정확한 절대 출력 경로 `output_directory`, 미래의 timezone 포함 `expires_at`이 일치해야 한다. 승인 없는 실행은 생산 모듈이나 소켓에 접근하기 전에 거절한다. 코드·입력·설정이 바뀌면 manifest와 승인을 다시 고정한다.

모델 연결 전 승인 파일 옆에 `<승인파일>.claim.json`을 exclusive 생성·fsync하여 소비를 고정한다. manifest와 실행 경로를 기록하며 다른 output 이름으로 같은 승인을 재사용할 수 없다. 중단·0콜·오류도 claim을 유지한다. 재실행은 새 명시 승인이 필요하며 도구는 claim을 지우거나 호출 예산을 초기화하지 않는다. 악의적인 승인 파일 수정자 방어는 범위가 아니다.

```sh
PROMETHEUS_REVISION_MODEL=gemma4:31b-it-q4_K_M \
OLLAMA_BASE_URL=http://localhost:11434 \
python scripts/evaluate_current_stack_quality.py run \
  --mode live_inference --manifest /path/to/quality/manifest.json \
  --approval /path/to/explicit-approval.json --output /path/to/quality/new-run
```

이 문서의 명령은 실행 예시이며 실제 모델 실행 승인이나 실행 완료 증거가 아니다. 중단된 실행은 원장을 보존하고 종료 코드 1을 반환한다.

## 호출·문맥·메모리 계약

사례당 모델 요청 14회, 두 사례 합계 28회, 직렬 실행, 전체 45분, 요청당 제품 timeout 180초를 상한으로 둔다. 단계 1이 실패하면 단계 2를 시작하지 않는다. 기본 생성의 default→compact 파싱 재시도 최대 2회, 초기 루브릭 5회, 조건부 피드백 1회, 재작성 1회, 재평가 최대 5회다. Q2 캐시가 있으면 재평가는 4회라 합계 상한 13회다. Q2 파싱 실패로 캐시가 없으면 14회가 필요하며, 합성 API 회귀에서 두 사례의 총 28회를 확인했다. 예외적인 호환 재호출이 더 필요해도 15번째/29번째 요청 전에 중단한다.

Qwen 피드백이 제품 조건상 발동하지 않으면 강제하지 않는다. 실제 Qwen 호출이 없으면 해당 역할 품질은 미측정이다. 피드백 성공과 최종 답변 개선을 자동으로 동일시하지 않는다.

모델 문맥은 8,192이고 출력은 생성/피드백/재작성/근거 평가 1,536, 문체 평가 640, 단일 항목 평가 192다. API 근거 매퍼의 기존 기본값(2,048 문맥·512 출력 예약·256 system 예약)은 그대로 기록하며 모델 예산과 혼동하지 않는다. 관찰층은 별도의 system 예약 1,024와 `ceil(prompt_chars/2)` 추정으로 프롬프트를 거절할 수 있다. 절단해서 실행하지 않는다. **문자 추정은 실제 토큰화 증명이 아니다.** 실제 `prompt_eval_count`, `eval_count`, `done`, `done_reason`도 확인하며 usage 누락·문맥 초과·length 종료는 실패로 남긴다. 이 검사도 서버가 입력을 절단하지 않았다는 증명은 아니다. 45분 시간 상한은 최악의 28회×180초보다 짧아 모든 허용 호출의 완료를 보장하지 않는다.

36GiB 환경에서 두 큰 모델 동시 상주를 허용하지 않는다. 최초 상주가 비어 있어야 하며, 자기 실행이 이미 사용한 한 모델만 남아 있으면 제품처럼 다음 모델을 직렬 요청해 Ollama native eviction을 사용한다. 자연 만료 대기·keep_alive 변경·강제 unload·모델 다운로드를 하지 않는다. 호출 전후 `/api/ps`와 OS 메모리를 확인하고 다른 모델 점유·복수 상주·메모리 측정 실패면 중단한다. 상주 이름만으로 동시 외부 작업의 소유자를 식별할 수 없으므로 다른 작업은 실행하지 않는 범위다. 메모리 확인은 호출 전후 checkpoint이며 실행 도중의 OOM 방지를 보장하지 않는다.

### 메모리 판정 v2와 이전 중단 보존

2026-11-06의 첫 실제 실행은 Gemma 1회 후 `free+inactive+speculative=2.761GiB < 8GiB`로 중단됐다. HTTP 정상 종료·JSON 구문 유효·QA 완료 0/2를 그대로 보존한다. 당시 OS 압력은 기록하지 않아 실제 메모리 압박 여부는 확인 불가다. 그 기록의 승인·남은 호출을 재사용하지 않는다.

페이지 가용량 합계를 실제 메모리 압력으로 대신 판정했던 한계를 고치기 위해 v2 계약은 **모델 미상주인 요청 전 8GiB 조건을 유지**하고, macOS에서는 매 호출 전후 읽기 전용 `/usr/sbin/sysctl -n kern.memorystatus_vm_pressure_level`도 기록한다. 첫 호출뿐 아니라 자연 만료 등으로 `resident_before=[]`가 된 다음 호출에도 8GiB를 다시 적용한다. `1`(normal)일 때만 계속하며 `2`(warning)·`4`(critical)·알 수 없는 값·측정 실패는 중단한다. 자기 모델이 상주한 경우 페이지 가용량은 기록하되 8GiB만으로 중단하지 않는다. non-macOS는 기존 매 checkpoint MemAvailable 8GiB 조건을 유지하며 실제 실행은 별도 미검증이다.

[Apple의 메모리 설명](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)은 압력을 여유 메모리·swap rate·wired/cache를 함께 고려한 상태로 설명한다. [Apple XNU의 조회 구현](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c)은 내부 압력을 normal/warn/critical 알림 값으로 변환해 반환한다. 이 근거는 관찰 신호를 바꾼 이유이며 이전 실행이 안전했다는 증명이나 새 실행 승인 자체는 아니다.

모델·프롬프트·문맥/출력·14/28회 상한과 중단 후 재시도 금지는 유지한다. v2 코드·main·정책이 달라 새 manifest와 별도 명시 승인이 필요하다. 합성 검사는 낮은 페이지 가용량+정상 압력의 다음 호출 허용과 높은 페이지 가용량+경고/위험의 중단을 확인할 뿐 실제 모델 품질 효과로 세지 않는다.

모델 요청 외에 `/api/tags` 최대 1회와 `/api/ps` 최대 57회의 읽기 전용 loopback GET이 있다. 다른 호스트·포트·모델·API로의 통신은 거절한다. 고정 근거 경로에서 DB/임베딩이 필요해도 대체 결과를 주입하지 않고 중단한다.

## 원시 증거·재생·사람 검토

`calls.jsonl`은 실제 전송 URL·POST 본문 bytes·timeout·model digest와 fingerprint를 기록한다. `/api/generate`의 HTTP 상태·header와 **httpx가 읽은 응답 body bytes를 base64로 별도 저장**하므로 모델 텍스트의 공백 제거, malformed JSON, HTTP 오류 전후의 원문을 잃지 않는다. 응답 도착 전 실패·진행 중 취소는 raw 없음과 `server_completion_unknown`을 명시한다. 관찰층은 제품 payload나 파싱·재시도를 바꾸지 않는다. HTTP 원문과 native 해석 결과를 구분한다. 관찰된 native 호출 밖의 HTTP 모델 요청도 운송 전에 차단한다.

실행 초기 `run.json`을 저장하고, 각 실제 POST 직전에 `reservations.jsonl`에 요청과 call ID를 append·fsync한다. 강제 종료 뒤 최종 `calls.jsonl`에 대응 기록이 없으면 전송/서버 완료 불명으로 보고한다. 예약과 최종 기록을 중복 합산하지 않으며, 초기 receipt의 0콜을 확정된 무호출로 해석하지 않는다. 마지막 JSONL 기록이 쓰다 끊겼으면 해당 꼬리와 미해결 예약을 명시한다.

저장 재생은 `--mode stored_replay --replay-calls <calls.jsonl>`이며 정확한 입력·완성 요청·timeout·형식·설정·코드·모델 digest·manifest·실제 HTTP 원문 대응이 일치해야 한다. 과거 자료에 이 정보가 없으면 재사용하지 않는다. 재생 소요시간·합성 HTTP 검사·실제 모델 소요시간을 섞지 않는다.

```sh
python scripts/evaluate_current_stack_quality.py review-template \
  --run-dir /path/to/new-run --output /path/to/blank-review-packets
# 사람이 원문/근거/결과를 검토하고 자신의 측정 시간·판정·수정 내용을 채운 뒤:
python scripts/evaluate_current_stack_quality.py review \
  --run-dir /path/to/new-run --entry /path/to/completed-one-case.json
python scripts/evaluate_current_stack_quality.py report \
  --manifest /path/to/quality/manifest.json --run-dir /path/to/new-run \
  --output /path/to/quality/report.json
```

검토 양식의 사람 ID·판정·시간은 비어 있으며 도구가 채우지 않는다. 자동 점수·편집량·AI 검수는 사람 시간으로 승격하지 않는다. 시간은 검토자가 측정했다고 제출한 경과 시간이고, 능동 작업 시간의 독립 검증은 아니다. 수작업 기준선이 없으면 **부담 감소는 확인 불가**다. 사람 검토가 없으면 미측정이며 이슈 #16을 완료 처리하지 않는다.

양식에는 사례별 최초 생성·평가·Qwen 피드백·재작성의 요청 payload와 native 해석 결과도 담아 사람이 역할별 오류를 대조할 수 있게 한다. 역할 관찰 메모는 빈칸이다. 최종 답변의 수용 판정만으로 피드백 품질이나 평가기 정확도를 확인했다고 계산하지 않는다.

준비 도구·차단 회귀의 PR과 실제 모델 실행·사람 검토 결과를 별도 단계로 인계할 수 있다. 준비 PR이 병합되어도 실제 평가가 완료되거나 이슈 #16이 닫히는 것은 아니다.
