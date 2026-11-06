# 민원 워크벤치 화면

| 경로 | 하는 일 |
| --- | --- |
| `/` | 민원 케이스 선택 |
| `/workbench` | 요약과 유사 사례 확인, 답변 초안 편집 |
| `/intelligence` | 이슈·인사이트·중복 후보와 자료 출처 조회 |
| `/admin` | 관리자 통계 확인 |

[전체 로컬 실행 안내](../docs/30_manuals/local_dev_runbook.md)는 자료·모델 준비와 API·화면 시작을 한 profile로 관리합니다. [화면 시연 안내](WORKBENCH_E2E_DEMO.md)에는 각 단계에서 확인할 동작이 있습니다.

## 프런트 개발

화면만 개발할 때는 저장소 루트에서 의존성을 설치합니다.

```bash
npm --prefix frontend ci
```

`frontend/.env.local`의 `NEXT_PUBLIC_API_BASE_URL`을 실행 중인 로컬 API 주소에 맞춥니다. 독립 시연 profile을 사용했다면 `http://127.0.0.1:8092`입니다. 프런트만 따로 시작할 때는 `npm --prefix frontend run dev`를 사용합니다.

| 명령 (`frontend`에서) | 용도 |
| --- | --- |
| `npm run dev` | 로컬 개발 화면 시작 |
| `npm run build` · `npm start` | Next.js 빌드와 로컬 서버 시작 |
| `npm run lint` · `npm test` | ESLint와 Vitest 검사 |

API 호출은 [lib/api.ts](lib/api.ts), 페이지는 [app](app/)에 있습니다. 관제 DB 재구성 절차는 [관제 replay 관리](../docs/30_manuals/complaint_intelligence_demo_replay.md)에 있습니다. 코드 편집 전 [AGENTS.md](AGENTS.md)를 확인합니다.
