"""저장된 실행/시드 메타데이터만 사용하는 시연 출처 표시."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.complaint_intelligence.repository import AnalysisRunRecord


class SourceProvenance(BaseModel):
    """입력 준비자가 제공한 정책이며 원문 진위의 자동 검증 결과가 아니다."""

    source_policies: list[str] = Field(default_factory=list)
    source_datasets: list[str] = Field(default_factory=list)
    time_basis: Literal["scenario_replay", "unknown"] = "unknown"
    location_basis: Literal["scenario_assigned", "unknown"] = "unknown"
    structured_fields_basis: Literal["scenario_template", "unknown"] = "unknown"


class RunProvenance(BaseModel):
    run_id: str
    analysis_type: str | None = None
    mode: str
    status: str
    source_name: str | None = None
    started_at: str
    completed_at: str | None = None
    as_of: str
    source: SourceProvenance
    generation_methods: list[str] = Field(default_factory=list)
    provider: str | None = None
    provider_class: str | None = None
    model: str | None = None
    llm_attempt_count: int | None = None
    fallback_count: int | None = None


def seed_source_provenance(seed: dict[str, Any], *, built_scenarios: bool = False) -> dict[str, Any]:
    scenarios = seed.get("scenarios") or []
    policies = sorted({item["source_policy"] for item in scenarios if isinstance(item.get("source_policy"), str)})
    datasets = sorted({
        str(record["source_dataset"]).replace("\\", "/").rsplit("/", 1)[-1]
        for scenario in scenarios for record in scenario.get("source_records", [])
        if record.get("source_dataset")
    })
    replay_policies = {"real_text_excerpt_replayed_time", "real_text_replayed_time", "real_text_excerpt_replayed_time_with_synthetic_fill"}
    return SourceProvenance(
        source_policies=policies, source_datasets=datasets,
        time_basis="scenario_replay" if built_scenarios or (policies and set(policies) <= replay_policies) else "unknown",
        location_basis="scenario_assigned" if built_scenarios else "unknown",
        structured_fields_basis="scenario_template" if built_scenarios else "unknown",
    ).model_dump()


def run_provenance(run: AnalysisRunRecord) -> RunProvenance:
    metadata = run.metadata
    source_data = metadata.get("source_provenance")
    try:
        source = SourceProvenance.model_validate(source_data) if isinstance(source_data, dict) else SourceProvenance()
    except ValueError:
        source = SourceProvenance()
    metrics = metadata.get("llm_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}

    def count(key: str) -> int | None:
        value = metrics.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    def text(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    methods = []
    provider_class = text(metrics.get("llm_provider_class"))
    if (count("llm_success_count") or 0) > 0:
        methods.append({"FakePublicInsightLLMProvider": "simulated", "LocalLLMProvider": "local_model"}.get(provider_class, "unknown"))
    if (count("fallback_count") or 0) > 0:
        methods.append("template")
    if not methods:
        methods = ["not_run" if count("llm_attempt_count") == 0 else "unknown"]
    return RunProvenance(
        run_id=run.run_id, analysis_type=text(metadata.get("analysis_type")), mode=run.mode, status=run.status,
        source_name=run.source_name, started_at=run.started_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None, as_of=run.as_of.isoformat(),
        source=source, generation_methods=methods,
        provider=text(metrics.get("llm_provider")), provider_class=provider_class, model=text(metrics.get("llm_model")),
        llm_attempt_count=count("llm_attempt_count"), fallback_count=count("fallback_count"),
    )
