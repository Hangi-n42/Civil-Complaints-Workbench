// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import IntelligencePage from "../app/intelligence/page";
import { ProvenancePanel } from "../components/intelligence/ProvenancePanel";
import { fetchDuplicateGroupsApi, fetchIntelDashboardApi, runIntelAnalysisApi, type IntelDashboardData, type IntelRunProvenance } from "../lib/api";

vi.mock("../lib/api", async (original) => ({
  ...await original<typeof import("../lib/api")>(),
  fetchIntelDashboardApi: vi.fn(), fetchDuplicateGroupsApi: vi.fn(), runIntelAnalysisApi: vi.fn(),
}));
vi.mock("../components/intelligence/HotspotMap", () => ({ HotspotMap: () => null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const run: IntelRunProvenance = {
  run_id: "synthetic-run", analysis_type: "issue_and_public_insight", mode: "replay", status: "completed",
  source_name: "합성 검사 입력", started_at: "2026-06-20T09:00:00+09:00", completed_at: "2026-06-20T09:01:00+09:00", as_of: "2026-06-20T09:00:00+09:00",
  source: { source_policies: ["synthetic_scenario"], source_datasets: ["synthetic-fixture"], time_basis: "scenario_replay",
    location_basis: "scenario_assigned", structured_fields_basis: "scenario_template" },
  generation_methods: ["simulated"], provider: "fake", provider_class: "FakePublicInsightLLMProvider", model: "not-called-model",
  llm_attempt_count: 1, fallback_count: 0,
};
const dashboard: IntelDashboardData = {
  summary: { alert_count: 0, critical_alert_count: 0, public_insight_count: 0, high_priority_insight_count: 0, human_review_required_count: 0, linked_alert_count: 0 },
  tabs: [], issue_alerts: [], public_insights: [], empty_state: {}, operation: "stored_read", provenance_runs: [run],
};
let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  vi.clearAllMocks();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  vi.mocked(fetchIntelDashboardApi).mockResolvedValue({ data: dashboard, error: null });
  vi.mocked(fetchDuplicateGroupsApi).mockResolvedValue({ data: { count: 0, duplicate_groups: [] }, error: null });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

it("실제 인텔리전스 화면과 다시 조회에서 재생·모의 생성·저장 조회를 표시한다", async () => {
  await act(async () => root.render(<IntelligencePage />));
  expect(host.textContent).toContain("저장 결과 조회 · 새 분석은 실행하지 않았습니다");
  expect(host.textContent).toContain("합성 시연 자료");
  expect(host.textContent).toContain("시나리오 재생 시각");
  expect(host.textContent).toContain("시나리오 배치 좌표");
  expect(host.textContent).toContain("모의 생성(fake) · 실제 모델 추론 아님");
  expect(host.textContent).not.toContain("not-called-model");
  expect(host.textContent).toContain("실제 민원 병합·회신 발송·행정 처리 완료를 뜻하지 않습니다");
  const refresh = [...host.querySelectorAll("button")].find((button) => button.textContent === "저장 결과 다시 조회")!;
  await act(async () => refresh.click());
  expect(fetchIntelDashboardApi).toHaveBeenCalledTimes(2);
  expect(runIntelAnalysisApi).not.toHaveBeenCalled();
});

it("기록 없는 기존 응답은 미확인으로 표시하며 실제 출처를 추정하지 않는다", async () => {
  vi.mocked(fetchIntelDashboardApi).mockResolvedValue({ data: { ...dashboard, provenance_runs: undefined }, error: null });
  await act(async () => root.render(<IntelligencePage />));
  expect(host.textContent).toContain("원문 출처·시각·좌표·생성 방식 미확인");
  expect(host.textContent).not.toContain("모의 생성(fake)");
});

it("새 분석의 로컬 모델·대체 생성 기록을 fake로 바꾸지 않는다", async () => {
  await act(async () => root.render(<ProvenancePanel operation="new_analysis" runs={[{
    ...run, mode: "manual", generation_methods: ["local_model", "template"], model: "recorded-local-model", provider: "local",
  }]} />));
  expect(host.textContent).toContain("새 분석 실행 결과");
  expect(host.textContent).toContain("로컬 모델 호출 결과");
  expect(host.textContent).toContain("규칙/템플릿 대체 생성");
  expect(host.textContent).toContain("recorded-local-model");
  expect(host.textContent).not.toContain("모의 생성(fake)");
});
