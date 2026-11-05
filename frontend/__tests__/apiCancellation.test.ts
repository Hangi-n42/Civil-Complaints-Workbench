import { afterEach, describe, expect, it, vi } from "vitest";
import { runQaApi, searchCasesApi, streamQaApi } from "../lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("검색·QA 취소 전달", () => {
  it.each([searchCasesApi, runQaApi])("일반 API fetch에 같은 취소 신호를 전달한다", async (call) => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ data: { answer: "초안", results: [] } }) }));
    vi.stubGlobal("fetch", fetchMock);
    await call({ complaintId: "A", query: "합성 입력", signal: controller.signal });
    expect(fetchMock.mock.calls[0]).toHaveLength(2);
    expect((fetchMock.mock.calls as unknown as [string, RequestInit][])[0][1].signal).toBe(controller.signal);
  });

  it("SSE 취소 뒤에는 stage 콜백이나 일반 QA 폴백을 실행하지 않는다", async () => {
    const controller = new AbortController();
    const onStage = vi.fn();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      body: new ReadableStream<Uint8Array>({
        start(stream) {
          controller.abort();
          stream.enqueue(new TextEncoder().encode('event: stage\ndata: {"stage":"generating"}\n\n'));
          stream.close();
        },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await streamQaApi({ complaintId: "A", query: "합성 입력", signal: controller.signal }, onStage);
    expect(response.error).not.toBeNull();
    expect(onStage).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("취소되지 않은 스트림 실패는 같은 signal로 일반 QA에 폴백한다", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, body: null })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ data: { answer: "폴백 초안" } }) });
    vi.stubGlobal("fetch", fetchMock);
    const response = await streamQaApi({ complaintId: "A", query: "합성 입력", signal: controller.signal });
    expect(response.error).toBeNull();
    expect(response.data.answer).toBe("폴백 초안");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].signal).toBe(controller.signal);
  });
});
