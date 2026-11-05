// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkbenchPage from "../app/workbench/page";
import { clearDraftSnapshot, fetchUiCasesApi, loadLastDraft, saveDraftSnapshot, searchCasesApi, streamQaApi, type SearchResponseData } from "../lib/api";

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
  fetchUiCasesApi: vi.fn(), searchCasesApi: vi.fn(), streamQaApi: vi.fn(),
}));

function bundle(query: string, id: string): SearchResponseData {
  const docs = [{ docId: `${id} 근거`, title: `${id} 근거`, snippet: `${id} 근거`, score: 0.9 }];
  return {
    query, results: docs, searchResults: docs, retrievedDocs: docs,
    routingHint: { topicType: "general", complexityLevel: "medium", route_key: `${id} 라우팅` },
    routingTrace: {
      topicType: "general", complexityLevel: "medium", complexityScore: 0.5, requestSegments: [`${id} 요청`], routeReason: `${id} 라우팅`,
      complexityTrace: { intent_count: 1, constraint_count: 0, entity_diversity: 1, policy_reference_count: 0, cross_sentence_dependency: false },
    },
    strategyId: `${id} 전략`, routeKey: `${id} 라우팅`,
  };
}

let root: Root;
let host: HTMLDivElement;

async function mount(caseId = "A") {
  navigation.params = new URLSearchParams({ case_id: caseId });
  root = createRoot(host);
  await act(async () => root.render(<WorkbenchPage />));
}

async function refresh(caseId = "A") {
  await act(async () => root.unmount());
  await mount(caseId);
}

async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.trim() === label || item.firstElementChild?.textContent?.trim() === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
}

async function edit(selector: "textarea" | "input", value: string) {
  const element = host.querySelector(selector)!;
  await act(async () => {
    const prototype = selector === "textarea" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  navigation.router.replace.mockImplementation((url: string) => { navigation.params = new URL(url, "http://localhost").searchParams; });
  vi.mocked(fetchUiCasesApi).mockResolvedValue({ data: { cases: ["A", "B"].map((id) => ({
    case_id: id, title: `민원 ${id}`, summary: `민원 ${id}`, raw_text: `${id} 원문`,
    received_at: "2026-11-05", category: "일반", region: "테스트", priority: "보통",
    request_segments: [`${id} 요청`],
  })) }, error: null });
  vi.mocked(searchCasesApi).mockImplementation(async (params) => ({ data: bundle(params.query, params.complaintId), error: null }));
  vi.mocked(streamQaApi).mockImplementation(async (params) => ({ data: {
    complaintId: params.complaintId, answer: `${params.complaintId} 생성 초안`, citations: [{ doc_id: `${params.complaintId} 근거` }],
    structuredOutput: { requestSegments: [`${params.complaintId} 요청`] },
  }, error: null }));
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe("민원별 초안과 편집 보존", () => {
  it.each(["", "없는-민원"])("초기 URL 민원 ID %s가 비어 있거나 없으면 첫 민원에 작업을 저장한다", async (caseId) => {
    await mount(caseId);
    await click("초안");
    expect(vi.mocked(streamQaApi).mock.calls[0][0].complaintId).toBe("A");
    expect(loadLastDraft("A")?.editorValue).toBe("A 생성 초안");
    expect(navigation.router.replace).toHaveBeenCalledWith("/workbench?case_id=A");
  });

  it("처음 복원하기 전에 기존 생성 초안을 지우지 않는다", async () => {
    window.localStorage.setItem("workbench-last-draft", JSON.stringify({ draft: { complaintId: "A", answer: "기존 A 초안" } }));
    await mount();
    expect(host.querySelector("textarea")?.value).toBe("기존 A 초안");
    await refresh();
    expect(host.querySelector("textarea")?.value).toBe("기존 A 초안");
  });

  it("A와 B의 생성·편집·검색·근거·routing·query hash를 별도로 복원한다", async () => {
    await mount();
    await edit("input", "A 검색어");
    await click("초안");
    await edit("textarea", "A 담당자 수정본");
    const a = loadLastDraft("A")!;
    expect(a).toMatchObject({ editorValue: "A 담당자 수정본", draft: { answer: "A 생성 초안" }, searchQuery: "A 검색어", searchBundle: { routingHint: { route_key: "A 라우팅" } } });
    expect(a.draftQueryHash).toBeTruthy();
    await click("민원 B");
    expect(host.querySelector("textarea")?.value).toBe("");
    expect(host.textContent).not.toContain("A 근거");
    await edit("input", "B 검색어");
    await click("초안");
    await edit("textarea", "B 담당자 수정본");
    await click("민원 A");
    expect(host.querySelector("textarea")?.value).toBe("A 담당자 수정본");
    expect(host.querySelector("input")?.value).toBe("A 검색어");
    expect(host.textContent).toContain("A 근거");
    expect(host.textContent).not.toContain("B 근거");
    await refresh();
    expect(host.querySelector("textarea")?.value).toBe("A 담당자 수정본");
    expect(loadLastDraft("A")).toEqual(a);
    await refresh("B");
    expect(host.querySelector("textarea")?.value).toBe("B 담당자 수정본");
    expect(loadLastDraft("B")?.searchBundle?.routingHint.route_key).toBe("B 라우팅");
  });

  it.each(["검토중", "처리완료"])("%s로 다음 민원에 이동한 뒤에도 수정본을 보존한다", async (status) => {
    await mount();
    await click("초안");
    await edit("textarea", "상태 변경 전 수정본");
    await click(status);
    expect(host.querySelector("textarea")?.value).toBe("");
    await click("민원 A");
    expect(host.querySelector("textarea")?.value).toBe("상태 변경 전 수정본");
    expect(host.textContent).toContain(`로컬 작업 상태: ${status}`);
    expect(host.textContent).toContain("공식 회신이나 행정 처리 완료로 전송되지 않습니다");
    await refresh();
    expect(host.querySelector("textarea")?.value).toBe("상태 변경 전 수정본");
  });

  it("AI 생성 전 직접 작성한 답변도 민원 전환과 새로고침 후 유지한다", async () => {
    await mount();
    await edit("textarea", "직접 작성 답변");
    await click("민원 B");
    await click("민원 A");
    await refresh();
    expect(host.querySelector("textarea")?.value).toBe("직접 작성 답변");
    expect(loadLastDraft("A")?.draft).toBeNull();
  });

  it("다른 검색 근거로 바뀌었다는 stale 경고를 복원 후에도 유지한다", async () => {
    await mount();
    await click("초안");
    await edit("input", "다른 검색어");
    await click("유사민원검색");
    expect(host.textContent).toContain("검색 결과가 갱신됨");
    await refresh();
    expect(host.textContent).toContain("검색 결과가 갱신됨");
    expect(host.querySelector("textarea")?.value).toBe("A 생성 초안");
  });

  it("불일치·손상 snapshot과 공통 routing을 다른 민원에 복원하지 않는다", async () => {
    window.localStorage.setItem("workbench-last-draft:A", JSON.stringify({ complaintId: "B", editorValue: "다른 민원 답변" }));
    window.localStorage.setItem("workbench-last-draft", JSON.stringify({ draft: { complaintId: "B", answer: "B 이전 초안" } }));
    window.localStorage.setItem("workbench-routing-info", JSON.stringify({ routingTrace: bundle("B 검색", "B").routingTrace }));
    await mount();
    expect(host.querySelector("textarea")?.value).toBe("");
    await click("초안");
    expect(vi.mocked(streamQaApi).mock.calls[0][0].routingHint?.route_key).toBe("A 라우팅");
    expect(loadLastDraft("B")?.editorValue).toBe("B 이전 초안");
  });

  it("명시적인 A 초기화는 B snapshot을 삭제하지 않는다", async () => {
    saveDraftSnapshot({ complaintId: "A", answer: "A 초안" });
    saveDraftSnapshot({ complaintId: "B", answer: "B 초안" });
    clearDraftSnapshot("A");
    expect(loadLastDraft("A")).toBeNull();
    expect(loadLastDraft("B")?.editorValue).toBe("B 초안");
  });

  it("갱신은 로컬 작업 상태만 초기화하고 민원별 편집본은 남긴다", async () => {
    await mount();
    await click("초안");
    await edit("textarea", "갱신 전 수정본");
    await click("검토중");
    const refreshButton = host.querySelector<HTMLButtonElement>('button[aria-label="갱신"]')!;
    await act(async () => refreshButton.click());
    await click("민원 A");
    expect(host.textContent).toContain("로컬 작업 상태: 미처리");
    expect(host.querySelector("textarea")?.value).toBe("갱신 전 수정본");
  });

  it("브라우저 저장 실패를 표시하고 화면의 편집값은 유지한다", async () => {
    await mount();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("저장 공간 부족"); });
    await edit("textarea", "저장 실패한 수정본");
    expect(host.querySelector("textarea")?.value).toBe("저장 실패한 수정본");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("저장하지 못했습니다");
  });

  it("지연 검색 도중의 최신 편집을 검색 완료 시 이전 값으로 덮어쓰지 않는다", async () => {
    await mount();
    await click("초안");
    let resolve!: (result: Awaited<ReturnType<typeof searchCasesApi>>) => void;
    vi.mocked(searchCasesApi).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await edit("input", "지연된 재검색");
    await click("유사민원검색");
    await edit("textarea", "검색 도중 추가한 최신 수정본");
    await act(async () => resolve({ data: bundle("지연된 재검색", "A"), error: null }));
    expect(loadLastDraft("A")?.editorValue).toBe("검색 도중 추가한 최신 수정본");
    await refresh();
    expect(host.querySelector("textarea")?.value).toBe("검색 도중 추가한 최신 수정본");
  });

  it.each(["current", "legacy"])("중첩 형식이 잘못된 %s 초안에서도 답변과 편집값을 복원한다", async (version) => {
    const draft = { complaintId: "A", answer: "유효한 생성 답변", structuredOutput: {
      summary: "유효한 요약", requestSegments: "잘못된 문자열 형식", actionItems: ["유효한 조치"],
    } };
    const key = version === "legacy" ? "workbench-last-draft" : "workbench-last-draft:A";
    const raw = JSON.stringify(version === "legacy" ? { draft } : {
      complaintId: "A", draft, editorValue: "유효한 편집값", searchQuery: "", searchBundle: null, draftQueryHash: null,
    });
    window.localStorage.setItem(key, raw);

    await mount();

    expect(host.querySelector("textarea")?.value).toBe(version === "legacy" ? draft.answer : "유효한 편집값");
    expect(loadLastDraft("A")?.draft).toMatchObject({ answer: draft.answer, structuredOutput: {
      summary: "유효한 요약", requestSegments: [], actionItems: ["유효한 조치"],
    } });
    expect(window.localStorage.getItem(key)).toBe(raw);
  });

  it("잘못된 검색 근거 항목만 제외하고 편집값과 정상 근거는 복원한다", async () => {
    const valid = bundle("검색", "A");
    const docs = [null, { ...valid.results[0], caseId: {} }, valid.results[0]];
    window.localStorage.setItem("workbench-last-draft:A", JSON.stringify({
      complaintId: "A", draft: { complaintId: "A", answer: "유효한 생성 답변" },
      editorValue: "유효한 편집값", searchQuery: "검색", draftQueryHash: null,
      searchBundle: { ...valid, results: docs, retrievedDocs: docs, searchResults: docs },
    }));

    await mount();

    expect(host.querySelector("textarea")?.value).toBe("유효한 편집값");
    expect(host.textContent).toContain("A 근거");
    expect(loadLastDraft("A")?.searchBundle).toMatchObject({
      results: valid.results, retrievedDocs: valid.retrievedDocs, searchResults: valid.searchResults,
    });
  });

  it("요약·조치·요청별 답변의 잘못된 형식은 제외하고 정상 보조 데이터는 유지한다", async () => {
    const validCard = { index: 0, requestSegment: "유효 요청", answer: "유효 개별 답변", caseIds: ["A 근거"], hasEvidence: true };
    window.localStorage.setItem("workbench-last-draft", JSON.stringify({ draft: {
      complaintId: "A", answer: "유효한 생성 답변", structuredOutput: {
        summary: { invalid: true }, actionItems: [null, "유효 조치", {}], requestSegments: ["유효 요청", {}],
        segmentAnswers: [null, { ...validCard, caseIds: null }, { ...validCard, answer: {} }, validCard],
      },
    } }));

    await mount();

    expect(host.querySelector("textarea")?.value).toBe("유효한 생성 답변");
    expect(host.textContent).toContain("유효 개별 답변");
    expect(loadLastDraft("A")?.draft?.structuredOutput).toEqual({
      summary: undefined, actionItems: ["유효 조치"], requestSegments: ["유효 요청"], segmentAnswers: [validCard],
    });
  });

  it("보조 검색 정보와 편집값이 잘못되어도 유효한 생성 answer는 남긴다", async () => {
    window.localStorage.setItem("workbench-last-draft:A", JSON.stringify({
      complaintId: "A", draft: { complaintId: "A", answer: "유효한 생성 답변" },
      editorValue: {}, searchQuery: "검색", searchBundle: { query: "검색" }, draftQueryHash: null,
    }));

    await mount();

    expect(host.querySelector("textarea")?.value).toBe("유효한 생성 답변");
    expect(loadLastDraft("A")?.searchBundle).toBeNull();
  });
});
