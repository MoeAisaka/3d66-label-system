from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("evaluation_jobs.id", ondelete="CASCADE"), unique=True)
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
    reviews: Mapped[list["HumanReview"]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
        order_by="HumanReview.created_at",
    )


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), index=True
    )
    reviewer_name: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(30))
    corrected_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
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
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comparison_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[PromptRegressionRun] = relationship(back_populates="items")
    sample_item: Mapped[SampleSetItem] = relationship()
    evaluation: Mapped[EvaluationResult | None] = relationship()


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
