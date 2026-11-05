import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { HotspotMap } from "../components/intelligence/HotspotMap";
import {
  averageMapCenter,
  hotspotCircleRadius,
  hotspotMarkerSize,
  KOREA_MAP_CENTER,
  KOREA_MAP_ZOOM,
  resolveHotspotMapPoint,
} from "../components/intelligence/hotspotMapUtils";
import type { IntelIssueAlertCard } from "../lib/api";

vi.mock("next/dynamic", () => ({ default: () => () => null }));

function alert(overrides: Partial<IntelIssueAlertCard>): IntelIssueAlertCard {
  return {
    id: "issue-1",
    status: "ACTIVE",
    severity: "WARNING",
    severity_label: "주의",
    color: "amber",
    title: "공사 소음 급증",
    summary: "반복 신고가 접수되었습니다.",
    topic: "공사 소음",
    region: "새빛동",
    center: null,
    radius: null,
    recent_count: 6,
    baseline: 0,
    surge_ratio: 6,
    confidence: 0.8,
    keywords: [],
    representative_complaint_ids: [],
    linked_insight_ids: [],
    map_focus: null,
    first_seen: "2026-06-20T08:00:00Z",
    last_seen: "2026-06-20T09:00:00Z",
    ...overrides,
  };
}

describe("hotspot map helpers", () => {
  it("center가 있으면 저장된 분석 중심 좌표를 우선 사용한다", () => {
    expect(resolveHotspotMapPoint(alert({ center: { latitude: 37.55, longitude: 126.98 } }))).toMatchObject({
      source: "center",
      latitude: 37.55,
      longitude: 126.98,
    });
  });

  it("center가 없으면 region 기반 대표 좌표를 반환한다", () => {
    expect(resolveHotspotMapPoint(alert({ region: "새빛동", center: null }))).toMatchObject({
      source: "region",
      label: "새빛동",
    });
  });

  it("region과 center가 모두 불명확하면 null을 반환한다", () => {
    expect(resolveHotspotMapPoint(alert({ region: null, center: null }))).toBeNull();
  });

  it("recent_count가 커져도 marker size를 제한된 단계로 표현한다", () => {
    expect(hotspotMarkerSize(2)).toBe(28);
    expect(hotspotMarkerSize(13)).toBe(44);
  });

  it("반경이 없으면 최근 건수 기반의 보수적 표시 반경을 사용한다", () => {
    expect(hotspotCircleRadius(alert({ radius: null, recent_count: 6 }))).toBeGreaterThan(180);
    expect(hotspotCircleRadius(alert({ radius: 0.25 }))).toBe(250);
  });

  it("대한민국 전체를 볼 수 있는 기본 지도 중심과 줌을 사용한다", () => {
    expect(KOREA_MAP_CENTER).toEqual([36.45, 127.85]);
    expect(KOREA_MAP_ZOOM).toBe(7);
    expect(averageMapCenter([])).toEqual(KOREA_MAP_CENTER);
  });

  it.each([
    [{ center: { latitude: 37.55, longitude: 126.98 } }, "저장된 분석 중심 · 원본 현장 좌표 여부 미확인"],
    [{ center: null, region: "새빛동" }, "시연용 행정구역 대표 위치"],
    [{ center: null, region: null }, "위치 미확인"],
  ] as const)("지도 상세가 위치 근거에 맞는 안내를 표시한다: %s", (overrides, label) => {
    const selected = alert(overrides);
    const html = renderToStaticMarkup(createElement(HotspotMap, { alerts: [selected], focusedAlertId: selected.id }));
    expect(html).toContain(label);
    expect(html).toContain("실제 발생 범위를 확인한 값이 아닙니다");
    expect(html).not.toContain("민원 발생 중심과 반경을 확인합니다");
  });
});
