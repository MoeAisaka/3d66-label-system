from __future__ import annotations

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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(80), default="管理员")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40), index=True)
    system_prompt: Mapped[str] = mapped_column(Text)
    user_prompt: Mapped[str] = mapped_column(Text)
    rubric_version: Mapped[str] = mapped_column(String(40), default="rubric-v2.1")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


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


class StrategyBundle(Base):
    __tablename__ = "strategy_bundles"
    __table_args__ = (UniqueConstraint("canonical_hash", name="uq_strategy_canonical_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    model_config_snapshot: Mapped[str] = mapped_column(Text)
    prompt_a_version: Mapped[str] = mapped_column(String(40), index=True)
    prompt_b_version: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    rubric_version: Mapped[str] = mapped_column(String(40), index=True)
    engine_version: Mapped[str] = mapped_column(String(40), index=True)
    sampling_policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_review_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class StrategyBundleImmutableError(ValueError):
    """Raised when persisted strategy history is changed in place."""


class StrategySnapshotRequiredError(ValueError):
    """Raised when a new result lacks its complete strategy binding."""


@event.listens_for(EvaluationJob, "before_insert")
def _apply_evaluation_job_queue_defaults(
    _mapper: object,
    _connection: Connection,
    target: EvaluationJob,
) -> None:
    queue_class = target.queue_class or "production_batch"
    if target.regression_item_id is not None and queue_class == "production_batch":
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
        not isinstance(snapshot["canonical_hash"], str)
        or len(snapshot["canonical_hash"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in snapshot["canonical_hash"].lower()
        )
        or snapshot["schema_version"] != "strategy-bundle-v1"
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
        SELECT canonical_hash, model_id, prompt_a_version, prompt_b_version,
               rubric_version, engine_version, sampling_policy_revision,
               risk_review_version
        FROM strategy_bundles
        WHERE id = ?
        """,
        (result.strategy_bundle_id,),
    ).mappings().first()
    if bundle is None:
        raise StrategySnapshotRequiredError("strategy_bundle_id 不存在")

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


@event.listens_for(EvaluationResult, "before_insert")
def _require_strategy_snapshot_for_new_result(
    _mapper: object, connection: Connection, target: EvaluationResult
) -> None:
    if _strategy_contract_is_active(connection):
        _validate_strategy_snapshot(target, connection)


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), index=True
    )
    reviewer_name: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(30))
    corrected_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    corrected_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    corrections_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluation: Mapped[EvaluationResult] = relationship(back_populates="reviews")


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
