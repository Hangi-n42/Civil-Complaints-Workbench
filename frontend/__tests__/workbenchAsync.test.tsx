// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkbenchPage from "../app/workbench/page";
import { fetchUiCasesApi, loadLastDraft, searchCasesApi, streamQaApi, type SearchResponseData } from "../lib/api";

const navigation = vi.hoisted(() => ({
  params: new URLSearchParams("case_id=A"),
  router: { replace: vi.fn(), push: vi.fn() },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => navigation.router,
  useSearchParams: () => navigation.params,
}));
vi.mock("../lib/api", async (original) => ({
  ...await original<typeof import("../lib/api")>(),
  fetchUiCasesApi: vi.fn(),
  searchCasesApi: vi.fn(),
  streamQaApi: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function bundle(query = "민원 A", marker = "A 근거"): SearchResponseData {
  const docs = [{ docId: marker, title: marker, snippet: marker, score: 0.9 }];
  return {
    query, results: docs, searchResults: docs, retrievedDocs: docs,
    routingHint: { topicType: "general", complexityLevel: "medium", route_key: marker },
    routingTrace: {
      topicType: "general", complexityLevel: "medium", complexityScore: 0.5,
      requestSegments: [query], routeReason: marker,
      complexityTrace: { intent_count: 1, constraint_count: 0, entity_diversity: 1, policy_reference_count: 0, cross_sentence_dependency: false },
    },
    strategyId: marker, routeKey: marker,
  };
}

let root: Root;
let host: HTMLDivElement;
const searchMock = vi.mocked(searchCasesApi);
const qaMock = vi.mocked(streamQaApi);

async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === label || item.firstElementChild?.textContent?.trim() === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
}

async function setQuery(value: string) {
  const input = host.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(async () => {
  vi.clearAllMocks();
  window.localStorage.clear();
  navigation.params = new URLSearchParams("case_id=A");
  navigation.router.replace.mockImplementation((url: string) => { navigation.params = new URL(url, "http://localhost").searchParams; });
  vi.mocked(fetchUiCasesApi).mockResolvedValue({ data: { cases: ["A", "B"].map((id) => ({
    case_id: id, title: `민원 ${id}`, summary: `민원 ${id}`, raw_text: `${id} 원문`,
    received_at: "2026-11-05", category: "일반", region: "테스트", priority: "보통",
  })) }, error: null });
  searchMock.mockImplementation(async (params) => ({ data: bundle(params.query, `${params.complaintId} 근거`), error: null }));
  qaMock.mockImplementation(async (params) => ({ data: { complaintId: params.complaintId, answer: `${params.complaintId} 초안` }, error: null }));
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<WorkbenchPage />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe("워크벤치 민원별 비동기 결과 경계", () => {
  it.each(["success", "failure"])("A 검색의 늦은 %s가 B의 검색 결과를 바꾸지 않는다", async (outcome) => {
    const pending = deferred<Awaited<ReturnType<typeof searchCasesApi>>>();
    searchMock.mockReturnValueOnce(pending.promise);
    await click("유사민원검색");
    await click("민원 B");
    await click("유사민원검색");
    await act(async () => pending.resolve({ data: bundle(), error: outcome === "failure" ? { message: "A 검색 실패" } : null }));
    expect(host.textContent).toContain("B 근거");
    expect(host.textContent).not.toContain("A 근거");
    expect(host.textContent).not.toContain("A 검색 실패");
  });

  it.each(["success", "failure"])("같은 민원의 이전 검색 %s가 역순 완료되어도 최신 결과를 유지한다", async (outcome) => {
    const pending = deferred<Awaited<ReturnType<typeof searchCasesApi>>>();
    searchMock.mockReturnValueOnce(pending.promise);
    await setQuery("첫 검색");
    await click("유사민원검색");
    await setQuery("최신 검색");
    await click("유사민원검색");
    await act(async () => pending.resolve({ data: bundle("첫 검색", "이전 근거"), error: outcome === "failure" ? { message: "이전 검색 실패" } : null }));
    expect(host.textContent).toContain("A 근거");
    expect(host.textContent).not.toContain("이전 근거");
    expect(host.textContent).not.toContain("이전 검색 실패");
    expect(searchMock.mock.calls[0][0].signal?.aborted).toBe(true);
    expect(searchMock.mock.calls[1][0].signal?.aborted).toBe(false);
  });

  it.each(["success", "failure", "throw"])("A QA의 늦은 %s와 단계가 B 초안·저장을 바꾸지 않는다", async (outcome) => {
    const pending = deferred<Awaited<ReturnType<typeof streamQaApi>>>();
    qaMock.mockReturnValueOnce(pending.promise);
    await click("초안");
    const [aParams, aStage] = qaMock.mock.calls[0];
    await click("민원 B");
    const bPending = deferred<Awaited<ReturnType<typeof streamQaApi>>>();
    qaMock.mockReturnValueOnce(bPending.promise);
    await click("초안");
    await act(async () => aStage?.("generating", "초안 작성 중"));
    expect(host.textContent).not.toContain("초안 작성 중");
    expect(aParams.signal?.aborted).toBe(true);
    expect(qaMock.mock.calls[1][0].signal?.aborted).toBe(false);
    await act(async () => bPending.resolve({ data: { complaintId: "B", answer: "B 확정 초안" }, error: null }));
    const saved = loadLastDraft("B");
    await act(async () => {
      if (outcome === "throw") pending.reject(new Error("A QA 예외"));
      else pending.resolve({ data: { complaintId: "A", answer: "A 늦은 초안" }, error: outcome === "failure" ? { message: "A QA 실패" } : null });
    });
    expect(host.querySelector("textarea")?.value).toBe("B 확정 초안");
    expect(host.textContent).not.toContain("A QA");
    expect(loadLastDraft("B")).toEqual(saved);
    expect(loadLastDraft("A")?.draft).toBeNull();
  });

  it("A 자동 선행검색 중 B로 전환하면 A QA를 시작하지 않는다", async () => {
    const pending = deferred<Awaited<ReturnType<typeof searchCasesApi>>>();
    searchMock.mockReturnValueOnce(pending.promise);
    await click("초안");
    await click("민원 B");
    await act(async () => pending.resolve({ data: bundle(), error: null }));
    expect(qaMock).not.toHaveBeenCalled();
    expect(loadLastDraft("A")?.draft).toBeNull();
    expect(host.textContent).not.toContain("생성할 수 없습니다");
  });

  it("검색어 변경 뒤 생성하면 새 검색의 입력·라우팅·근거를 사용한다", async () => {
    await click("유사민원검색");
    await setQuery("변경한 검색어");
    await click("초안");
    expect(searchMock).toHaveBeenCalledTimes(2);
    expect(qaMock.mock.calls[0][0]).toMatchObject({ query: "변경한 검색어", routingHint: { route_key: "A 근거" }, searchResults: [{ docId: "A 근거" }] });
    expect(host.querySelector("textarea")?.value).toBe("A 초안");
  });

  it("같은 민원의 재검색이 진행 중 QA를 취소하고 늦은 초안 저장을 막는다", async () => {
    const pending = deferred<Awaited<ReturnType<typeof streamQaApi>>>();
    qaMock.mockReturnValueOnce(pending.promise);
    await click("초안");
    await setQuery("새 근거 검색");
    await click("유사민원검색");
    await act(async () => pending.resolve({ data: { complaintId: "A", answer: "이전 근거 초안" }, error: null }));
    expect(qaMock.mock.calls[0][0].signal?.aborted).toBe(true);
    expect(host.querySelector("textarea")?.value).toBe("");
    expect(loadLastDraft("A")?.draft).toBeNull();
    await click("초안");
    expect(qaMock.mock.calls[1][0].query).toBe("새 근거 검색");
    expect(host.querySelector("textarea")?.value).toBe("A 초안");
  });

  it("URL로 B 민원을 선택한 경우에도 A 요청 결과를 차단한다", async () => {
    const pending = deferred<Awaited<ReturnType<typeof searchCasesApi>>>();
    searchMock.mockReturnValueOnce(pending.promise);
    await click("유사민원검색");
    navigation.params = new URLSearchParams("case_id=B");
    await act(async () => root.render(<WorkbenchPage />));
    await act(async () => pending.resolve({ data: bundle(), error: null }));
    expect(host.textContent).toContain("B 원문");
    expect(host.textContent).not.toContain("A 근거");
    expect(searchMock.mock.calls[0][0].signal?.aborted).toBe(true);
  });

  it("현재 민원의 검색·생성 오류를 표시하고 저장 오류와 생성 성공을 구분한다", async () => {
    searchMock.mockResolvedValueOnce({ data: bundle(), error: { message: "현재 검색 실패" } });
    await click("유사민원검색");
    expect(host.textContent).toContain("현재 검색 실패");
    qaMock.mockResolvedValueOnce({ data: { answer: "" }, error: { message: "현재 생성 실패" } });
    await click("초안");
    expect(host.textContent).toContain("현재 생성 실패");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("저장 공간 부족"); });
    await click("초안");
    expect(host.querySelector("textarea")?.value).toBe("A 초안");
    expect(host.textContent).toContain("이 브라우저에 작업 내용을 저장하지 못했습니다");
  });

  it("컴포넌트 해제는 진행 중 요청을 취소한다", async () => {
    const pending = deferred<Awaited<ReturnType<typeof searchCasesApi>>>();
    searchMock.mockReturnValueOnce(pending.promise);
    await click("유사민원검색");
    await act(async () => root.unmount());
    expect(searchMock.mock.calls[0][0].signal?.aborted).toBe(true);
    await act(async () => pending.resolve({ data: bundle(), error: null }));
    root = createRoot(host);
  });
});
