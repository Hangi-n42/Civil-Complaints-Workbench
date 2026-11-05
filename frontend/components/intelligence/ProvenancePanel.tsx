import type { IntelRunProvenance } from "@/lib/api";

const SOURCE_LABELS: Record<string, string> = {
  real_text_excerpt_replayed_time: "실제 자료 발췌 · 재생 입력",
  real_text_replayed_time: "실제 자료 · 재생 입력",
  real_text_excerpt_replayed_time_with_synthetic_fill: "실제 자료 발췌와 합성 자료 혼합",
  synthetic_fill: "합성 시연 자료",
  synthetic_scenario: "합성 시연 자료",
};
const GENERATION_LABELS: Record<string, string> = {
  simulated: "모의 생성(fake) · 실제 모델 추론 아님",
  local_model: "로컬 모델 호출 결과",
  template: "규칙/템플릿 대체 생성",
  not_run: "생성 호출 없음",
  unknown: "생성 방식 미확인",
};
const MODE_LABELS: Record<string, string> = {
  replay: "재생 실행", manual: "수동 실행", realtime: "실행 모드: realtime (자동 수집 여부 미확인)",
};
const STATUS_LABELS: Record<string, string> = { completed: "분석 완료", failed: "실행 실패", running: "실행 중" };

export function ProvenancePanel({ operation, runs }: {
  operation: "stored_read" | "new_analysis";
  runs: IntelRunProvenance[];
}) {
  return (
    <section aria-label="자료 출처와 실행 구분" className="rounded-lg border border-amber-200 bg-amber-50/70 px-4 py-3 text-xs text-slate-700">
      <h2 className="font-extrabold text-slate-950">{operation === "new_analysis" ? "새 분석 실행 결과" : "저장 결과 조회 · 새 분석은 실행하지 않았습니다"}</h2>
      <p className="mt-1">확정·추천은 로컬 검토 자료이며 실제 민원 병합·회신 발송·행정 처리 완료를 뜻하지 않습니다.</p>
      <p className="mt-1">아래는 최근 실행 기록 최대 5개입니다. 입력 준비자가 제공한 출처 정책이며 원문 사실의 자동 검증 결과가 아닙니다. 저장된 모든 카드의 개별 출처를 보증하지 않습니다.</p>
      {runs.length === 0 ? <p className="mt-2 font-semibold">표시할 출처 실행 기록 없음 · 원문 출처·시각·좌표·생성 방식 미확인</p> : (
        <ul className="mt-2 space-y-2">
          {runs.map((run) => (
            <li key={run.run_id} className="rounded border border-amber-100 bg-white/80 px-3 py-2">
              <div className="font-bold">{run.analysis_type === "duplicate_merge" ? "중복 후보 분석" : run.analysis_type === "issue_and_public_insight" ? "이슈·인사이트 분석" : "분석 종류 미확인"} · {MODE_LABELS[run.mode] || "실행 모드 미확인"} · {STATUS_LABELS[run.status] || "실행 상태 미확인"}</div>
              <div>기록 시각: {run.completed_at || run.started_at} · 분석 기준: {run.as_of}</div>
              <div className="mt-1">입력 명칭: {run.source_name || "미확인"} · 원문 출처: {run.source.source_datasets.join(", ") || "미확인"}</div>
              <div>출처 정책: {run.source.source_policies.map((policy) => SOURCE_LABELS[policy] || "미확인 정책").join(", ") || "미확인"}</div>
              <div>시각: {run.source.time_basis === "scenario_replay" ? "시나리오 재생 시각" : "원본 접수 시각 여부 미확인"} · 좌표: {run.source.location_basis === "scenario_assigned" ? "시나리오 배치 좌표" : "원본 좌표 여부 미확인"} · 구조화: {run.source.structured_fields_basis === "scenario_template" ? "시나리오 템플릿" : "생성 근거 미확인"}</div>
              <div className="font-semibold">생성: {run.generation_methods.map((method) => GENERATION_LABELS[method] || GENERATION_LABELS.unknown).join(" / ") || GENERATION_LABELS.unknown}{run.generation_methods.includes("local_model") && run.model ? ` · 모델 ${run.model}` : ""}</div>
              <div className="text-slate-500">기록된 provider: {run.provider || "미확인"} · 생성 시도 {run.llm_attempt_count ?? "미확인"}회 · 대체 생성 {run.fallback_count ?? "미확인"}회</div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2">지도에는 저장된 분석 중심 또는 시연용 행정구역 대표 위치가 표시됩니다. 실제 현장 좌표·실시간 감지를 검증한 표시는 아닙니다.</p>
    </section>
  );
}
