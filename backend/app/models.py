from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Connection,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, attributes, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


PROMPT_OPTIMIZATION_AUDIT_DEFAULT = (
    '{"status":"not_recorded","attempt_count":0,'
    '"upstream_status_code":null,"request_correlation_id":null,'
    '"elapsed_ms":null,"error_type":null,"error_message":null,'
    '"output_budget":null,"reasoning_effort":null}'
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(80), default="管理员")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship()


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="豆包主模型")
    provider: Mapped[str] = mapped_column(String(40), default="doubao")
    base_url: Mapped[str] = mapped_column(
        String(300), default="https://ark.cn-beijing.volces.com/api/v3"
    )
    api_path: Mapped[str] = mapped_column(String(120), default="/chat/completions")
    model_id: Mapped[str] = mapped_column(String(200), default="doubao-seed-2-0-lite-260215")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.1)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    max_retries: Mapped[int] = mapped_column(Integer, default=1)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2)
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True)
    high_risk_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    input_micros_per_million_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    output_micros_per_million_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    max_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    benchmark_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("0")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OptimizerConfig(Base):
    __tablename__ = "optimizer_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="SOL 提示词诊断模型")
    provider: Mapped[str] = mapped_column(String(40), default="openai")
    base_url: Mapped[str] = mapped_column(String(300), default="https://api.openai.com/v1")
    api_path: Mapped[str] = mapped_column(String(120), default="/chat/completions")
    model_id: Mapped[str] = mapped_column(String(200), default="gpt-5.6-sol")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.1)
    max_tokens: Mapped[int] = mapped_column(Integer, default=12000)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_retries: Mapped[int] = mapped_column(Integer, default=1)
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True)
    input_micros_per_million_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    output_micros_per_million_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    max_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_optimization_run_id",
            name="uq_prompt_versions_source_optimization_run",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40), index=True)
    system_prompt: Mapped[str] = mapped_column(Text)
    user_prompt: Mapped[str] = mapped_column(Text)
    rubric_version: Mapped[str] = mapped_column(String(40), default="rubric-v2.1")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_optimization_run_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    source_automation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rollback_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    canary_status: Mapped[str] = mapped_column(
        String(20), default="not_started", index=True
    )
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PromptMetricSnapshot(Base):
    __tablename__ = "prompt_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "prompt_id",
            "task_set_hash",
            name="uq_prompt_metric_snapshot_task_set",
        ),
        CheckConstraint(
            "total_count >= 1 AND reviewed_count >= 0 "
            "AND reviewed_count <= total_count",
            name="ck_prompt_metric_snapshot_counts",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="CASCADE"), index=True
    )
    task_set_key: Mapped[str] = mapped_column(String(160), index=True)
    task_set_hash: Mapped[str] = mapped_column(String(64), index=True)
    evaluation_ids_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)
    total_count: Mapped[int] = mapped_column(Integer)
    reviewed_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    prompt: Mapped[PromptVersion] = relationship()


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(500))
    stored_name: Mapped[str] = mapped_column(String(200), unique=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MaterialPackage(Base):
    __tablename__ = "material_packages"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual_upload','production_import','legacy_backfill')",
            name="ck_material_packages_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(
        String(30), default="manual_upload", index=True
    )
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    items: Mapped[list["MaterialPackageItem"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="MaterialPackageItem.position",
    )


class MaterialPackageItem(Base):
    __tablename__ = "material_package_items"
    __table_args__ = (
        UniqueConstraint(
            "package_id", "position", name="uq_material_package_item_position"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("material_packages.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(500))
    duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    package: Mapped[MaterialPackage] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship()


class AgentPlanVersion(Base):
    __tablename__ = "agent_plan_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_agent_plan_versions_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    plan_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", index=True
    )
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class EvaluationJob(Base):
    __tablename__ = "evaluation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "root_job_id",
            "technical_attempt",
            name="uq_evaluation_jobs_root_attempt",
        ),
        UniqueConstraint(
            "loop_attempt_id",
            "technical_attempt",
            name="uq_evaluation_jobs_loop_attempt_technical",
        ),
        Index(
            "uq_evaluation_jobs_regression_strategy",
            "regression_item_id",
            "strategy_bundle_id",
            unique=True,
            sqlite_where=sql_text(
                "regression_item_id IS NOT NULL "
                "AND strategy_bundle_id IS NOT NULL "
                "AND technical_attempt = 0"
            ),
        ),
        Index(
            "uq_evaluation_jobs_baseline_regression_item",
            "baseline_regression_item_id",
            unique=True,
            sqlite_where=sql_text(
                "baseline_regression_item_id IS NOT NULL "
                "AND technical_attempt = 0"
            ),
        ),
        CheckConstraint(
            "queue_class IN ('validation','interactive','production_batch','canary','recovery')",
            name="ck_evaluation_jobs_queue_class",
        ),
        CheckConstraint(
            "origin_queue_class IN ('validation','interactive','production_batch','canary','recovery')",
            name="ck_evaluation_jobs_origin_queue_class",
        ),
        CheckConstraint(
            "technical_attempt BETWEEN 0 AND 2",
            name="ck_evaluation_jobs_technical_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    prompt_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    regression_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_regression_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    baseline_regression_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("baseline_regression_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    strategy_bundle_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    loop_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("loop_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    root_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    queue_class: Mapped[str] = mapped_column(
        String(30), default="production_batch", index=True
    )
    origin_queue_class: Mapped[str] = mapped_column(
        String(30), default="production_batch", index=True
    )
    technical_attempt: Mapped[int] = mapped_column(Integer, default=0)
    technical_error_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    retry_after_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    batch_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="waiting")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    asset: Mapped[Asset] = relationship()
    prompt_a: Mapped[PromptVersion | None] = relationship(foreign_keys=[prompt_a_id])
    prompt_b: Mapped[PromptVersion | None] = relationship(foreign_keys=[prompt_b_id])


class EvaluationControl(Base):
    __tablename__ = "evaluation_controls"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SamplingPolicy(Base):
    __tablename__ = "sampling_policies"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    sample_rate: Mapped[int] = mapped_column(Integer, default=10)
    low_confidence_threshold: Mapped[float] = mapped_column(Float, default=0.7)
    medium_confidence_threshold: Mapped[float] = mapped_column(Float, default=0.9)
    cold_start_required_count: Mapped[int] = mapped_column(Integer, default=5)
    high_level_required_from: Mapped[int] = mapped_column(Integer, default=4)
    updated_by: Mapped[str] = mapped_column(String(80), default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReviewWorkflowPolicy(Base):
    __tablename__ = "review_workflow_policies"
    __table_args__ = (
        CheckConstraint(
            "initial_reviewers >= 1 "
            "AND initial_reviewers <= 9 "
            "AND initial_reviewers % 2 = 1",
            name="ck_review_workflow_policies_odd_reviewers",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    initial_reviewers: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(80), default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CanaryRun(Base):
    __tablename__ = "canary_runs"
    __table_args__ = (
        CheckConstraint(
            "current_state IN ("
            "'draft','preflight_ready','approvals_ready','freeze_ready',"
            "'candidate_ready','human_review_ready','failed','cancelled'"
            ")",
            name="ck_canary_runs_current_state",
        ),
        CheckConstraint(
            "json_valid(plan_json) = 1 "
            "AND json_type(plan_json, '$') = 'object'",
            name="ck_canary_runs_plan_json",
        ),
        CheckConstraint(
            "json_valid(evidence_json) = 1 "
            "AND json_type(evidence_json, '$') = 'object'",
            name="ck_canary_runs_evidence_json",
        ),
        CheckConstraint(
            "json_valid(snapshot_json) = 1 "
            "AND json_type(snapshot_json, '$') = 'object'",
            name="ck_canary_runs_snapshot_json",
        ),
        CheckConstraint(
            "length(snapshot_fingerprint) = 64 "
            "AND lower(snapshot_fingerprint) "
            "NOT GLOB '*[^0-9a-f]*'",
            name="ck_canary_runs_snapshot_fingerprint",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        String(80), primary_key=True, unique=True
    )
    display_name: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    current_state: Mapped[str] = mapped_column(
        String(30), default="draft", index=True
    )
    plan_json: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    snapshot_json: Mapped[str] = mapped_column(Text)
    snapshot_fingerprint: Mapped[str] = mapped_column(
        String(64), index=True
    )
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )


class StrategyBundle(Base):
    __tablename__ = "strategy_bundles"
    __table_args__ = (
        CheckConstraint(
            "strategy_schema_version IN ("
            "'strategy-bundle-v1','strategy-bundle-v2',"
            "'strategy-bundle-v3'"
            ")",
            name="ck_strategy_bundles_schema_version",
        ),
        CheckConstraint(
            "("
            "strategy_schema_version = 'strategy-bundle-v1' "
            "AND dimension_route_policy_id IS NULL "
            "AND dimension_schema_set_snapshot IS NULL "
            "AND label_field_set_snapshot IS NULL "
            "AND resolved_schema_contract_version IS NULL "
            "AND dimension_route_policy_snapshot IS NULL "
            "AND evaluation_profile_set_snapshot IS NULL"
            ") OR ("
            "strategy_schema_version = 'strategy-bundle-v2' "
            "AND length(trim(dimension_route_policy_id)) > 0 "
            "AND json_valid(dimension_schema_set_snapshot) "
            "AND json_type(dimension_schema_set_snapshot, '$') = 'object' "
            "AND json_valid(label_field_set_snapshot) "
            "AND json_type(label_field_set_snapshot, '$') = 'object' "
            "AND length(trim(resolved_schema_contract_version)) > 0 "
            "AND dimension_route_policy_snapshot IS NULL "
            "AND evaluation_profile_set_snapshot IS NULL"
            ") OR ("
            "strategy_schema_version = 'strategy-bundle-v3' "
            "AND prompt_b_version IS NULL "
            "AND length(trim(dimension_route_policy_id)) > 0 "
            "AND json_valid(dimension_schema_set_snapshot) "
            "AND json_type(dimension_schema_set_snapshot, '$') = 'object' "
            "AND json_valid(label_field_set_snapshot) "
            "AND json_type(label_field_set_snapshot, '$') = 'object' "
            "AND length(trim(resolved_schema_contract_version)) > 0 "
            "AND json_valid(dimension_route_policy_snapshot) "
            "AND json_type(dimension_route_policy_snapshot, '$') = 'object' "
            "AND json_valid(evaluation_profile_set_snapshot) "
            "AND json_type(evaluation_profile_set_snapshot, '$') = 'object'"
            ")",
            name="ck_strategy_bundles_dimension_contract",
        ),
        UniqueConstraint(
            "canonical_hash",
            name="uq_strategy_canonical_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strategy_schema_version: Mapped[str] = mapped_column(
        String(40),
        default="strategy-bundle-v1",
        server_default="strategy-bundle-v1",
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    model_config_snapshot: Mapped[str] = mapped_column(Text)
    prompt_a_version: Mapped[str] = mapped_column(String(40), index=True)
    prompt_b_version: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    rubric_version: Mapped[str] = mapped_column(String(40), index=True)
    engine_version: Mapped[str] = mapped_column(String(40), index=True)
    sampling_policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_review_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_plan_version: Mapped[str] = mapped_column(
        String(80),
        default="controlled-agent-plan-v1",
        server_default="controlled-agent-plan-v1",
        index=True,
    )
    dimension_route_policy_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    dimension_schema_set_snapshot: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    label_field_set_snapshot: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    resolved_schema_contract_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    dimension_route_policy_snapshot: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    evaluation_profile_set_snapshot: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DimensionSchema(Base):
    __tablename__ = "dimension_schemas"
    __table_args__ = (
        CheckConstraint(
            "length(trim(schema_key)) > 0",
            name="ck_dimension_schemas_schema_key",
        ),
        CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_dimension_schemas_version",
        ),
        CheckConstraint(
            "schema_type IN ('core','family_pack','extension')",
            name="ck_dimension_schemas_schema_type",
        ),
        CheckConstraint(
            "family_key IN ('space','product','graphic','intent','common')",
            name="ck_dimension_schemas_family_key",
        ),
        CheckConstraint(
            "status IN ('draft','candidate','published','retired')",
            name="ck_dimension_schemas_status",
        ),
        CheckConstraint(
            "json_valid(definition_json) "
            "AND json_type(definition_json, '$') = 'object'",
            name="ck_dimension_schemas_definition_json",
        ),
        CheckConstraint(
            "length(canonical_hash) = 64 "
            "AND canonical_hash = lower(canonical_hash) "
            "AND canonical_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_dimension_schemas_canonical_hash",
        ),
        CheckConstraint(
            "parent_schema_id IS NULL OR parent_schema_id <> id",
            name="ck_dimension_schemas_parent_not_self",
        ),
        CheckConstraint(
            "core_schema_id IS NULL OR core_schema_id <> id",
            name="ck_dimension_schemas_core_not_self",
        ),
        CheckConstraint(
            "((status IN ('published','retired')) "
            "AND published_by IS NOT NULL AND published_at IS NOT NULL) "
            "OR ((status IN ('draft','candidate')) "
            "AND published_by IS NULL AND published_at IS NULL)",
            name="ck_dimension_schemas_publish_audit",
        ),
        CheckConstraint(
            "(status = 'retired' AND retired_at IS NOT NULL) "
            "OR (status <> 'retired' AND retired_at IS NULL)",
            name="ck_dimension_schemas_retired_at",
        ),
        UniqueConstraint(
            "schema_key",
            "version",
            name="uq_dimension_schemas_key_version",
        ),
        UniqueConstraint(
            "canonical_hash",
            name="uq_dimension_schemas_canonical_hash",
        ),
        Index(
            "ix_dimension_schemas_registry",
            "schema_type",
            "family_key",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_key: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    schema_type: Mapped[str] = mapped_column(String(20), index=True)
    family_key: Mapped[str] = mapped_column(String(20), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    parent_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("dimension_schemas.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    core_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("dimension_schemas.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    definition_json: Mapped[str] = mapped_column(Text)
    canonical_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_optimization_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_optimization_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
    published_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DimensionRoutePolicy(Base):
    __tablename__ = "dimension_route_policies"
    __table_args__ = (
        CheckConstraint(
            "length(trim(policy_key)) > 0",
            name="ck_dimension_route_policies_policy_key",
        ),
        CheckConstraint(
            "length(trim(version)) > 0",
            name="ck_dimension_route_policies_version",
        ),
        CheckConstraint(
            "status IN ('draft','candidate','published','retired')",
            name="ck_dimension_route_policies_status",
        ),
        CheckConstraint(
            "json_valid(definition_json) "
            "AND json_type(definition_json, '$') = 'object'",
            name="ck_dimension_route_policies_definition_json",
        ),
        CheckConstraint(
            "length(canonical_hash) = 64 "
            "AND canonical_hash = lower(canonical_hash) "
            "AND canonical_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_dimension_route_policies_canonical_hash",
        ),
        CheckConstraint(
            "((status IN ('published','retired')) "
            "AND published_by IS NOT NULL AND published_at IS NOT NULL) "
            "OR ((status IN ('draft','candidate')) "
            "AND published_by IS NULL AND published_at IS NULL)",
            name="ck_dimension_route_policies_publish_audit",
        ),
        CheckConstraint(
            "(status = 'retired' AND retired_at IS NOT NULL) "
            "OR (status <> 'retired' AND retired_at IS NULL)",
            name="ck_dimension_route_policies_retired_at",
        ),
        UniqueConstraint(
            "policy_key",
            "version",
            name="uq_dimension_route_policies_key_version",
        ),
        UniqueConstraint(
            "canonical_hash",
            name="uq_dimension_route_policies_canonical_hash",
        ),
        Index(
            "ix_dimension_route_policies_registry",
            "policy_key",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    definition_json: Mapped[str] = mapped_column(Text)
    canonical_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
    published_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class DimensionCalibrationRun(Base):
    __tablename__ = "dimension_calibration_runs"
    __table_args__ = (
        CheckConstraint(
            "length(trim(run_key)) > 0",
            name="ck_dimension_calibration_runs_key",
        ),
        CheckConstraint(
            "status IN ("
            "'queued','running','completed','partial_failed','failed'"
            ")",
            name="ck_dimension_calibration_runs_status",
        ),
        CheckConstraint(
            "total BETWEEN 1 AND 100",
            name="ck_dimension_calibration_runs_total",
        ),
        CheckConstraint(
            "processing >= 0 AND completed >= 0 "
            "AND core_fallback >= 0 AND blocked >= 0 "
            "AND unassessable >= 0 AND failed >= 0 "
            "AND processing + completed + core_fallback + blocked "
            "+ unassessable + failed <= total",
            name="ck_dimension_calibration_runs_counts",
        ),
        CheckConstraint(
            "length(strategy_bundle_hash) = 64 "
            "AND strategy_bundle_hash = lower(strategy_bundle_hash) "
            "AND strategy_bundle_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_dimension_calibration_runs_bundle_hash",
        ),
        CheckConstraint(
            "length(definition_hash) = 64 "
            "AND definition_hash = lower(definition_hash) "
            "AND definition_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_dimension_calibration_runs_definition_hash",
        ),
        CheckConstraint(
            "json_valid(strategy_snapshot_json) "
            "AND json_type(strategy_snapshot_json, '$') = 'object'",
            name="ck_dimension_calibration_runs_strategy_snapshot",
        ),
        CheckConstraint(
            "json_valid(asset_manifest_json) "
            "AND json_type(asset_manifest_json, '$') = 'object'",
            name="ck_dimension_calibration_runs_asset_manifest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(
        String(120), unique=True, index=True
    )
    strategy_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"),
        index=True,
    )
    strategy_bundle_hash: Mapped[str] = mapped_column(
        String(64), index=True
    )
    strategy_snapshot_json: Mapped[str] = mapped_column(Text)
    asset_manifest_json: Mapped[str] = mapped_column(Text)
    definition_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="queued", index=True
    )
    total: Mapped[int] = mapped_column(Integer)
    processing: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    core_fallback: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    unassessable: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(
        String(80), default="system"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    strategy_bundle: Mapped[StrategyBundle] = relationship()
    items: Mapped[list["DimensionCalibrationItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="DimensionCalibrationItem.id",
    )


class DimensionCalibrationItem(Base):
    __tablename__ = "dimension_calibration_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "asset_id",
            name="uq_dimension_calibration_run_asset",
        ),
        CheckConstraint(
            "status IN ("
            "'queued','processing','completed','core_fallback',"
            "'blocked','unassessable','failed'"
            ")",
            name="ck_dimension_calibration_items_status",
        ),
        CheckConstraint(
            "json_valid(asset_snapshot_json) "
            "AND json_type(asset_snapshot_json, '$') = 'object'",
            name="ck_dimension_calibration_items_asset_snapshot",
        ),
        CheckConstraint(
            "resolution_snapshot_json IS NULL OR "
            "(json_valid(resolution_snapshot_json) "
            "AND json_type(resolution_snapshot_json, '$') = 'object')",
            name="ck_dimension_calibration_items_resolution",
        ),
        CheckConstraint(
            "precheck_json IS NULL OR "
            "(json_valid(precheck_json) "
            "AND json_type(precheck_json, '$') = 'object')",
            name="ck_dimension_calibration_items_precheck",
        ),
        CheckConstraint(
            "aesthetic_json IS NULL OR "
            "(json_valid(aesthetic_json) "
            "AND json_type(aesthetic_json, '$') = 'object')",
            name="ck_dimension_calibration_items_aesthetic",
        ),
        CheckConstraint(
            "scoring_json IS NULL OR "
            "(json_valid(scoring_json) "
            "AND json_type(scoring_json, '$') = 'object')",
            name="ck_dimension_calibration_items_scoring",
        ),
        CheckConstraint(
            "level IS NULL OR level IN ('L1','L2','L3','L4','L5')",
            name="ck_dimension_calibration_items_level",
        ),
        CheckConstraint(
            "(status = 'queued' AND worker_id IS NULL "
            "AND started_at IS NULL AND finished_at IS NULL) "
            "OR (status = 'processing' AND length(trim(worker_id)) > 0 "
            "AND started_at IS NOT NULL AND finished_at IS NULL) "
            "OR (status IN ("
            "'completed','core_fallback','blocked','unassessable','failed'"
            ") AND length(trim(worker_id)) > 0 "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL)",
            name="ck_dimension_calibration_items_lifecycle",
        ),
        CheckConstraint(
            "(status = 'completed' "
            "AND resolution_snapshot_json IS NOT NULL "
            "AND precheck_json IS NOT NULL "
            "AND aesthetic_json IS NOT NULL "
            "AND scoring_json IS NOT NULL "
            "AND score IS NOT NULL AND level IS NOT NULL "
            "AND confidence IS NOT NULL "
            "AND error_type IS NULL AND error_message = '') "
            "OR (status IN ('core_fallback','blocked','unassessable') "
            "AND resolution_snapshot_json IS NOT NULL "
            "AND precheck_json IS NOT NULL "
            "AND aesthetic_json IS NULL AND scoring_json IS NULL "
            "AND score IS NULL AND level IS NULL AND confidence IS NULL "
            "AND error_type IS NULL AND error_message = '') "
            "OR (status = 'failed' AND length(trim(error_type)) > 0 "
            "AND length(trim(error_message)) > 0 "
            "AND aesthetic_json IS NULL AND scoring_json IS NULL "
            "AND score IS NULL AND level IS NULL AND confidence IS NULL) "
            "OR status IN ('queued','processing')",
            name="ck_dimension_calibration_items_terminal_payload",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("dimension_calibration_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    asset_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), default="queued", index=True
    )
    worker_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    resolution_snapshot_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    precheck_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    aesthetic_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    level: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[DimensionCalibrationRun] = relationship(
        back_populates="items"
    )
    asset: Mapped[Asset] = relationship()


class LoopRun(Base):
    __tablename__ = "loop_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('waiting_result','machine_converged','needs_human')",
            name="ck_loop_runs_status",
        ),
        CheckConstraint(
            "current_round BETWEEN 1 AND 3",
            name="ck_loop_runs_current_round",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(160), unique=True, index=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    strategy_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default="waiting_result", index=True
    )
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    asset: Mapped[Asset] = relationship()
    strategy_bundle: Mapped[StrategyBundle] = relationship()
    attempts: Mapped[list["LoopAttempt"]] = relationship(
        back_populates="loop_run",
        cascade="all, delete-orphan",
        order_by="LoopAttempt.business_round",
    )


class LoopAttempt(Base):
    __tablename__ = "loop_attempts"
    __table_args__ = (
        UniqueConstraint(
            "loop_run_id",
            "business_round",
            name="uq_loop_attempt_business_round",
        ),
        UniqueConstraint(
            "loop_run_id",
            "result_idempotency_key",
            name="uq_loop_attempt_result_idempotency",
        ),
        CheckConstraint(
            "business_round BETWEEN 1 AND 3",
            name="ck_loop_attempts_business_round",
        ),
        CheckConstraint(
            "kind IN ('base','targeted_recheck','arbitration')",
            name="ck_loop_attempts_kind",
        ),
        CheckConstraint(
            "status IN ('waiting_result','completed')",
            name="ck_loop_attempts_status",
        ),
        CheckConstraint(
            "technical_attempt BETWEEN 0 AND 2",
            name="ck_loop_attempts_technical_attempt",
        ),
        CheckConstraint(
            "(business_round = 1 AND kind = 'base') OR "
            "(business_round = 2 AND kind = 'targeted_recheck') OR "
            "(business_round = 3 AND kind = 'arbitration')",
            name="ck_loop_attempts_round_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    loop_run_id: Mapped[int] = mapped_column(
        ForeignKey("loop_runs.id", ondelete="CASCADE"), index=True
    )
    business_round: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    target_dimensions_json: Mapped[str] = mapped_column(Text, default="[]")
    input_evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    normalized_result_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    conflict_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(
        String(30), default="waiting_result", index=True
    )
    technical_attempt: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_idempotency_key: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    result_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    loop_run: Mapped[LoopRun] = relationship(back_populates="attempts")


class CircuitBreaker(Base):
    __tablename__ = "circuit_breakers"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_key",
            name="uq_circuit_breaker_scope",
        ),
        CheckConstraint(
            "scope_type IN ('strategy','batch')",
            name="ck_circuit_breakers_scope_type",
        ),
        CheckConstraint(
            "state IN ('closed','open')",
            name="ck_circuit_breakers_state",
        ),
        CheckConstraint(
            "failure_count >= 0",
            name="ck_circuit_breakers_failure_count",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20), index=True)
    scope_key: Mapped[str] = mapped_column(String(160), index=True)
    state: Mapped[str] = mapped_column(String(20), default="closed", index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reset_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class QueueSchedulerState(Base):
    __tablename__ = "queue_scheduler_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_queue_scheduler_state_singleton"),
        CheckConstraint("dispatch_count >= 0", name="ck_queue_scheduler_dispatch_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    policy_version: Mapped[str] = mapped_column(
        String(80), default="queue-policy-v1"
    )
    global_limit: Mapped[int] = mapped_column(Integer, default=1)
    validation_deficit: Mapped[int] = mapped_column(Integer, default=0)
    interactive_deficit: Mapped[int] = mapped_column(Integer, default=0)
    production_batch_deficit: Mapped[int] = mapped_column(Integer, default=0)
    canary_deficit: Mapped[int] = mapped_column(Integer, default=0)
    recovery_deficit: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_count: Mapped[int] = mapped_column(Integer, default=0)
    last_recovery_dispatch: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        CheckConstraint(
            "review_stage IN ('initial','secondary','arbitration','completed')",
            name="ck_evaluation_results_review_stage",
        ),
        CheckConstraint(
            "review_revision >= 0",
            name="ck_evaluation_results_review_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("evaluation_jobs.id", ondelete="CASCADE"), unique=True)
    strategy_bundle_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    strategy_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    precheck_json: Mapped[str] = mapped_column(Text)
    aesthetic_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_json: Mapped[str] = mapped_column(Text)
    raw_response_a: Mapped[str] = mapped_column(Text)
    raw_response_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response_risk_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    level: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_stage: Mapped[str] = mapped_column(
        String(20), default="initial", index=True
    )
    review_revision: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str] = mapped_column(String(200))
    prompt_a_version: Mapped[str] = mapped_column(String(40))
    prompt_b_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_review_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rubric_version: Mapped[str] = mapped_column(String(40))
    engine_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    asset: Mapped[Asset] = relationship()
    job: Mapped[EvaluationJob] = relationship()
    strategy_bundle: Mapped[StrategyBundle | None] = relationship()
    reviews: Mapped[list["HumanReview"]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
        order_by="HumanReview.created_at",
    )
    review_panel: Mapped["ReviewPanel | None"] = relationship(
        back_populates="evaluation",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="ReviewPanel.evaluation_id",
    )


class StrategyBundleImmutableError(ValueError):
    """Raised when persisted strategy history is changed in place."""


class DimensionSchemaImmutableError(ValueError):
    """Raised when a published dimension schema is changed in place."""


class DimensionRoutePolicyImmutableError(ValueError):
    """Raised when a published dimension route policy is changed in place."""


class DimensionRoutePolicyContractError(ValueError):
    """Raised when a route policy definition and hash disagree."""


class DimensionCalibrationFrozenError(ValueError):
    """Raised when an isolated calibration audit record is changed."""


class StrategySnapshotRequiredError(ValueError):
    """Raised when a new result lacks its complete strategy binding."""


@event.listens_for(EvaluationJob, "before_insert")
def _apply_evaluation_job_queue_defaults(
    _mapper: object,
    _connection: Connection,
    target: EvaluationJob,
) -> None:
    queue_class = target.queue_class or "production_batch"
    if (
        target.regression_item_id is not None
        or target.baseline_regression_item_id is not None
    ) and queue_class == "production_batch":
        queue_class = "validation"
    target.queue_class = queue_class
    target.origin_queue_class = target.origin_queue_class or queue_class


@event.listens_for(EvaluationJob, "after_insert")
def _set_evaluation_job_root(
    _mapper: object,
    connection: Connection,
    target: EvaluationJob,
) -> None:
    if target.root_job_id is not None:
        return
    connection.execute(
        EvaluationJob.__table__.update()
        .where(EvaluationJob.__table__.c.id == target.id)
        .values(root_job_id=target.id)
    )
    attributes.set_committed_value(target, "root_job_id", target.id)


def _strategy_contract_is_active(connection: Connection) -> bool:
    """Return whether migration 11 has activated the strict result contract.

    Pre-v11 databases may contain historical NULL strategy fields. The migration
    marker lets those rows remain readable while all post-v11 inserts are strict.
    """
    migrations_table = connection.exec_driver_sql(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).first()
    if migrations_table is None:
        return False
    return (
        connection.exec_driver_sql(
            "SELECT 1 FROM schema_migrations WHERE version = 11"
        ).first()
        is not None
    )


def _contract_canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _contract_sha256(value: object) -> str:
    return hashlib.sha256(
        _contract_canonical_json(value).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_v2_dimension_snapshot(
    snapshot: dict[str, object],
    bundle: object,
    connection: Connection,
) -> None:
    required_bundle_keys = {
        "agent_plan_version",
        "dimension_route_policy_id",
        "dimension_schema_set",
        "label_field_set",
        "resolved_schema_contract_version",
    }
    required_resolution_keys = {
        "resolved_dimension_schema_id",
        "resolved_dimension_schema_key",
        "resolved_dimension_schema_version",
        "resolved_dimension_schema_hash",
        "resolved_dimensions_snapshot",
        "resolved_prompt_b_hash",
        "route_decision_snapshot",
        "resolved_snapshot_hash",
    }
    if not required_bundle_keys.issubset(snapshot) or not (
        required_resolution_keys.issubset(snapshot)
    ):
        raise StrategySnapshotRequiredError(
            "strategy-bundle-v2 缺少维度身份或解析结果"
        )

    try:
        bundle_dimension_set = json.loads(
            bundle["dimension_schema_set_snapshot"]
        )
        bundle_label_set = json.loads(bundle["label_field_set_snapshot"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise StrategySnapshotRequiredError(
            "StrategyBundle 的维度合同已损坏"
        ) from exc

    if (
        snapshot["dimension_route_policy_id"]
        != bundle["dimension_route_policy_id"]
        or snapshot["dimension_schema_set"] != bundle_dimension_set
        or snapshot["label_field_set"] != bundle_label_set
        or snapshot["resolved_schema_contract_version"]
        != bundle["resolved_schema_contract_version"]
    ):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 与 StrategyBundle 维度合同不一致"
        )

    bundle_definition_keys = (
        "schema_version",
        "model_id",
        "model_config",
        "prompt_a",
        "prompt_b",
        "rubric_version",
        "engine_version",
        "sampling_policy",
        "risk_review_version",
        "agent_plan_version",
        "dimension_route_policy_id",
        "dimension_schema_set",
        "label_field_set",
        "resolved_schema_contract_version",
    )
    bundle_definition = {
        key: snapshot[key] for key in bundle_definition_keys
    }
    if _contract_sha256(bundle_definition) != snapshot["canonical_hash"]:
        raise StrategySnapshotRequiredError(
            "strategy-bundle-v2 规范哈希无法复算"
        )

    schema_id = snapshot["resolved_dimension_schema_id"]
    schema_key = snapshot["resolved_dimension_schema_key"]
    schema_version = snapshot["resolved_dimension_schema_version"]
    schema_hash = snapshot["resolved_dimension_schema_hash"]
    definition = snapshot["resolved_dimensions_snapshot"]
    route_decision = snapshot["route_decision_snapshot"]
    if (
        not isinstance(schema_id, int)
        or schema_id <= 0
        or not isinstance(schema_key, str)
        or not schema_key
        or not isinstance(schema_version, str)
        or not schema_version
        or not _is_sha256(schema_hash)
        or not isinstance(definition, dict)
        or not isinstance(route_decision, dict)
        or route_decision.get("policy_id")
        != bundle["dimension_route_policy_id"]
        or route_decision.get("dimension_schema_id") != schema_id
        or route_decision.get("dimension_schema_hash") != schema_hash
    ):
        raise StrategySnapshotRequiredError(
            "strategy-bundle-v2 的维度解析身份不完整"
        )

    set_entries = (
        bundle_dimension_set.get("schemas")
        if isinstance(bundle_dimension_set, dict)
        else None
    )
    if not isinstance(set_entries, list) or not any(
        isinstance(entry, dict)
        and entry.get("schema_key") == schema_key
        and entry.get("version") == schema_version
        and entry.get("canonical_hash") == schema_hash
        and entry.get("definition") == definition
        for entry in set_entries
    ):
        raise StrategySnapshotRequiredError(
            "解析后的维度 Schema 不在冻结候选集合中"
        )

    persisted_schema = connection.exec_driver_sql(
        """
        SELECT schema_key, version, canonical_hash, definition_json
        FROM dimension_schemas
        WHERE id = ?
        """,
        (schema_id,),
    ).mappings().first()
    if persisted_schema is None:
        raise StrategySnapshotRequiredError(
            "resolved_dimension_schema_id 不存在"
        )
    try:
        persisted_definition = json.loads(
            persisted_schema["definition_json"]
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise StrategySnapshotRequiredError(
            "已发布 DimensionSchema 定义损坏"
        ) from exc
    if (
        persisted_schema["schema_key"] != schema_key
        or persisted_schema["version"] != schema_version
        or persisted_schema["canonical_hash"] != schema_hash
        or persisted_definition != definition
        or _contract_sha256(definition) != schema_hash
    ):
        raise StrategySnapshotRequiredError(
            "结果维度快照与已发布 DimensionSchema 不一致"
        )

    prompt_b = snapshot["prompt_b"]
    expected_prompt_hash = (
        _contract_sha256(prompt_b)
        if isinstance(prompt_b, dict)
        else None
    )
    if snapshot["resolved_prompt_b_hash"] != expected_prompt_hash:
        raise StrategySnapshotRequiredError(
            "resolved_prompt_b_hash 无法复算"
        )

    resolution_keys = (
        "resolved_dimension_schema_id",
        "resolved_dimension_schema_key",
        "resolved_dimension_schema_version",
        "resolved_dimension_schema_hash",
        "resolved_dimensions_snapshot",
        "resolved_prompt_b_hash",
        "route_decision_snapshot",
    )
    resolution = {key: snapshot[key] for key in resolution_keys}
    if (
        not _is_sha256(snapshot["resolved_snapshot_hash"])
        or _contract_sha256(resolution)
        != snapshot["resolved_snapshot_hash"]
    ):
        raise StrategySnapshotRequiredError(
            "维度解析快照哈希无法复算"
        )


def _validate_strategy_snapshot(
    result: EvaluationResult, connection: Connection
) -> None:
    if result.strategy_bundle_id is None:
        raise StrategySnapshotRequiredError(
            "新评测结果必须绑定 strategy_bundle_id"
        )
    if not isinstance(result.strategy_snapshot_json, str) or not result.strategy_snapshot_json.strip():
        raise StrategySnapshotRequiredError(
            "新评测结果必须保存完整 strategy_snapshot_json"
        )
    try:
        snapshot = json.loads(result.strategy_snapshot_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 必须是有效 JSON"
        ) from exc
    if not isinstance(snapshot, dict):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 必须是 JSON 对象"
        )

    required_keys = {
        "bundle_id",
        "canonical_hash",
        "schema_version",
        "model_id",
        "model_config",
        "prompt_a",
        "prompt_b",
        "rubric_version",
        "engine_version",
        "sampling_policy",
        "risk_review_version",
    }
    if not required_keys.issubset(snapshot):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 缺少完整策略字段"
        )
    if snapshot["bundle_id"] != result.strategy_bundle_id:
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 与 strategy_bundle_id 不一致"
        )
    if (
        not _is_sha256(snapshot["canonical_hash"])
        or snapshot["schema_version"]
        not in {"strategy-bundle-v1", "strategy-bundle-v2"}
        or not isinstance(snapshot["model_config"], dict)
        or not isinstance(snapshot["model_id"], str)
        or not snapshot["model_id"]
        or not isinstance(snapshot["rubric_version"], str)
        or not snapshot["rubric_version"]
        or not isinstance(snapshot["engine_version"], str)
        or not snapshot["engine_version"]
    ):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 的策略标识不完整"
        )

    model_config_keys = {
        "name",
        "provider",
        "base_url",
        "api_path",
        "model_id",
        "temperature",
        "max_tokens",
        "timeout_seconds",
        "max_retries",
        "max_concurrency",
        "structured_output",
        "high_risk_review_enabled",
    }
    if not model_config_keys.issubset(snapshot["model_config"]):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 的 model_config 不完整"
        )

    for key in ("prompt_a", "prompt_b"):
        prompt = snapshot[key]
        if key == "prompt_b" and prompt is None:
            continue
        if not isinstance(prompt, dict):
            raise StrategySnapshotRequiredError(
                f"strategy_snapshot_json 的 {key} 不完整"
            )
        prompt_keys = {
            "id",
            "stage",
            "version",
            "name",
            "rubric_version",
            "system_prompt",
            "user_prompt",
        }
        if (
            not prompt_keys.issubset(prompt)
            or not isinstance(prompt["id"], int)
            or prompt["id"] <= 0
            or not isinstance(prompt["version"], str)
            or not prompt["version"]
            or not isinstance(prompt["system_prompt"], str)
            or not isinstance(prompt["user_prompt"], str)
        ):
            raise StrategySnapshotRequiredError(
                f"strategy_snapshot_json 的 {key} 不完整"
            )

    sampling_policy = snapshot["sampling_policy"]
    if sampling_policy is not None:
        policy_keys = {
            "id",
            "revision",
            "sample_rate",
            "low_confidence_threshold",
            "medium_confidence_threshold",
            "cold_start_required_count",
            "high_level_required_from",
        }
        if not isinstance(sampling_policy, dict) or not policy_keys.issubset(
            sampling_policy
        ):
            raise StrategySnapshotRequiredError(
                "strategy_snapshot_json 的 sampling_policy 不完整"
            )

    bundle = connection.exec_driver_sql(
        """
        SELECT canonical_hash, strategy_schema_version, model_id,
               prompt_a_version, prompt_b_version,
               rubric_version, engine_version, sampling_policy_revision,
               risk_review_version, agent_plan_version,
               dimension_route_policy_id, dimension_schema_set_snapshot,
               label_field_set_snapshot, resolved_schema_contract_version
        FROM strategy_bundles
        WHERE id = ?
        """,
        (result.strategy_bundle_id,),
    ).mappings().first()
    if bundle is None:
        raise StrategySnapshotRequiredError("strategy_bundle_id 不存在")
    if snapshot["schema_version"] != bundle["strategy_schema_version"]:
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 与 StrategyBundle 快照版本不一致"
        )

    snapshot_prompt_b = snapshot["prompt_b"]
    snapshot_bundle_values = {
        "canonical_hash": snapshot["canonical_hash"],
        "model_id": snapshot["model_id"],
        "prompt_a_version": snapshot["prompt_a"]["version"],
        "prompt_b_version": (
            snapshot_prompt_b["version"] if snapshot_prompt_b is not None else None
        ),
        "rubric_version": snapshot["rubric_version"],
        "engine_version": snapshot["engine_version"],
        "sampling_policy_revision": (
            sampling_policy["revision"] if sampling_policy is not None else None
        ),
        "risk_review_version": snapshot["risk_review_version"],
    }
    if any(
        snapshot_bundle_values[key] != bundle[key]
        for key in snapshot_bundle_values
    ):
        raise StrategySnapshotRequiredError(
            "strategy_snapshot_json 与所绑定 StrategyBundle 定义不一致"
        )
    if snapshot["schema_version"] == "strategy-bundle-v2":
        _validate_v2_dimension_snapshot(snapshot, bundle, connection)
    if (
        result.model_id != bundle["model_id"]
        or result.prompt_a_version != bundle["prompt_a_version"]
        or (
            result.prompt_b_version is not None
            and result.prompt_b_version != bundle["prompt_b_version"]
        )
        or result.rubric_version != bundle["rubric_version"]
        or result.engine_version != bundle["engine_version"]
        or result.risk_review_version != bundle["risk_review_version"]
    ):
        raise StrategySnapshotRequiredError(
            "EvaluationResult 版本字段与 StrategyBundle 不一致"
        )


@event.listens_for(StrategyBundle, "before_update")
def _prevent_strategy_bundle_update(
    _mapper: object, _connection: Connection, _target: StrategyBundle
) -> None:
    raise StrategyBundleImmutableError("StrategyBundle 持久化后禁止原地更新")


@event.listens_for(StrategyBundle, "before_delete")
def _prevent_strategy_bundle_delete(
    _mapper: object, _connection: Connection, _target: StrategyBundle
) -> None:
    raise StrategyBundleImmutableError("StrategyBundle 是永久审计记录，禁止删除")


def _persisted_dimension_schema_status(
    connection: Connection,
    target: DimensionSchema,
) -> str | None:
    if target.id is None:
        return None
    return connection.exec_driver_sql(
        "SELECT status FROM dimension_schemas WHERE id = ?",
        (target.id,),
    ).scalar_one_or_none()


@event.listens_for(DimensionSchema, "before_update")
def _prevent_published_dimension_schema_update(
    _mapper: object,
    connection: Connection,
    target: DimensionSchema,
) -> None:
    if _persisted_dimension_schema_status(connection, target) in {
        "published",
        "retired",
    }:
        raise DimensionSchemaImmutableError(
            "已发布的 DimensionSchema 禁止原地更新；请创建新版本"
        )


@event.listens_for(DimensionSchema, "before_delete")
def _prevent_published_dimension_schema_delete(
    _mapper: object,
    connection: Connection,
    target: DimensionSchema,
) -> None:
    if _persisted_dimension_schema_status(connection, target) in {
        "published",
        "retired",
    }:
        raise DimensionSchemaImmutableError(
            "已发布的 DimensionSchema 是永久审计记录，禁止删除"
        )


def _persisted_dimension_route_policy_status(
    connection: Connection,
    target: DimensionRoutePolicy,
) -> str | None:
    if target.id is None:
        return None
    return connection.exec_driver_sql(
        "SELECT status FROM dimension_route_policies WHERE id = ?",
        (target.id,),
    ).scalar_one_or_none()


def _validate_dimension_route_policy_contract(
    target: DimensionRoutePolicy,
) -> None:
    try:
        definition = json.loads(target.definition_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DimensionRoutePolicyContractError(
            "DimensionRoutePolicy 定义不是合法 JSON"
        ) from exc
    if not isinstance(definition, dict):
        raise DimensionRoutePolicyContractError(
            "DimensionRoutePolicy 定义必须是 JSON 对象"
        )
    canonical = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if target.canonical_hash != expected_hash:
        raise DimensionRoutePolicyContractError(
            "DimensionRoutePolicy 规范哈希与定义不一致"
        )


@event.listens_for(DimensionRoutePolicy, "before_insert")
def _validate_dimension_route_policy_insert(
    _mapper: object,
    _connection: Connection,
    target: DimensionRoutePolicy,
) -> None:
    _validate_dimension_route_policy_contract(target)


@event.listens_for(DimensionRoutePolicy, "before_update")
def _prevent_published_dimension_route_policy_update(
    _mapper: object,
    connection: Connection,
    target: DimensionRoutePolicy,
) -> None:
    if _persisted_dimension_route_policy_status(connection, target) in {
        "published",
        "retired",
    }:
        raise DimensionRoutePolicyImmutableError(
            "已发布的 DimensionRoutePolicy 禁止原地更新；请创建新版本"
        )
    _validate_dimension_route_policy_contract(target)


@event.listens_for(DimensionRoutePolicy, "before_delete")
def _prevent_published_dimension_route_policy_delete(
    _mapper: object,
    connection: Connection,
    target: DimensionRoutePolicy,
) -> None:
    if _persisted_dimension_route_policy_status(connection, target) in {
        "published",
        "retired",
    }:
        raise DimensionRoutePolicyImmutableError(
            "已发布的 DimensionRoutePolicy 是永久审计记录，禁止删除"
        )


_CALIBRATION_RUN_FROZEN_FIELDS = (
    "run_key",
    "strategy_bundle_id",
    "strategy_bundle_hash",
    "strategy_snapshot_json",
    "asset_manifest_json",
    "definition_hash",
    "total",
    "created_by",
    "created_at",
)
_CALIBRATION_ITEM_FROZEN_FIELDS = (
    "run_id",
    "asset_id",
    "asset_snapshot_json",
    "created_at",
)
_CALIBRATION_RUN_TERMINAL_STATUSES = {
    "completed",
    "partial_failed",
    "failed",
}
_CALIBRATION_ITEM_TERMINAL_STATUSES = {
    "completed",
    "core_fallback",
    "blocked",
    "unassessable",
    "failed",
}


def _persisted_calibration_status(
    connection: Connection,
    *,
    table_name: str,
    row_id: int | None,
) -> str | None:
    if row_id is None:
        return None
    return connection.exec_driver_sql(
        f"SELECT status FROM {table_name} WHERE id = ?",
        (row_id,),
    ).scalar_one_or_none()


def _changed_fields(target: object, names: tuple[str, ...]) -> set[str]:
    return {
        name
        for name in names
        if attributes.get_history(target, name).has_changes()
    }


@event.listens_for(DimensionCalibrationRun, "before_update")
def _protect_dimension_calibration_run_update(
    _mapper: object,
    connection: Connection,
    target: DimensionCalibrationRun,
) -> None:
    persisted_status = _persisted_calibration_status(
        connection,
        table_name="dimension_calibration_runs",
        row_id=target.id,
    )
    if persisted_status in _CALIBRATION_RUN_TERMINAL_STATUSES:
        raise DimensionCalibrationFrozenError(
            "维度校准运行进入终态后禁止更新"
        )
    changed = _changed_fields(
        target,
        _CALIBRATION_RUN_FROZEN_FIELDS,
    )
    if changed:
        raise DimensionCalibrationFrozenError(
            "维度校准运行冻结字段禁止更新："
            + "、".join(sorted(changed))
        )


@event.listens_for(DimensionCalibrationRun, "before_delete")
def _protect_dimension_calibration_run_delete(
    _mapper: object,
    _connection: Connection,
    _target: DimensionCalibrationRun,
) -> None:
    raise DimensionCalibrationFrozenError(
        "维度校准运行是永久审计记录，禁止删除"
    )


@event.listens_for(DimensionCalibrationItem, "before_update")
def _protect_dimension_calibration_item_update(
    _mapper: object,
    connection: Connection,
    target: DimensionCalibrationItem,
) -> None:
    persisted_status = _persisted_calibration_status(
        connection,
        table_name="dimension_calibration_items",
        row_id=target.id,
    )
    if persisted_status in _CALIBRATION_ITEM_TERMINAL_STATUSES:
        raise DimensionCalibrationFrozenError(
            "维度校准项进入终态后禁止更新"
        )
    changed = _changed_fields(
        target,
        _CALIBRATION_ITEM_FROZEN_FIELDS,
    )
    if changed:
        raise DimensionCalibrationFrozenError(
            "维度校准项冻结字段禁止更新："
            + "、".join(sorted(changed))
        )


@event.listens_for(DimensionCalibrationItem, "before_delete")
def _protect_dimension_calibration_item_delete(
    _mapper: object,
    _connection: Connection,
    _target: DimensionCalibrationItem,
) -> None:
    raise DimensionCalibrationFrozenError(
        "维度校准项是永久审计记录，禁止删除"
    )


@event.listens_for(EvaluationResult, "before_insert")
def _require_strategy_snapshot_for_new_result(
    _mapper: object, connection: Connection, target: EvaluationResult
) -> None:
    if _strategy_contract_is_active(connection):
        _validate_strategy_snapshot(target, connection)


class HumanReview(Base):
    __tablename__ = "human_reviews"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('initial','secondary','arbitration')",
            name="ck_human_reviews_stage",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), index=True
    )
    reviewer_name: Mapped[str] = mapped_column(String(80))
    panel_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_panels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    panel_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default="initial", index=True)
    decision: Mapped[str] = mapped_column(String(30))
    corrected_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    corrected_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    corrections_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluation: Mapped[EvaluationResult] = relationship(back_populates="reviews")


class ReviewPanel(Base):
    __tablename__ = "review_panels"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_review_panels_evaluation"),
        CheckConstraint(
            "required_reviewers >= 1 "
            "AND required_reviewers <= 9 "
            "AND required_reviewers % 2 = 1",
            name="ck_review_panels_odd_reviewers",
        ),
        CheckConstraint(
            "status IN ('collecting','lead_adjudication','completed')",
            name="ck_review_panels_status",
        ),
        CheckConstraint("revision >= 0", name="ck_review_panels_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    required_reviewers: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(30), default="collecting", index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    final_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("human_reviews.id", ondelete="RESTRICT"), nullable=True
    )
    final_truth_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    evaluation: Mapped[EvaluationResult] = relationship(
        back_populates="review_panel",
        foreign_keys=[evaluation_id],
    )


class OptimizationCaseQueue(Base):
    __tablename__ = "optimization_case_queue"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_optimization_case_key"),
        CheckConstraint(
            "source_type IN ('human_review','production_feedback','baseline_regression')",
            name="ck_optimization_case_source_type",
        ),
        CheckConstraint(
            "status IN ('pending','batched','processing','completed','failed')",
            name="ck_optimization_case_status",
        ),
        CheckConstraint(
            "severity IN ('P0','P1','P2','P3')",
            name="ck_optimization_case_severity",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_optimization_case_attempt_count",
        ),
        CheckConstraint(
            "(source_type = 'human_review' "
            "AND evaluation_id IS NOT NULL "
            "AND final_review_id IS NOT NULL "
            "AND source_event_id IS NULL "
            "AND baseline_regression_item_id IS NULL) "
            "OR "
            "(source_type = 'production_feedback' "
            "AND evaluation_id IS NULL "
            "AND final_review_id IS NULL "
            "AND source_event_id IS NOT NULL "
            "AND baseline_regression_item_id IS NULL) "
            "OR "
            "(source_type = 'baseline_regression' "
            "AND evaluation_id IS NOT NULL "
            "AND final_review_id IS NULL "
            "AND source_event_id IS NULL "
            "AND baseline_regression_item_id IS NOT NULL)",
            name="ck_optimization_case_source_refs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(160), unique=True, index=True
    )
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    final_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("human_reviews.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(30), default="human_review", index=True
    )
    source_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("production_feedback_events.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    baseline_regression_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("baseline_regression_items.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="P2", index=True)
    case_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    automation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("automation_optimization_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AutomationPolicy(Base):
    __tablename__ = "automation_policies"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_automation_policy_singleton"),
        CheckConstraint(
            "case_threshold >= 1 AND case_threshold <= 1000",
            name="ck_automation_policy_case_threshold",
        ),
        CheckConstraint(
            "daily_budget_micros >= 0",
            name="ck_automation_policy_daily_budget",
        ),
        CheckConstraint(
            "cooldown_seconds >= 0",
            name="ck_automation_policy_cooldown",
        ),
        CheckConstraint(
            "max_candidates >= 1 AND max_candidates <= 5",
            name="ck_automation_policy_max_candidates",
        ),
        CheckConstraint(
            "lease_seconds >= 30 AND lease_seconds <= 3600",
            name="ck_automation_policy_lease_seconds",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 10",
            name="ck_automation_policy_max_attempts",
        ),
        CheckConstraint(
            "base_retry_seconds >= 1 AND base_retry_seconds <= 86400",
            name="ck_automation_policy_retry_seconds",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    case_threshold: Mapped[int] = mapped_column(Integer, default=10)
    immediate_severities_json: Mapped[str] = mapped_column(
        Text, default='["P0","P1"]'
    )
    daily_budget_micros: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=21600)
    max_candidates: Mapped[int] = mapped_column(Integer, default=1)
    lease_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    base_retry_seconds: Mapped[int] = mapped_column(Integer, default=60)
    updated_by: Mapped[str] = mapped_column(String(80), default="system")
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AutomationOptimizationRun(Base):
    __tablename__ = "automation_optimization_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_automation_optimization_run_key"),
        CheckConstraint(
            "status IN ("
            "'planned','awaiting_executor','processing','succeeded','running','awaiting_release_review',"
            "'failed','cancelled'"
            ")",
            name="ck_automation_optimization_run_status",
        ),
        CheckConstraint(
            "estimated_cost_micros >= 0 AND actual_cost_micros >= 0",
            name="ck_automation_optimization_run_costs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    base_prompt_version: Mapped[str] = mapped_column(String(40), index=True)
    policy_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(40), default="planned", index=True
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger_reason: Mapped[str] = mapped_column(String(80))
    case_ids_json: Mapped[str] = mapped_column(Text)
    frozen_input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    actual_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="automation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AutomationBudgetDay(Base):
    __tablename__ = "automation_budget_days"
    __table_args__ = (
        CheckConstraint(
            "reserved_micros >= 0 AND spent_micros >= 0",
            name="ck_automation_budget_day_costs",
        ),
    )

    budget_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    reserved_micros: Mapped[int] = mapped_column(Integer, default=0)
    spent_micros: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProductionFeedbackEvent(Base):
    __tablename__ = "production_feedback_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_production_feedback_event_id"),
        CheckConstraint(
            "status IN ('accepted','mapped','rejected')",
            name="ck_production_feedback_event_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    source_system: Mapped[str] = mapped_column(String(120), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="accepted", index=True
    )
    received_by: Mapped[str] = mapped_column(String(80), default="system")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_audit_event_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[str] = mapped_column(String(160), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ModelBenchmarkExperiment(Base):
    __tablename__ = "model_benchmark_experiments"
    __table_args__ = (
        UniqueConstraint("experiment_key", name="uq_model_benchmark_key"),
        CheckConstraint(
            "status IN ('draft','running','completed','failed','cancelled')",
            name="ck_model_benchmark_status",
        ),
        CheckConstraint(
            "execution_mode IN ('disabled','test','real')",
            name="ck_model_benchmark_execution_mode",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_key: Mapped[str] = mapped_column(
        String(160), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(20), default="draft", index=True
    )
    execution_mode: Mapped[str] = mapped_column(
        String(20), default="test", index=True
    )
    cohort_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    frozen_snapshot_json: Mapped[str] = mapped_column(Text)
    quality_gate_json: Mapped[str] = mapped_column(Text)
    max_round_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    actual_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ModelBenchmarkVariant(Base):
    __tablename__ = "model_benchmark_variants"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "model_key", name="uq_model_benchmark_variant"
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_model_benchmark_variant_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("model_benchmark_experiments.id", ondelete="CASCADE"),
        index=True,
    )
    model_key: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(200))
    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    pricing_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    observations_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SampleSet(Base):
    __tablename__ = "sample_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="test", index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list["SampleSetItem"]] = relationship(
        back_populates="sample_set",
        cascade="all, delete-orphan",
        order_by="SampleSetItem.created_at",
    )


class SampleSetItem(Base):
    __tablename__ = "sample_set_items"
    __table_args__ = (UniqueConstraint("sample_set_id", "asset_id", name="uq_sample_set_asset"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_set_id: Mapped[int] = mapped_column(
        ForeignKey("sample_sets.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source_result_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), index=True
    )
    expected_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    expected_category: Mapped[str] = mapped_column(String(120), default="无法判断")
    truth_json: Mapped[str] = mapped_column(Text, default="{}")
    truth_revision: Mapped[int] = mapped_column(Integer, default=1)
    truth_updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    truth_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sample_set: Mapped[SampleSet] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship()
    source_result: Mapped[EvaluationResult] = relationship()


class SampleTruthRevision(Base):
    __tablename__ = "sample_truth_revisions"
    __table_args__ = (
        UniqueConstraint("sample_item_id", "revision", name="uq_sample_truth_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_item_id: Mapped[int] = mapped_column(
        ForeignKey("sample_set_items.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    truth_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    reviewer_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sample_item: Mapped[SampleSetItem] = relationship()


class BaselineSet(Base):
    __tablename__ = "baseline_sets"
    __table_args__ = (
        CheckConstraint(
            "default_expected_level IN ('L1','L2','L3','L4','L5')",
            name="ck_baseline_sets_default_level",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    default_expected_level: Mapped[str] = mapped_column(String(10), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    items: Mapped[list["BaselineSetItem"]] = relationship(
        back_populates="baseline_set",
        cascade="all, delete-orphan",
        order_by="BaselineSetItem.id",
    )
    runs: Mapped[list["BaselineRegressionRun"]] = relationship(
        back_populates="baseline_set",
        order_by="BaselineRegressionRun.sequence_no",
    )


class BaselineSetItem(Base):
    __tablename__ = "baseline_set_items"
    __table_args__ = (
        UniqueConstraint(
            "baseline_set_id", "asset_id", name="uq_baseline_set_asset"
        ),
        CheckConstraint(
            "expected_level IN ('L1','L2','L3','L4','L5')",
            name="ck_baseline_set_items_expected_level",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_set_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_sets.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    source_package_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_packages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    expected_level: Mapped[str] = mapped_column(String(10), index=True)
    asset_snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    baseline_set: Mapped[BaselineSet] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship()
    source_package: Mapped[MaterialPackage | None] = relationship()


class BaselineRegressionRun(Base):
    __tablename__ = "baseline_regression_runs"
    __table_args__ = (
        UniqueConstraint(
            "baseline_set_id", "sequence_no", name="uq_baseline_run_sequence"
        ),
        CheckConstraint(
            "status IN ('running','completed','partial_failed','failed')",
            name="ck_baseline_regression_runs_status",
        ),
        CheckConstraint(
            "sequence_no >= 1", name="ck_baseline_regression_runs_sequence"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_set_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_sets.id", ondelete="RESTRICT"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    previous_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("baseline_regression_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    strategy_bundle_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"), index=True
    )
    strategy_snapshot_json: Mapped[str] = mapped_column(Text)
    baseline_set_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    total: Mapped[int] = mapped_column(Integer)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    valid_predictions: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    baseline_set: Mapped[BaselineSet] = relationship(back_populates="runs")
    previous_run: Mapped["BaselineRegressionRun | None"] = relationship(
        remote_side=[id]
    )
    strategy_bundle: Mapped[StrategyBundle] = relationship()
    items: Mapped[list["BaselineRegressionItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="BaselineRegressionItem.id",
    )


class BaselineRegressionItem(Base):
    __tablename__ = "baseline_regression_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "baseline_set_item_id", name="uq_baseline_run_set_item"
        ),
        CheckConstraint(
            "expected_level IN ('L1','L2','L3','L4','L5')",
            name="ck_baseline_regression_items_expected_level",
        ),
        CheckConstraint(
            "status IN ('queued','completed','failed')",
            name="ck_baseline_regression_items_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_regression_runs.id", ondelete="CASCADE"), index=True
    )
    baseline_set_item_id: Mapped[int] = mapped_column(
        ForeignKey("baseline_set_items.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    expected_level: Mapped[str] = mapped_column(String(10), index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    result_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[BaselineRegressionRun] = relationship(back_populates="items")
    baseline_set_item: Mapped[BaselineSetItem] = relationship()
    asset: Mapped[Asset] = relationship()
    job: Mapped[EvaluationJob | None] = relationship(foreign_keys=[job_id])
    evaluation: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[evaluation_id]
    )


class BaselineFrozenError(ValueError):
    """Raised when a frozen baseline definition is changed in place."""


@event.listens_for(BaselineSet, "before_update")
@event.listens_for(BaselineSet, "before_delete")
def _prevent_baseline_set_mutation(
    _mapper: object, _connection: Connection, _target: BaselineSet
) -> None:
    raise BaselineFrozenError("BaselineSet 创建后不可修改或删除")


@event.listens_for(BaselineSetItem, "before_update")
@event.listens_for(BaselineSetItem, "before_delete")
def _prevent_baseline_set_item_mutation(
    _mapper: object, _connection: Connection, _target: BaselineSetItem
) -> None:
    raise BaselineFrozenError("BaselineSet 条目创建后不可修改或删除")


class PromptRegressionRun(Base):
    __tablename__ = "prompt_regression_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sample_set_id: Mapped[int] = mapped_column(
        ForeignKey("sample_sets.id", ondelete="CASCADE"), index=True
    )
    trigger_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_a_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="CASCADE"), index=True
    )
    prompt_b_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="CASCADE"), index=True
    )
    regression_mode: Mapped[str] = mapped_column(
        String(30), default="single", index=True
    )
    baseline_strategy_bundle_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_strategy_bundle_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_bundles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    baseline_strategy_snapshot_json: Mapped[str] = mapped_column(
        Text, default="{}"
    )
    candidate_strategy_snapshot_json: Mapped[str] = mapped_column(
        Text, default="{}"
    )
    sample_set_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    sample_manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    metric_rules_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    metric_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    recommendation: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    approval_status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_note: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.9)
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sample_set: Mapped[SampleSet] = relationship()
    prompt_a: Mapped[PromptVersion] = relationship(foreign_keys=[prompt_a_id])
    prompt_b: Mapped[PromptVersion] = relationship(foreign_keys=[prompt_b_id])
    baseline_strategy_bundle: Mapped[StrategyBundle | None] = relationship(
        foreign_keys=[baseline_strategy_bundle_id]
    )
    candidate_strategy_bundle: Mapped[StrategyBundle | None] = relationship(
        foreign_keys=[candidate_strategy_bundle_id]
    )
    items: Mapped[list["PromptRegressionItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PromptRegressionItem.id"
    )


class PromptRegressionItem(Base):
    __tablename__ = "prompt_regression_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_regression_runs.id", ondelete="CASCADE"), index=True
    )
    sample_item_id: Mapped[int] = mapped_column(
        ForeignKey("sample_set_items.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sample_role: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    source_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("human_reviews.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    truth_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    baseline_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    baseline_result_json: Mapped[str] = mapped_column(Text, default="{}")
    candidate_result_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comparison_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[PromptRegressionRun] = relationship(back_populates="items")
    sample_item: Mapped[SampleSetItem] = relationship()
    evaluation: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[evaluation_id]
    )
    source_evaluation: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[source_evaluation_id]
    )
    source_review: Mapped[HumanReview | None] = relationship(
        foreign_keys=[source_review_id]
    )
    baseline_evaluation: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[baseline_evaluation_id]
    )
    candidate_evaluation: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[candidate_evaluation_id]
    )


class PromptOptimizationRun(Base):
    __tablename__ = "prompt_optimization_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    base_prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="CASCADE"), index=True
    )
    sample_set_id: Mapped[int] = mapped_column(
        ForeignKey("sample_sets.id", ondelete="CASCADE"), index=True
    )
    optimizer_model_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    corrected_count: Mapped[int] = mapped_column(Integer, default=0)
    diagnosis_json: Mapped[str] = mapped_column(Text, default="{}")
    diagnostic_audit_json: Mapped[str] = mapped_column(
        Text,
        default=PROMPT_OPTIMIZATION_AUDIT_DEFAULT,
        server_default=sql_text(
            f"'{PROMPT_OPTIMIZATION_AUDIT_DEFAULT}'"
        ),
    )
    synthesis_audit_json: Mapped[str] = mapped_column(
        Text,
        default=PROMPT_OPTIMIZATION_AUDIT_DEFAULT,
        server_default=sql_text(
            f"'{PROMPT_OPTIMIZATION_AUDIT_DEFAULT}'"
        ),
    )
    candidate_system_prompt: Mapped[str] = mapped_column(Text, default="")
    candidate_user_prompt: Mapped[str] = mapped_column(Text, default="")
    change_note: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    base_prompt: Mapped[PromptVersion] = relationship()
    sample_set: Mapped[SampleSet] = relationship()


class MigrationRun(Base):
    __tablename__ = "migration_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    baseline_model_id: Mapped[str] = mapped_column(String(200), index=True)
    candidate_model_id: Mapped[str] = mapped_column(String(200), index=True)
    sample_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MigrationItem(Base):
    __tablename__ = "migration_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("migration_runs.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    baseline_result_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), index=True
    )
    sample_expected_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    candidate_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    comparison_reason: Mapped[str] = mapped_column(Text, default="")
    human_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[MigrationRun] = relationship()
    asset: Mapped[Asset] = relationship()
    baseline_result: Mapped[EvaluationResult] = relationship(foreign_keys=[baseline_result_id])
    candidate_result: Mapped[EvaluationResult | None] = relationship(
        foreign_keys=[candidate_result_id]
    )
