from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import re
import uuid
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Mapping

from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SkipValidation,
    WithJsonSchema,
    model_validator,
)
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .category_pipeline import (
    CATEGORY_KEY_PATTERN,
    DIMENSION_OPTIONS,
    MODEL_NODE_KEYS,
    active_modules,
    allowed_mimes_for_pipeline,
    default_pipeline,
    dimension_options_from_definition,
    dimension_selection_from_job_snapshot,
    dimension_selection_payload,
    legacy_preprocess_to_pipeline,
    pipeline_catalog_payload,
    project_dimension_definition,
    validate_pipeline_config,
)
from .database import SessionLocal, get_db, init_database
from .correction_contract import (
    correction_contract_hash,
    freeze_contract_from_execution_snapshot,
)
from .correction_view import (
    CorrectionViewError,
    build_correction_view,
    submit_correction_nodes,
)
from .adaptive_correction import wrap_evaluation_item
from .production_dimension_contract import (
    ProductionDimensionContractError,
    resolve_published_dimension_contract,
)
from .doubao import DoubaoClient
from .migration import compare_results
from .models import (
    AgentPlanVersion,
    Asset,
    AssetVersion,
    AuditEvent,
    ContentIngressEvent,
    ContentRecord,
    AutomationOptimizationRun,
    AutomationPolicy,
    BaselineRegressionItem,
    BaselineRegressionRun,
    BaselineCorrectionRun,
    BaselineSet,
    BaselineSetItem,
    CategoryEvaluationV3Config,
    CategoryEvaluationV3Revision,
    CircuitBreaker,
    DimensionRoutePolicy,
    DimensionSchema,
    EvaluationCategoryProfile,
    CATEGORY_PROFILE_DEFAULTS,
    EvaluationControl,
    EvaluationPackage,
    EvaluationJob,
    EvaluationProductionRun,
    EvaluationResult,
    HumanReview,
    MaterialPackage,
    MaterialPackageItem,
    MigrationItem,
    MigrationRun,
    ModelConfig,
    ModelRegistryEntry,
    ModelNodeBinding,
    ModelBenchmarkExperiment,
    ModelBenchmarkVariant,
    LoopAttempt,
    LoopRun,
    OptimizerConfig,
    PromptOptimizationRun,
    PromptMetricSnapshot,
    PromptRegressionItem,
    PromptRegressionRun,
    PromptVersion,
    QueueSchedulerState,
    SampleSet,
    SampleSetItem,
    SampleTruthRevision,
    SamplingPolicy,
    SessionToken,
    StrategyBundle,
    ReviewPanel,
    ReviewWorkflowPolicy,
    OptimizationCaseQueue,
    ProductionFeedbackEvent,
    LabelOutboxEvent,
    LabelRelease,
    PublishedLabel,
    ConsumerSyncCheckpoint,
    ProjectionContract,
    ProjectionManifest,
    ProjectionReconciliation,
    FieldDemandContract,
    ShadowProjectionRun,
    ShadowProjectionTarget,
    SourceIdentityVerification,
    TagDemandContract,
    UpstreamReadRun,
    UpstreamSourceContract,
    User,
)
from .audit import append_audit_event, canonical_json
from .automation_api import build_automation_router
from .field_demand_contracts import (
    FieldDemandContractError,
    asset_version_payload,
    create_field_demand_contract,
    field_demand_contract_payload,
    record_asset_version,
)
from .nas_storage import (
    NasStorageError,
    inspect_nas_file,
    nas_relative_path,
    normalize_nas_uri,
    resolve_asset_path,
    resolve_nas_uri,
    sha256_file_cached,
)
from .readonly_sources import (
    ReadOnlySourceError,
    create_upstream_source_contract,
    source_contract_payload,
    source_run_payload,
)
from .semantic_tag_contracts import (
    PLATFORM_SEMANTIC_CONTRACT_KEY,
    SemanticTagContractError,
    canonical_contract_hash,
    validate_tag_demand_contract,
)
from .semantic_tag_quality import (
    build_run_semantic_quality,
    freeze_semantic_truth_snapshot,
)
from .projection_contracts import (
    LocalProjectionAdapter,
    ProjectionContractError,
    build_projection_manifest,
    contract_payload as projection_contract_payload,
    create_contract_version,
    manifest_payload as projection_manifest_payload,
    persist_reconciliation,
    reconciliation_payload as projection_reconciliation_payload,
)
from .shadow_projection import (
    ShadowProjectionError,
    create_shadow_projection_target,
    enqueue_shadow_projection_run,
    resolve_configured_shadow_projection_adapter,
    retry_shadow_projection_run,
    rollback_shadow_projection_run,
    shadow_projection_run_payload,
    shadow_projection_target_payload,
)
from .quality_assets import build_quality_asset_export
from .baseline_regression import (
    BASELINE_OPTIMIZATION_CASE_PURPOSE,
    LEVELS as BASELINE_LEVELS,
    TERMINAL_RUN_STATUSES as BASELINE_TERMINAL_STATUSES,
    baseline_set_fingerprint,
    build_baseline_field_metrics,
    canonical_json as baseline_canonical_json,
    compute_level_metrics,
    correction_input_snapshot,
    execute_correction_run,
    fail_correction_run,
    fail_baseline_item,
    filename_level_suggestion,
    latest_locked_golden_levels,
    run_comparison,
)
from .baseline_rule_diagnostics import diagnostics_from_run
from .baseline_correction_orchestration import (
    CorrectionOrchestrationError,
    advance_correction_run,
    configured_correction_generator,
    generate_correction_candidate,
    prepare_correction_generation,
    refresh_correction_run,
)
from .automation_case_intake import on_final_review_completed
from .category_evaluation_v3_revisions import (
    CategoryEvaluationV3RevisionError,
    activate_candidate_revision,
    revision_bundle,
)
from .category_evaluation_contract import (
    CategoryEvaluationPromptBindingError,
    bind_category_evaluation_prompt_versions,
    validate_category_evaluation_prompt_bindings,
)
from .inspiration_auto_correction import (
    AutoCorrectionPolicy,
    apply_auto_correction_to_run,
    build_drift_report as build_inspiration_drift_report,
)
from .benchmarking import (
    MODEL_KEYS,
    DeterministicBenchmarkAdapter,
    OpenAICompatibleBenchmarkAdapter,
    run_benchmark_experiment,
    snapshot_hash as benchmark_snapshot_hash,
    token_cost_micros,
)
from .loop_engine import (
    LoopContractError,
    ROUND_KIND,
    advance_loop_attempt,
    assert_safe_normalized_payload,
    canonical_json,
    enqueue_loop_evaluation_job,
    normalize_targeted_model_result,
    request_fingerprint,
    validate_result_scope,
    validate_submission_scope,
)
from .historical_corrections import preview_historical_workbooks
from .p0e_safe_import import ImportPreflightError
from .queue_scheduler import (
    QUEUE_CLASSES,
    DeterministicQueueScheduler,
    QueuePolicy,
)
from .security import (
    MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    SecretStorageError,
    create_session_token,
    hash_session_token,
    protect_secret,
    verify_password,
    hash_password,
)
from .authz import ROLE_LABELS, ROLE_PERMISSIONS, effective_role, has_permission, require_permission
from .optimizer import run_prompt_optimization, stage_audit_payload
from .optimization_automation import (
    assert_bundle_pair_category_contract,
    automation_lifecycle_status,
    automation_runtime_status,
    automation_budget_status,
    category_bundle_contract_errors,
    configured_optimization_adapter,
    consume_optimization_queue_once,
    automation_worker_snapshot,
)
from .p0e_canary_api import build_canary_router
from .category_evaluation_preview_api import (
    build_category_evaluation_preview_router,
)
from .category_evaluation_v3_config_api import (
    build_category_evaluation_v3_config_router,
)
from .node_correction_api import build_node_correction_router
from .script_registry_api import build_script_registry_router
from .workflow_registry_api import build_workflow_registry_router
from .workflow_runtime_api import build_workflow_runtime_router
from .worker_v3_authoritative import (
    V3AuthoritativeError,
    v3_authoritative_category,
)
from .evaluation_packages import (
    build_evaluation_package_router,
    publish_evaluation_package,
)
from .evaluation_production import build_evaluation_production_router
from .seed import seed_defaults
from .schema_adapter import (
    repair_combined_aesthetic_results,
    rescore_stored_results,
    validate_production_correction,
)
from .regression import (
    SAMPLE_ROLES,
    complete_paired_regression_item,
    dimension_contract_for_result,
    fail_regression_item,
    paired_gate_policy,
    refresh_paired_regression_run,
    reviewed_truth_snapshot,
    latest_review_for_result,
    truth_from_result,
)
from .evaluation_credentials import (
    default_evaluation_model,
    job_has_required_credentials,
    job_primary_model,
    model_has_credentials,
)
from .prompt_metrics import (
    calculate_prompt_metrics,
    final_review as final_human_review,
    frozen_task_set_hash,
)
from .production_feedback import (
    FeedbackConflict,
    ingest_production_feedback,
)
from .label_governance import (
    LabelIntegrationConflict,
    approve_semantic_facts,
    create_release,
    ingest_content_event,
    publish_release,
    release_payload,
    route_content_event_to_incremental_package,
    rollback_release,
)
from .label_export import build_export
from .review_panel import (
    KEY_FIELD_PATHS,
    ReviewPanelRevisionConflict,
    claim_review_panel_revision,
    resolve_panel_consensus,
    review_truth,
)
from .review_sampling import build_review_sampling
from .risk_review import RISK_REVIEW_VERSION
from .scoring import (
    DimensionScoringContractError,
    ENGINE_VERSION,
    calculate_corrected_score,
    dimension_schema_from_strategy_snapshot,
    validate_dimension_scoring_contract,
)
from .dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
)
from .v3_review_dimensions import (
    calculate_v3_review_corrected_score,
    v3_review_dimension_schema_payload,
)
from .strategy_bundle import (
    build_model_config_snapshot,
    build_strategy_snapshot,
    get_or_create_bundle,
    safe_strategy_snapshot_payload,
)


settings = get_settings()
logger = logging.getLogger(__name__)
COOKIE_NAME = "3d66_session"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 1000
MAX_ARCHIVE_IMAGES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 30 * 1024 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CATEGORY_KEYS = (
    "space_image",
    "pdf_text",
    "material_image",
    "inspiration_image",
    "model_3d_su",
)
ALLOWED_PDF_SUFFIXES = {".pdf"}


def _protected_api_key(
    value: SecretStr | None,
    *,
    account: str,
) -> str | None:
    if value is None:
        return None
    secret = value.get_secret_value().strip()
    if not secret:
        # The settings UI deliberately sends an empty string when the operator
        # edits non-secret fields without rotating the stored credential.
        # Treat blank input the same as an omitted value so the existing
        # protected reference is preserved.
        return None
    if len(secret) > 1000:
        raise HTTPException(status_code=422, detail="API Key 长度不能超过 1000 个字符")
    try:
        return protect_secret(secret, account=account)
    except SecretStorageError as error:
        logger.error(
            "api_key_storage_failed account=%s reason=%s system_error=%s",
            account,
            error.reason,
            error.system_error,
        )
        if error.reason == "SECURE_STORAGE_PLATFORM_UNSUPPORTED":
            detail = "API Key 安全存储失败（服务未运行在原生 Windows/macOS 安全存储环境）"
        elif error.reason == "DPAPI_INIT_FAILED":
            detail = "API Key 安全存储失败（Windows DPAPI 不可用）"
        elif error.reason == "DPAPI_SCOPE_INVALID":
            detail = "API Key 安全存储失败（Windows DPAPI 存储范围配置无效）"
        elif error.reason == "DPAPI_PROTECT_FAILED":
            suffix = (
                f"，系统错误 {error.system_error}"
                if error.system_error is not None
                else ""
            )
            detail = f"API Key 安全存储失败（Windows DPAPI 加密失败{suffix}）"
        else:
            detail = "API Key 安全存储失败"
        raise HTTPException(status_code=500, detail=detail) from None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["admin", "manager", "reviewer", "analyst", "viewer"] = "reviewer"


class UserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "manager", "reviewer", "analyst", "viewer"]
    is_active: bool = True
    password: str | None = Field(default=None, min_length=10, max_length=200)


class ModelNodeBindingRequest(BaseModel):
    model_config_id: int = Field(ge=1)
    category_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,39}$")
    enabled: bool = True


class ModelConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="doubao", min_length=1, max_length=40)
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages", "custom_json"] = "openai_chat"
    capabilities: list[Literal["text", "vision", "structured_output", "pdf", "gif"]] = Field(
        default_factory=lambda: ["text", "vision", "structured_output"], max_length=10
    )
    description: str = Field(default="", max_length=1000)
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(
        default=None,
        json_schema_extra={"maxLength": 1000},
    )
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=128, le=65536)
    timeout_seconds: int = Field(ge=10, le=600)
    max_retries: int = Field(ge=0, le=5)
    max_concurrency: int = Field(ge=1, le=10)
    structured_output: bool = True
    high_risk_review_enabled: bool = True
    thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    input_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    output_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    max_input_tokens: int = Field(default=0, ge=0, le=10_000_000)
    benchmark_enabled: bool = False


class OptimizerConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="openai", min_length=1, max_length=40)
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages", "custom_json"] = "openai_chat"
    capabilities: list[Literal["text", "vision", "structured_output", "pdf", "gif"]] = Field(
        default_factory=lambda: ["text", "structured_output"], max_length=10
    )
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(
        default=None,
        json_schema_extra={"maxLength": 1000},
    )
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=12000, ge=512, le=65536)
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    max_retries: int = Field(default=1, ge=0, le=5)
    structured_output: bool = True
    input_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    output_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    max_input_tokens: int = Field(default=0, ge=0, le=10_000_000)


class BenchmarkModelConfigCreate(ModelConfigUpdate):
    provider: str = Field(default="openai", min_length=1, max_length=40)


class ModelRegistryUpdate(BaseModel):
    role: Literal["main", "tuning", "benchmark"] = "main"
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=40)
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages", "custom_json"] = "openai_chat"
    capabilities: list[Literal["text", "vision", "structured_output", "pdf", "gif"]] = Field(
        default_factory=lambda: ["text", "vision", "structured_output"], max_length=10
    )
    description: str = Field(default="", max_length=1000)
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, json_schema_extra={"maxLength": 1000})
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=128, le=65536)
    timeout_seconds: int = Field(default=120, ge=10, le=900)
    max_retries: int = Field(default=1, ge=0, le=5)
    max_concurrency: int = Field(default=8, ge=1, le=100)
    max_requests_per_minute: int = Field(default=0, ge=0, le=1_000_000)
    max_input_tokens: int = Field(default=0, ge=0, le=10_000_000)
    input_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    output_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    monthly_budget_micros: int = Field(default=0, ge=0, le=10_000_000_000_000)
    thinking_mode: Literal["auto", "enabled", "disabled"] = "auto"
    level: str = Field(default="standard", min_length=1, max_length=40)
    structured_output: bool = True
    active: bool = True

    @model_validator(mode="after")
    def validate_capability_contract(self) -> "ModelRegistryUpdate":
        if "text" not in self.capabilities:
            raise ValueError("模型注册项必须声明 text 能力")
        if self.structured_output and "structured_output" not in self.capabilities:
            raise ValueError("启用结构化输出时必须声明 structured_output 能力")
        return self


class SamplingPolicyUpdate(BaseModel):
    sample_rate: int = Field(ge=0, le=100)
    low_confidence_threshold: float = Field(ge=0, le=1)
    medium_confidence_threshold: float = Field(ge=0, le=1)
    cold_start_required_count: int = Field(ge=0, le=100)
    high_level_required_from: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def validate_confidence_thresholds(self) -> "SamplingPolicyUpdate":
        if self.medium_confidence_threshold < self.low_confidence_threshold:
            raise ValueError("中置信度上限不能低于低置信度阈值")
        return self


class ReviewWorkflowPolicyUpdate(BaseModel):
    initial_reviewers: int = Field(ge=1, le=9)

    @model_validator(mode="after")
    def validate_odd_reviewers(self) -> "ReviewWorkflowPolicyUpdate":
        if self.initial_reviewers % 2 != 1:
            raise ValueError("初审审核员人数必须为 1 或不大于 9 的奇数")
        return self


class AutomationPolicyUpdate(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    case_threshold: int = Field(default=10, ge=1, le=1000)
    immediate_severities: list[Literal["P0", "P1", "P2", "P3"]] = Field(
        default_factory=lambda: ["P0", "P1"], min_length=1, max_length=4
    )
    daily_budget_micros: int = Field(default=0, ge=0, le=1_000_000_000)
    cooldown_seconds: int = Field(default=21600, ge=0, le=2_592_000)
    max_candidates: int = Field(default=1, ge=1, le=5)
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    base_retry_seconds: int = Field(default=60, ge=1, le=86400)

    @model_validator(mode="after")
    def validate_safety_contract(self) -> "AutomationPolicyUpdate":
        if len(self.immediate_severities) != len(
            set(self.immediate_severities)
        ):
            raise ValueError("即时触发严重度不能重复")
        if self.enabled and not self.dry_run and self.daily_budget_micros <= 0:
            raise ValueError("非 dry-run 自动优化必须配置正数日预算")
        return self


class ProductionFeedbackRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    schema_version: Literal["production-feedback-v1"]
    event_type: Literal["human_correction_finalized"]
    source_system: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_category(self) -> "ProductionFeedbackRequest":
        if not self.payload.get("category_key"):
            raise ValueError("生产反馈 payload 必须填写 category_key")
        return self


class ContentIngressRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    schema_version: Literal["content-ingress-v1", "content-ingress-v2"]
    event_type: Literal["content.created", "content.updated", "content.deleted"]
    source_system: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    payload: dict[str, Any]


class LabelReleaseRequest(BaseModel):
    release_key: str = Field(min_length=1, max_length=160)
    evaluation_id: int = Field(ge=1)
    content_key: str | None = Field(default=None, max_length=320)


class LabelRollbackRequest(BaseModel):
    rollback_key: str = Field(min_length=1, max_length=160)


class PublishedLabelExportRequest(BaseModel):
    format: Literal["xlsx", "csv", "json"] = "xlsx"
    scope: Literal["current", "history"] = "current"
    category_key: str | None = Field(default=None, pattern=CATEGORY_KEY_PATTERN.pattern)
    published_from: datetime | None = None
    published_to: datetime | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "PublishedLabelExportRequest":
        if self.published_from is None or self.published_to is None:
            return self
        start = self.published_from
        end = self.published_to
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise ValueError("发布时间起点不能晚于终点")
        return self


class ConsumerCheckpointRequest(BaseModel):
    consumer_name: str = Field(min_length=1, max_length=120)
    cursor: int = Field(ge=0)


class ProjectionContractCreateRequest(BaseModel):
    contract_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    target_role: Literal["unified_dimension", "search_labels", "quality_governance"]
    table_name: str = Field(min_length=1, max_length=120)
    environment: Literal["local", "test", "shadow"] = "local"
    adapter_key: str = Field(default="local-sqlite", min_length=1, max_length=80)
    target_key: str | None = Field(default=None, max_length=120)
    write_policy: Literal["local_only", "shadow_only"] = "local_only"
    category_key: str | None = Field(default=None, max_length=40)
    field_contract_id: int | None = Field(default=None, ge=1)
    max_batch_size: int = Field(default=500, ge=1, le=500)
    primary_key: list[str] = Field(min_length=1, max_length=4)
    field_mappings: dict[str, str] = Field(min_length=1, max_length=200)
    input_versions: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["snapshot", "incremental_outbox"] = "snapshot"
    idempotency_key_template: str = Field(min_length=1, max_length=300)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)
    owner: str = Field(min_length=1, max_length=120)
    status: Literal["draft", "active", "retired"] = "draft"


class TagDemandContractCreateRequest(BaseModel):
    contract_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_-]*$")
    definition: dict[str, Any]
    status: Literal["draft", "candidate"] = "draft"


class SourceIdentityVerificationCreateRequest(BaseModel):
    contract_key: str = Field(min_length=1, max_length=120)
    source_system: str = Field(min_length=1, max_length=120)
    key_fields: tuple[Literal["res_type", "ll_id"], ...]
    result: Literal["verified", "conflict"]
    probe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_window: str = Field(min_length=1, max_length=120)
    scoped_row_count: int = Field(ge=0)
    duplicate_key_count: int = Field(ge=0)
    res_id_conflict_count: int = Field(ge=0)


class SourceIdentityVerificationBindRequest(BaseModel):
    verification_id: int = Field(ge=1)


class FieldDemandContractCreateRequest(BaseModel):
    contract_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    category_key: str = Field(min_length=1, max_length=40)
    consumer_key: str = Field(min_length=1, max_length=120)
    owner: str = Field(min_length=1, max_length=120)
    fields: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    thresholds: dict[str, float] = Field(min_length=1, max_length=40)
    status: Literal["draft", "active", "retired"] = "draft"


class UpstreamSourceContractCreateRequest(BaseModel):
    contract_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    adapter_key: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=120)
    category_key: str = Field(min_length=1, max_length=40)
    connection_locator: str = Field(min_length=1, max_length=200)
    secret_reference: str = Field(min_length=1, max_length=200)
    field_mappings: dict[str, str] = Field(min_length=4, max_length=40)
    cursor_definition: dict[str, Any] = Field(default_factory=dict)
    page_size: int = Field(default=100, ge=1, le=500)
    read_only: bool = True
    schema_fingerprint: str = Field(min_length=64, max_length=64)
    owner: str = Field(min_length=1, max_length=120)
    status: Literal["draft", "active", "retired"] = "draft"


class UpstreamPollRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: dict[str, Any] = Field(default_factory=dict)


class ShadowProjectionTargetCreateRequest(BaseModel):
    target_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    adapter_key: str = Field(min_length=1, max_length=80)
    connection_locator: str = Field(min_length=1, max_length=200)
    secret_reference: str = Field(min_length=1, max_length=200)
    schema_name: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    table_name: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    environment: Literal["shadow"] = "shadow"
    shadow_only: Literal[True] = True
    owner: str = Field(min_length=1, max_length=120)
    schema_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    status: Literal["draft", "active", "retired"] = "draft"


class ShadowProjectionRunCreateRequest(BaseModel):
    projection_contract_id: int = Field(ge=1)
    field_contract_id: int = Field(ge=1)
    target_id: int = Field(ge=1)
    max_rows: int = Field(default=500, ge=1, le=500)


class BenchmarkVariantRequest(BaseModel):
    model_key: Literal["sol", "terra", "luna"]
    model_config_id: int | None = Field(default=None, ge=1)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    input_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    output_micros_per_million_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    human_review_cost_micros: int = Field(ge=0, le=100_000_000)


class BenchmarkCreateRequest(BaseModel):
    experiment_key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    execution_mode: Literal["disabled", "test", "real"] = "test"
    cohort_asset_ids: list[int] = Field(min_length=1, max_length=5000)
    strategy_bundle_id: int = Field(ge=1)
    variants: list[BenchmarkVariantRequest] = Field(
        min_length=3, max_length=3
    )
    min_quality_accuracy: float = Field(default=0.9, ge=0, le=1)
    max_p0_p1_errors: int = Field(default=0, ge=0, le=1000)
    min_retry_stability: float = Field(default=0.95, ge=0, le=1)
    low_confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    max_round_cost_micros: int = Field(default=0, ge=0, le=1_000_000_000)
    quality_gate_approved: bool = False

    @model_validator(mode="after")
    def validate_frozen_cohort(self) -> "BenchmarkCreateRequest":
        if len(self.cohort_asset_ids) != len(set(self.cohort_asset_ids)):
            raise ValueError("横评 cohort 不能包含重复素材")
        if {item.model_key for item in self.variants} != set(MODEL_KEYS):
            raise ValueError("横评必须同时冻结 Sol、Terra、Luna")
        if self.execution_mode == "real":
            if self.max_round_cost_micros <= 0:
                raise ValueError("真实横评必须配置正数单轮成本上限")
            if not self.quality_gate_approved:
                raise ValueError("真实横评必须先通过质量门")
            if any(item.model_config_id is None for item in self.variants):
                raise ValueError("真实横评的每个变体必须绑定服务端模型配置")
        elif any(
            item.model_config_id is None and (not item.provider or not item.model_id)
            for item in self.variants
        ):
            raise ValueError("测试横评变体必须提供测试标识或模型配置")
        return self


class BenchmarkRunRequest(BaseModel):
    test_observations: dict[
        Literal["sol", "terra", "luna"], list[dict[str, Any]]
    ]

    @model_validator(mode="after")
    def validate_test_models(self) -> "BenchmarkRunRequest":
        if set(self.test_observations) != set(MODEL_KEYS):
            raise ValueError("测试观测必须覆盖 Sol、Terra、Luna")
        return self


class MaterialPackageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    category_key: str = Field(default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$")

    @model_validator(mode="after")
    def validate_unique_assets(self) -> "MaterialPackageCreateRequest":
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("素材包不能包含重复素材")
        return self


class NasAssetImportRequest(BaseModel):
    source_uri: str = Field(min_length=1, max_length=1000)
    package_name: str | None = Field(default=None, max_length=200)
    category_key: str = Field(default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$")
    max_files: int = Field(default=10_000, ge=1, le=10_000)


class AssetCategoryUpdateRequest(BaseModel):
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")


class AssetBulkDeleteRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_assets(self) -> "AssetBulkDeleteRequest":
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("素材列表不能包含重复素材")
        return self


class EnqueueRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    prompt_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    queue_class: Literal[
        "validation",
        "interactive",
        "production_batch",
        "canary",
    ] | None = None
    manual_recheck: bool = False
    category_key: str = Field(default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$")

    @model_validator(mode="after")
    def validate_prompt_mode(self) -> "EnqueueRequest":
        if self.prompt_id and (self.prompt_a_id or self.prompt_b_id):
            raise ValueError("单提示词模式不能同时选择 A/B 提示词")
        if (self.prompt_a_id is None) != (self.prompt_b_id is None):
            raise ValueError("A/B 提示词必须同时选择")
        if self.manual_recheck and len(self.asset_ids) != 1:
            raise ValueError("人工单图复判只能包含一张图片")
        if self.manual_recheck and self.queue_class not in (None, "interactive"):
            raise ValueError("人工单图复判固定进入 interactive")
        return self


class EvaluationCategoryProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    status: Literal["draft", "active", "retired"] = "active"
    allowed_mime_types: list[str] = Field(min_length=1, max_length=8)
    preprocess_config: dict[str, Any] = Field(default_factory=dict)
    pipeline_config: dict[str, Any] | None = None
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    model_config_id: int | None = Field(default=None, ge=1)
    rubric_version: str = Field(default="rubric-v2.1", min_length=1, max_length=40)
    dimension_schema_key: str | None = Field(default=None, max_length=80)
    dimension_schema_version: str | None = Field(default=None, max_length=64)
    automation_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dimension_identity(self) -> "EvaluationCategoryProfileUpdate":
        if (self.dimension_schema_key is None) != (
            self.dimension_schema_version is None
        ):
            raise ValueError("维度 Schema 业务键与版本必须同时填写")
        return self


class EvaluationCategoryProfileCreate(EvaluationCategoryProfileUpdate):
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    status: Literal["draft"] = "draft"


class DimensionSchemaWriteRequest(BaseModel):
    schema_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")
    version: str = Field(min_length=1, max_length=64)
    schema_type: Literal["core", "family_pack", "extension"] = "family_pack"
    family_key: Literal["space", "product", "graphic", "intent", "common"]
    display_name: str = Field(min_length=1, max_length=160)
    definition: dict[str, Any]
    parent_schema_id: int | None = Field(default=None, ge=1)
    core_schema_id: int | None = Field(default=None, ge=1)


class DimensionSchemaUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    definition: dict[str, Any]
    parent_schema_id: int | None = Field(default=None, ge=1)
    core_schema_id: int | None = Field(default=None, ge=1)


class PromptCreateRequest(BaseModel):
    category_key: str = Field(
        default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$"
    )
    pipeline_scope: Literal["full_pipeline", "baseline_regression", "shared"] = "shared"
    stage: str = Field(pattern="^[AB]$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    system_prompt: str = Field(min_length=20)
    user_prompt: str = Field(min_length=5)
    rubric_version: str = Field(min_length=1, max_length=40)
    change_note: str = ""
    source: str = "manual"


class PromptUpdateRequest(BaseModel):
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    pipeline_scope: Literal["full_pipeline", "baseline_regression", "shared"]
    stage: Literal["A", "B"]
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    system_prompt: str = Field(min_length=20)
    user_prompt: str = Field(min_length=5)
    rubric_version: str = Field(min_length=1, max_length=40)
    change_note: str = ""


class PromptCloneRequest(PromptUpdateRequest):
    pass


class PromptPublishRequest(BaseModel):
    pipeline_scope: Literal["full_pipeline", "baseline_regression", "shared"] | None = None


class PromptAiReviseRequest(BaseModel):
    prompt_id: int
    instruction: str = Field(min_length=4, max_length=2000)


class ReviewCorrection(BaseModel):
    target_type: str = Field(pattern="^(dimension|key_field)$")
    field_key: str = Field(min_length=1, max_length=80)
    model_value: Any = None
    human_value: Any = None
    reason_codes: list[str] = Field(min_length=1, max_length=8)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_changed_value(self) -> "ReviewCorrection":
        if self.target_type == "dimension":
            if not isinstance(self.human_value, int) or not 1 <= self.human_value <= 5:
                raise ValueError("维度纠错必须填写 1 至 5 的人工分数")
            if self.human_value == self.model_value:
                raise ValueError("人工维度分数必须与模型分数不同")
        elif self.field_key not in KEY_FIELD_PATHS:
            raise ValueError("关键字段不在允许纠偏的字段清单中")
        elif self.human_value == self.model_value:
            raise ValueError("人工关键字段值必须与模型值不同")
        else:
            validate_production_correction(self.field_key, self.human_value)
        return self


class ReviewRequest(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|corrected|rejected)$")
    expected_stage: Literal["initial", "secondary", "arbitration"]
    expected_review_revision: int = Field(ge=0)
    corrected_level: str | None = Field(default=None, pattern="^L[1-5]$")
    note: str = Field(default="", max_length=2000)
    corrections: list[ReviewCorrection] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_correction(self) -> "ReviewRequest":
        if self.decision == "corrected":
            if self.corrected_level is not None:
                raise ValueError("最终等级由维度纠正自动计算，不能手工指定")
            if not self.corrections:
                raise ValueError("修改结果时必须填写至少一个维度纠正")
        elif self.corrected_level is not None or self.corrections:
            raise ValueError("只有修改结果时才能填写修正等级或维度纠错")
        return self


class ReviewPanelVoteRequest(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|corrected|rejected)$")
    expected_panel_revision: int = Field(ge=0)
    note: str = Field(default="", max_length=2000)
    corrections: list[ReviewCorrection] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_vote(self) -> "ReviewPanelVoteRequest":
        if self.decision == "corrected" and not self.corrections:
            raise ValueError("修改结果时必须填写至少一个纠偏字段")
        if self.decision != "corrected" and self.corrections:
            raise ValueError("只有修改结果时才能填写纠偏字段")
        return self


class ReviewPanelAdjudicationRequest(BaseModel):
    lead_reviewer_name: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|corrected|rejected)$")
    expected_panel_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)
    corrections: list[ReviewCorrection] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_adjudication(self) -> "ReviewPanelAdjudicationRequest":
        if self.decision == "corrected" and not self.corrections:
            raise ValueError("主审修改结果时必须填写至少一个纠偏字段")
        if self.decision != "corrected" and self.corrections:
            raise ValueError("只有修改结果时才能填写纠偏字段")
        return self


class PromptMetricSnapshotRequest(BaseModel):
    task_set_key: str = Field(min_length=1, max_length=160)
    batch_key: str | None = Field(default=None, min_length=1, max_length=120)
    evaluation_ids: list[int] = Field(
        default_factory=list, max_length=5000
    )

    @model_validator(mode="after")
    def validate_task_set(self) -> "PromptMetricSnapshotRequest":
        if bool(self.batch_key) == bool(self.evaluation_ids):
            raise ValueError("必须且只能通过批次键或评测结果 ID 冻结任务集")
        if len(self.evaluation_ids) != len(set(self.evaluation_ids)):
            raise ValueError("冻结任务集不能包含重复评测结果")
        return self


class ReviewPanelOpenRequest(BaseModel):
    required_reviewers: int | None = Field(default=None, ge=1, le=9)

    @model_validator(mode="after")
    def validate_odd_reviewers(self) -> "ReviewPanelOpenRequest":
        if (
            self.required_reviewers is not None
            and self.required_reviewers % 2 != 1
        ):
            raise ValueError("初审组审核员人数必须为 1 或不大于 9 的奇数")
        return self


class ReviewPanelReopenRequest(BaseModel):
    expected_review_revision: int = Field(ge=0)


class PromptOptimizationCreateRequest(BaseModel):
    prompt_id: int = Field(ge=1)
    sample_set_id: int = Field(ge=1)


class SampleSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    kind: str = Field(default="test", pattern="^(golden|test)$")
    category_key: str = Field(default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$")
    # 已锁定黄金集不可直接改，_assert_sample_set_is_mutable 让运营"复制形成新草稿
    # 版本后再调整"——传入源样本集 id 即走这条路径，连同条目与人工纠偏真值一起深拷贝。
    source_sample_set_id: int | None = Field(default=None, ge=1)


class SampleSetAddItemsRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    expected_level: str | None = Field(default=None, pattern="^L[1-5]$")


class SampleSetItemUpdateRequest(BaseModel):
    expected_level: str | None = Field(default=None, pattern="^L[1-5]$")
    note: str = Field(default="", max_length=2000)
    truth: dict[str, Any] | None = None
    revision_reason: str = Field(default="", max_length=2000)


class SampleSetStatusRequest(BaseModel):
    status: str = Field(pattern="^(draft|locked)$")


class SampleSetExportRequest(BaseModel):
    format: Literal["csv", "json", "manifest"]


class RegressionCreateRequest(BaseModel):
    sample_set_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    threshold: float = Field(default=0.9, ge=0.5, le=1.0)


class BaselineSetItemCreateRequest(BaseModel):
    asset_id: int = Field(ge=1)
    expected_level: Literal["L1", "L2", "L3", "L4", "L5"] | None = None
    source_package_id: int | None = Field(default=None, ge=1)


class BaselineSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    default_expected_level: Literal["L1", "L2", "L3", "L4", "L5"]
    category_key: str = Field(default="space_image", pattern=r"^[a-z][a-z0-9_]{2,39}$")
    source_package_id: int | None = Field(default=None, ge=1)
    items: list[BaselineSetItemCreateRequest] = Field(
        default_factory=list, max_length=10_000
    )
    expected_level_overrides: dict[
        int, Literal["L1", "L2", "L3", "L4", "L5"]
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_assets(self) -> "BaselineSetCreateRequest":
        if self.source_package_id is None and not self.items:
            raise ValueError("请选择素材包或至少一张素材")
        if self.source_package_id is not None and self.items:
            raise ValueError("整包创建与逐张选择不能同时提交")
        if self.source_package_id is None and self.expected_level_overrides:
            raise ValueError("逐张等级覆盖仅用于整包创建")
        asset_ids = [item.asset_id for item in self.items]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("基准集不能包含重复素材")
        return self


class BalancedSampleRebuildRequest(BaseModel):
    """Parameters of a rebuilt balanced sample.

    All three decide the frozen set's name and fingerprint, so replaying the
    same parameters is idempotent and a different seed necessarily produces a
    new set rather than rewriting one that runs already reference.
    """

    per_level: int = Field(default=20, ge=1, le=500)
    strategy: Literal["stable_hash", "newest", "oldest"] = "stable_hash"
    seed: int = Field(default=1, ge=1, le=1_000_000)


class BaselineRunCategoryContext(BaseModel):
    source: Literal["baseline_set"]
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")


class BaselineRunCreateRequest(BaseModel):
    prompt_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    dimension_schema_id: int | None = Field(default=None, ge=1)
    dimension_mode: Literal["category_default", "all", "none"] = "category_default"
    execution_mode: Literal["freeform", "structured"] = "freeform"
    baseline_item_ids: list[int] | None = Field(
        default=None, min_length=1, max_length=1000
    )
    candidate_revision_id: int | None = Field(default=None, ge=1)
    category_context: BaselineRunCategoryContext | None = None

    @model_validator(mode="after")
    def validate_prompt_pair(self) -> "BaselineRunCreateRequest":
        if self.prompt_id is not None and (
            self.prompt_a_id is not None or self.prompt_b_id is not None
        ):
            raise ValueError("单提示词模式不能同时指定 A 与 B 提示词版本")
        if (self.prompt_a_id is None) != (self.prompt_b_id is None):
            raise ValueError("手动选择提示词时必须同时指定 A 与 B 版本")
        if self.baseline_item_ids is not None and len(self.baseline_item_ids) != len(
            set(self.baseline_item_ids)
        ):
            raise ValueError("基准回归子集不能包含重复 item")
        return self


class BaselineOptimizationQueueRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "BaselineOptimizationQueueRequest":
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("偏差条目不能重复")
        return self


class BaselineCorrectionCreateRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "BaselineCorrectionCreateRequest":
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("纠偏样本不能重复")
        return self


class BaselineCorrectionNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str = Field(min_length=1, max_length=160)
    human_value: Any
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=30)


class BaselineCorrectionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_hash: str = Field(min_length=64, max_length=64)
    nodes: list[BaselineCorrectionNodeRequest] = Field(
        min_length=1, max_length=100
    )
    review_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def validate_unique_nodes(self) -> "BaselineCorrectionSubmitRequest":
        node_keys = [node.node_key for node in self.nodes]
        if len(node_keys) != len(set(node_keys)):
            raise ValueError("纠偏节点不能重复")
        return self


class BaselineCorrectionDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)


class InspirationAutoCorrectionRequest(BaseModel):
    confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    minimum_support: int = Field(default=30, ge=1, le=10_000)
    coverage_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    calibration_fraction: float = Field(default=0.70, ge=0.1, le=0.9)
    maximum_level_shift: int = Field(default=1, ge=1, le=4)

    def to_policy(self) -> AutoCorrectionPolicy:
        return AutoCorrectionPolicy(**self.model_dump())


class PairedRegressionSampleRequest(BaseModel):
    sample_item_id: int = Field(ge=1)
    role: str = Field(pattern="^(target_error|stable_control|blind_holdout)$")


class PromptOptimizationMaterializeRequest(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    baseline_strategy_bundle_id: int = Field(ge=1)
    samples: list[PairedRegressionSampleRequest] = Field(
        min_length=3, max_length=1000
    )
    metric_rules_version: str = Field(min_length=1, max_length=80)
    aesthetic_accuracy_max_drop: float = Field(default=0, ge=0, le=1)
    whole_image_accuracy_max_drop: float = Field(default=0, ge=0, le=1)
    level_consistency_max_drop: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_roles(self) -> "PromptOptimizationMaterializeRequest":
        ids = [sample.sample_item_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("同一冻结样本不能重复")
        if {sample.role for sample in self.samples} != set(SAMPLE_ROLES):
            raise ValueError(
                "候选验证必须同时包含 target_error、stable_control、blind_holdout"
            )
        return self


class PairedRegressionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sample_set_id: int = Field(ge=1)
    baseline_strategy_bundle_id: int = Field(ge=1)
    candidate_strategy_bundle_id: int = Field(ge=1)
    trigger_prompt_id: int | None = Field(default=None, ge=1)
    samples: list[PairedRegressionSampleRequest] = Field(
        min_length=3, max_length=1000
    )
    metric_rules_version: str = Field(min_length=1, max_length=80)
    aesthetic_accuracy_max_drop: float = Field(ge=0, le=1)
    whole_image_accuracy_max_drop: float = Field(ge=0, le=1)
    level_consistency_max_drop: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_paired_contract(self) -> "PairedRegressionCreateRequest":
        if self.baseline_strategy_bundle_id == self.candidate_strategy_bundle_id:
            raise ValueError("基线与候选 StrategyBundle 必须不同")
        item_ids = [sample.sample_item_id for sample in self.samples]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("同一冻结样本不能重复")
        roles = {sample.role for sample in self.samples}
        if roles != set(SAMPLE_ROLES):
            raise ValueError(
                "配对回归必须同时包含 target_error、stable_control、blind_holdout"
            )
        return self


class PairedRegressionResultsRequest(BaseModel):
    baseline_evaluation_id: int = Field(ge=1)
    candidate_evaluation_id: int = Field(ge=1)


class PairedRegressionApprovalRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    reviewer_name: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=2000)


class MigrationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    baseline_model_id: str = Field(min_length=1, max_length=200)
    sample_size: int = Field(default=200, ge=1, le=500)
    sample_set_id: int | None = Field(default=None, ge=1)


class MigrationReviewRequest(BaseModel):
    verdict: str = Field(pattern="^(candidate_better|same|baseline_better)$")
    reviewer_name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)


def _missing_loop_value() -> object:
    return _LOOP_MISSING


def _loop_create_schema(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)
    schema["required"] = [
        "asset_id",
        "strategy_bundle_id",
        "idempotency_key",
    ]


def _loop_result_schema(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)
    schema["required"] = [
        "idempotency_key",
        "strategy_bundle_id",
        "kind",
        "normalized_result",
    ]


_LOOP_MISSING = object()
LoopPositiveInteger = Annotated[
    SkipValidation[int],
    WithJsonSchema({"type": "integer", "minimum": 1.0}),
]
LoopIdempotencyKey = Annotated[
    SkipValidation[str],
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 8,
            "maxLength": 160,
        }
    ),
]
LoopEvidenceObject = Annotated[
    SkipValidation[dict[str, Any]],
    WithJsonSchema(
        {
            "type": "object",
            "additionalProperties": True,
        }
    ),
]
LoopNullableModelId = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "maxLength": 200},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullablePromptVersion = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "maxLength": 40},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullableSource = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["interactive", "validation"],
                },
                {"type": "null"},
            ]
        }
    ),
]
LoopResultKind = Annotated[
    SkipValidation[str],
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["base", "targeted_recheck", "arbitration"],
        }
    ),
]
LoopTargetDimensions = Annotated[
    SkipValidation[list[str]],
    WithJsonSchema(
        {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 100,
        }
    ),
]
LoopConflicts = Annotated[
    SkipValidation[list[dict[str, Any]]],
    WithJsonSchema(
        {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
            "maxItems": 100,
        }
    ),
]
LoopTechnicalAttempt = Annotated[
    SkipValidation[int],
    WithJsonSchema({"type": "integer", "const": 0}),
]
LoopNullableCost = Annotated[
    SkipValidation[float | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0.0},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullableLatency = Annotated[
    SkipValidation[int | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": 0.0},
                {"type": "null"},
            ]
        }
    ),
]


class LoopCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra=_loop_create_schema,
    )

    asset_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    strategy_bundle_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    idempotency_key: LoopIdempotencyKey = Field(
        default_factory=_missing_loop_value
    )
    input_evidence: LoopEvidenceObject = Field(default_factory=dict)
    model_id: LoopNullableModelId = None
    prompt_a_version: LoopNullablePromptVersion = None
    prompt_b_version: LoopNullablePromptVersion = None
    source: LoopNullableSource = None


class LoopResultRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra=_loop_result_schema,
    )

    idempotency_key: LoopIdempotencyKey = Field(
        default_factory=_missing_loop_value
    )
    strategy_bundle_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    kind: LoopResultKind = Field(default_factory=_missing_loop_value)
    target_dimensions: LoopTargetDimensions = Field(default_factory=list)
    normalized_result: LoopEvidenceObject = Field(
        default_factory=_missing_loop_value
    )
    conflicts: LoopConflicts = Field(default_factory=list)
    technical_attempt: LoopTechnicalAttempt = 0
    cost: LoopNullableCost = None
    latency_ms: LoopNullableLatency = None
    model_id: LoopNullableModelId = None
    prompt_a_version: LoopNullablePromptVersion = None
    prompt_b_version: LoopNullablePromptVersion = None


class BreakerOpenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=80)
    cooldown_seconds: int = Field(default=300, ge=1, le=86400)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    db = SessionLocal()
    try:
        seed_defaults(db)
        repair_combined_aesthetic_results(db)
        rescore_stored_results(db)
    finally:
        db.close()
    yield


app = FastAPI(title="3d66 标签系统", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def current_user(
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    if not session_cookie:
        raise HTTPException(status_code=401, detail="请先登录")
    token_hash = hash_session_token(session_cookie)
    session = db.scalar(select(SessionToken).where(SessionToken.token_hash == token_hash))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="登录已过期")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


def _user_payload(user: User) -> dict[str, Any]:
    role = effective_role(user)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "role": role,
        "role_label": ROLE_LABELS[role],
        "permissions": sorted(ROLE_PERMISSIONS[role]),
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def _permission_user(permission: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"缺少权限：{permission}")
        return user

    return dependency


def production_feedback_sender(
    authorization: str | None = Header(default=None),
) -> str:
    configured = settings.production_feedback_token
    if configured is None:
        raise HTTPException(status_code=503, detail="生产回流接收未配置")
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix) :]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(
            status_code=401,
            detail="生产回流认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "production-feedback-sender"


def content_ingress_sender(
    authorization: str | None = Header(default=None),
) -> str:
    configured = settings.content_ingress_token
    if configured is None:
        raise HTTPException(status_code=503, detail="标签平台接入未配置")
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="标签平台接入认证失败", headers={"WWW-Authenticate": "Bearer"})
    return "content-ingress"


def label_consumer_sender(
    authorization: str | None = Header(default=None),
) -> str:
    configured = settings.label_consumer_token
    if configured is None:
        raise HTTPException(status_code=503, detail="标签平台消费接口未配置")
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="标签平台消费认证失败", headers={"WWW-Authenticate": "Bearer"})
    return "label-consumer"


app.include_router(build_canary_router(current_user))
app.include_router(build_category_evaluation_preview_router(current_user))
app.include_router(build_category_evaluation_v3_config_router(current_user, admin_user))
app.include_router(build_node_correction_router(_permission_user("reviews:write")))
app.include_router(build_script_registry_router(current_user))
app.include_router(build_workflow_registry_router(current_user))
app.include_router(build_workflow_runtime_router(current_user))
app.include_router(build_automation_router(current_user, admin_user))
app.include_router(
    build_evaluation_package_router(
        require_permission("releases:read"),
        require_permission("releases:write"),
        admin_user,
    )
)


def _asset_payload(
    asset: Asset,
    *,
    display_name: str | None = None,
) -> dict[str, Any]:
    name = display_name or asset.original_name
    suggestion = filename_level_suggestion(name)
    return {
        "id": asset.id,
        "name": name,
        "mime_type": asset.mime_type,
        "storage_backend": getattr(asset, "storage_backend", "local") or "local",
        "source_uri": getattr(asset, "source_uri", None),
        "category_key": asset.category_key,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "created_at": asset.created_at,
        "image_url": f"/api/assets/{asset.id}/file",
        "suggested_expected_level": suggestion["suggested_level"],
        "level_suggestion": suggestion,
    }


def _evaluation_asset_payload(
    result: EvaluationResult, sampling: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **_asset_payload(result.asset),
        "evaluation": _result_payload(result),
        "sampling": sampling or {},
    }


def _review_sampling_decisions(db: Session) -> dict[int, dict[str, Any]]:
    policy = db.get(SamplingPolicy, 1) or SamplingPolicy(id=1)
    sample_rate = policy.sample_rate if policy.sample_rate is not None else 10
    low_threshold = policy.low_confidence_threshold if policy.low_confidence_threshold is not None else 0.7
    medium_threshold = policy.medium_confidence_threshold if policy.medium_confidence_threshold is not None else 0.9
    cold_start_count = policy.cold_start_required_count if policy.cold_start_required_count is not None else 5
    high_level_from = policy.high_level_required_from if policy.high_level_required_from is not None else 4
    policy_revision = policy.revision if policy.revision is not None else 1
    all_results = db.scalars(
        select(EvaluationResult).order_by(
            EvaluationResult.created_at.asc(), EvaluationResult.id.asc()
        )
    ).all()
    golden_asset_ids = set(
        db.scalars(
            select(SampleSetItem.asset_id)
            .join(SampleSet, SampleSet.id == SampleSetItem.sample_set_id)
            .where(SampleSet.kind == "golden", SampleSet.status == "locked")
        ).all()
    )
    previous_level_by_asset: dict[int, str | None] = {}
    combination_counts: dict[tuple[str, str, str | None], int] = {}
    decisions: dict[int, dict[str, Any]] = {}
    for result in all_results:
        combination = (
            result.model_id,
            result.prompt_a_version,
            result.prompt_b_version,
        )
        combination_counts[combination] = combination_counts.get(combination, 0) + 1
        decisions[result.id] = build_review_sampling(
            result,
            is_golden=result.asset_id in golden_asset_ids,
            previous_level=previous_level_by_asset.get(result.asset_id),
            combination_index=combination_counts[combination],
            sample_rate=sample_rate,
            low_confidence_threshold=low_threshold,
            medium_confidence_threshold=medium_threshold,
            cold_start_required_count=cold_start_count,
            high_level_required_from=high_level_from,
            policy_version=f"smart-sampling-v1.1/policy-{policy_revision}",
        )
        previous_level_by_asset[result.asset_id] = result.level
    return decisions


def _result_payload(result: EvaluationResult | None) -> dict[str, Any] | None:
    if not result:
        return None
    ordered_reviews = sorted(
        result.reviews,
        key=lambda review: (_aware(review.created_at), review.id or 0),
    )
    panel = result.review_panel
    current_round_reviews = (
        [
            review
            for review in ordered_reviews
            if review.panel_id == panel.id
            and review.review_round == panel.review_round
        ]
        if panel is not None
        else []
    )
    visible_reviews = (
        ordered_reviews
        if panel is None or panel.status == "completed"
        else [
            review
            for review in ordered_reviews
            if not (
                review.panel_id == panel.id
                and review.review_round == panel.review_round
            )
        ]
    )
    # While a reopened round is collecting blind votes, retain the last
    # completed correction as the editable seed without revealing current
    # round votes. Once complete, the current round is authoritative.
    review_for_display = (
        current_round_reviews[-1]
        if current_round_reviews and panel is not None and panel.status == "completed"
        else visible_reviews[-1] if visible_reviews else None
    )
    completed_review = (
        review_for_display if result.review_stage == "completed" else None
    )
    display_review = completed_review or review_for_display
    final_level = (
        display_review.corrected_level or result.level
        if display_review and display_review.decision == "corrected"
        else result.level
    )
    final_score = (
        display_review.corrected_score
        if display_review
        and display_review.decision == "corrected"
        and display_review.corrected_score is not None
        else result.score
    )
    single_prompt = result.job.prompt_b_id is None and result.prompt_b_version is None
    review_history = [
        {
            "id": review.id,
            "review_round": review.review_round,
            "stage": review.stage,
            "reviewer_name": review.reviewer_name,
            "decision": review.decision,
            "corrected_level": review.corrected_level,
            "corrected_score": review.corrected_score,
            "note": review.note,
            "corrections": json.loads(review.corrections_json or "[]"),
            "created_at": review.created_at,
        }
        for review in (
            ordered_reviews
            if panel is None or panel.status == "completed"
            else [
                review
                for review in ordered_reviews
                if not (
                    review.panel_id == panel.id
                    and review.review_round == panel.review_round
                )
            ]
        )
    ]
    return {
        "id": result.id,
        "asset_id": result.asset_id,
        "job_id": result.job_id,
        "prompt_id": result.job.prompt_a_id if single_prompt else None,
        "prompt_a_id": result.job.prompt_a_id if not single_prompt else None,
        "prompt_b_id": result.job.prompt_b_id,
        "preprocess": (
            json.loads(result.preprocess_json)
            if result.preprocess_json
            else None
        ),
        "precheck": json.loads(result.precheck_json),
        "aesthetic": json.loads(result.aesthetic_json) if result.aesthetic_json else None,
        "dimension_schema": _evaluation_dimension_schema_payload(result),
        "scoring": json.loads(result.scoring_json),
        "score": result.score,
        "inspiration_aesthetic_score": result.inspiration_aesthetic_score,
        "level": result.level,
        "final_level": final_level,
        "final_score": final_score,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "review_stage": result.review_stage,
        "review_revision": result.review_revision,
        "review_truth_status": (
            "completed" if completed_review is not None else "provisional"
        ),
        "review_history": review_history,
        "correction_history": json.loads(result.correction_history_json or "[]"),
        "review_panel": (
            {
                "id": panel.id,
                "required_reviewers": panel.required_reviewers,
                "submitted_count": sum(
                    1
                    for review in ordered_reviews
                    if review.panel_id == panel.id
                    and review.review_round == panel.review_round
                ),
                "status": panel.status,
                "revision": panel.revision,
                "review_round": panel.review_round,
                "blind_answers_hidden": panel.status != "completed",
            }
            if panel
            else None
        ),
        "human_review": (
            {
                "id": review_for_display.id,
                "review_round": review_for_display.review_round,
                "stage": review_for_display.stage,
                "reviewer_name": review_for_display.reviewer_name,
                "decision": review_for_display.decision,
                "corrected_level": review_for_display.corrected_level,
                "corrected_score": review_for_display.corrected_score,
                "note": review_for_display.note,
                "corrections": json.loads(review_for_display.corrections_json or "[]"),
                "created_at": review_for_display.created_at,
            }
            if review_for_display
            else None
        ),
        "risk_review": (
            json.loads(result.risk_review_json) if result.risk_review_json else None
        ),
        "versions": {
            "model": result.model_id,
            "prompt": result.prompt_a_version if single_prompt else None,
            "prompt_a": result.prompt_a_version if not single_prompt else None,
            "prompt_b": result.prompt_b_version,
            "risk_review": result.risk_review_version,
            "rubric": result.rubric_version,
            "engine": result.engine_version,
        },
        "created_at": result.created_at,
        "updated_at": result.updated_at,
    }


def _evaluation_dimension_schema_payload(
    result: EvaluationResult,
) -> dict[str, Any]:
    """Expose the exact result-bound dimension contract to UI consumers."""
    try:
        v3_payload = v3_review_dimension_schema_payload(result)
        if v3_payload is not None:
            return v3_payload
        definition, dimension_keys, identity = (
            dimension_contract_for_result(result)
        )
        if identity is not None:
            selection = identity.get("selection")
            return {
                "status": "resolved",
                "schema_id": identity["schema_id"],
                "schema_key": identity["schema_key"],
                "version": identity["version"],
                "canonical_hash": identity["canonical_hash"],
                "legacy_derived": False,
                "dimension_keys": list(dimension_keys),
                "dimension_selection": selection,
                "dimension_mode": (
                    selection.get("mode")
                    if isinstance(selection, dict)
                    else "all"
                ),
                "definition": definition,
                "error": None,
            }
        return {
            "status": "resolved",
            "schema_id": None,
            "schema_key": str(
                definition.get("schema_key")
                or definition.get("package_key")
                or "space_aesthetic"
            ),
            "version": str(
                definition.get("compatibility_revision")
                or definition.get("package_version")
                or "legacy-derived"
            ),
            "canonical_hash": canonical_hash(definition),
            "legacy_derived": True,
            "dimension_keys": list(dimension_keys),
            "dimension_selection": None,
            "dimension_mode": "all",
            "definition": definition,
            "error": None,
        }
    except (
        DimensionScoringContractError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        aesthetic: dict[str, Any] = {}
        try:
            parsed = json.loads(result.aesthetic_json or "{}")
            if isinstance(parsed, dict):
                aesthetic = parsed
        except (json.JSONDecodeError, TypeError):
            pass
        dimensions = aesthetic.get("dimensions")
        display_keys = (
            [str(key) for key in dimensions]
            if isinstance(dimensions, dict)
            else []
        )
        return {
            "status": "invalid",
            "schema_id": None,
            "schema_key": None,
            "version": None,
            "canonical_hash": None,
            "legacy_derived": False,
            "dimension_keys": display_keys,
            "dimension_selection": None,
            "dimension_mode": None,
            "definition": None,
            "error": str(exc),
        }


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    workers = automation_worker_snapshot(db)
    return {
        "status": "ok",
        "service": "3d66-label-system",
        "workers": workers,
    }


@app.get("/api/health/ready")
def health_ready(db: Session = Depends(get_db)) -> dict[str, Any]:
    workers = automation_worker_snapshot(db)
    if workers["active_worker_count"] < 1:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "worker_not_ready",
                "message": "主服务已启动，但尚未检测到活跃评测 Worker",
                "workers": workers,
            },
        )
    return {"status": "ready", "service": "3d66-label-system", "workers": workers}


def _assert_bundle_versions(
    bundle: StrategyBundle,
    *,
    model_id: str | None,
    prompt_a_version: str | None,
    prompt_b_version: str | None,
) -> None:
    supplied = {
        "model_id": model_id,
        "prompt_a_version": prompt_a_version,
        "prompt_b_version": prompt_b_version,
    }
    actual = {
        "model_id": bundle.model_id,
        "prompt_a_version": bundle.prompt_a_version,
        "prompt_b_version": bundle.prompt_b_version,
    }
    if any(
        value is not None and value != actual[field]
        for field, value in supplied.items()
    ):
        raise HTTPException(status_code=409, detail="Loop 内禁止模型或提示词策略漂移")


def _reject_invalid_loop_request() -> None:
    raise LoopContractError("Loop 请求不符合接口约束")


def _is_loop_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional_bounded_loop_string(
    value: object,
    *,
    max_length: int,
) -> bool:
    return value is None or (
        isinstance(value, str) and len(value) <= max_length
    )


def _assert_loop_request_is_safe(
    payload: LoopCreateRequest | LoopResultRequest,
) -> None:
    """Scan the untouched request before any type or contract validation."""
    request_data = payload.model_dump(warnings=False)
    assert_safe_normalized_payload(request_data)
    if payload.model_extra:
        _reject_invalid_loop_request()

    idempotency_key = request_data.get("idempotency_key")
    if not (
        isinstance(idempotency_key, str)
        and 8 <= len(idempotency_key) <= 160
    ):
        _reject_invalid_loop_request()
    if not (
        _is_loop_integer(request_data.get("strategy_bundle_id"))
        and request_data["strategy_bundle_id"] >= 1
    ):
        _reject_invalid_loop_request()
    for field, max_length in (
        ("model_id", 200),
        ("prompt_a_version", 40),
        ("prompt_b_version", 40),
    ):
        if not _is_optional_bounded_loop_string(
            request_data.get(field),
            max_length=max_length,
        ):
            _reject_invalid_loop_request()

    if isinstance(payload, LoopCreateRequest):
        if not (
            _is_loop_integer(request_data.get("asset_id"))
            and request_data["asset_id"] >= 1
        ):
            _reject_invalid_loop_request()
        if not isinstance(request_data.get("input_evidence"), dict):
            _reject_invalid_loop_request()
        source = request_data.get("source")
        if source is not None and (
            not isinstance(source, str)
            or source not in {"interactive", "validation"}
        ):
            _reject_invalid_loop_request()
        return

    kind = request_data.get("kind")
    if not isinstance(kind, str) or kind not in {
        "base",
        "targeted_recheck",
        "arbitration",
    }:
        _reject_invalid_loop_request()
    target_dimensions = request_data.get("target_dimensions")
    if not (
        isinstance(target_dimensions, list)
        and len(target_dimensions) <= 100
        and all(isinstance(item, str) for item in target_dimensions)
    ):
        _reject_invalid_loop_request()
    if not isinstance(request_data.get("normalized_result"), dict):
        _reject_invalid_loop_request()
    conflicts = request_data.get("conflicts")
    if not (
        isinstance(conflicts, list)
        and len(conflicts) <= 100
        and all(isinstance(item, dict) for item in conflicts)
    ):
        _reject_invalid_loop_request()
    technical_attempt = request_data.get("technical_attempt")
    if not (_is_loop_integer(technical_attempt) and technical_attempt == 0):
        _reject_invalid_loop_request()
    cost = request_data.get("cost")
    if not (
        cost is None
        or (
            isinstance(cost, (int, float))
            and not isinstance(cost, bool)
            and cost >= 0
        )
    ):
        _reject_invalid_loop_request()
    latency_ms = request_data.get("latency_ms")
    if not (
        latency_ms is None
        or (_is_loop_integer(latency_ms) and latency_ms >= 0)
    ):
        _reject_invalid_loop_request()


def _loop_attempt_payload(attempt: LoopAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "round": attempt.business_round,
        "kind": attempt.kind,
        "target_dimensions": json.loads(attempt.target_dimensions_json),
        "input_evidence": json.loads(attempt.input_evidence_json),
        "normalized_result": (
            json.loads(attempt.normalized_result_json)
            if attempt.normalized_result_json
            else None
        ),
        "conflicts": json.loads(attempt.conflict_json),
        "status": attempt.status,
        "technical_attempt": attempt.technical_attempt,
        "cost": attempt.cost,
        "latency_ms": attempt.latency_ms,
        "created_at": attempt.created_at,
        "completed_at": attempt.completed_at,
    }


def _loop_payload(loop_run: LoopRun) -> dict[str, Any]:
    return {
        "id": loop_run.id,
        "asset_id": loop_run.asset_id,
        "strategy_bundle_id": loop_run.strategy_bundle_id,
        "strategy": {
            "model_id": loop_run.strategy_bundle.model_id,
            "prompt_a_version": loop_run.strategy_bundle.prompt_a_version,
            "prompt_b_version": loop_run.strategy_bundle.prompt_b_version,
            "rubric_version": loop_run.strategy_bundle.rubric_version,
            "engine_version": loop_run.strategy_bundle.engine_version,
        },
        "status": loop_run.status,
        "current_round": loop_run.current_round,
        "decision": json.loads(loop_run.decision_json or "{}"),
        "attempts": [
            _loop_attempt_payload(attempt) for attempt in loop_run.attempts
        ],
        "created_at": loop_run.created_at,
        "updated_at": loop_run.updated_at,
        "completed_at": loop_run.completed_at,
    }


@app.post("/api/loops")
@app.post("/api/loop-runs")
def create_loop(
    payload: LoopCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        _assert_loop_request_is_safe(payload)
    except LoopContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fingerprint_payload = payload.model_dump(exclude={"idempotency_key"})
    fingerprint = request_fingerprint(fingerprint_payload)
    existing = db.scalar(
        select(LoopRun).where(
            LoopRun.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="idempotency_key 已用于不同 Loop 请求",
            )
        return _loop_payload(existing)

    asset = db.get(Asset, payload.asset_id)
    bundle = db.get(StrategyBundle, payload.strategy_bundle_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    if bundle is None:
        raise HTTPException(status_code=404, detail="StrategyBundle 不存在")
    _assert_bundle_versions(
        bundle,
        model_id=payload.model_id,
        prompt_a_version=payload.prompt_a_version,
        prompt_b_version=payload.prompt_b_version,
    )

    loop_run = LoopRun(
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
        asset_id=asset.id,
        strategy_bundle_id=bundle.id,
        status="waiting_result",
        current_round=1,
        decision_json=canonical_json(
            {
                "status": "waiting_result",
                "machine_converged": False,
                "needs_human": False,
                "reason_codes": ["ROUND1_RESULT_REQUIRED"],
                "evidence": {},
                "next_round": 1,
                "next_kind": ROUND_KIND[1],
                "target_dimensions": [],
            }
        ),
        created_by=user.username,
    )
    loop_run.attempts.append(
        LoopAttempt(
            business_round=1,
            kind=ROUND_KIND[1],
            target_dimensions_json="[]",
            input_evidence_json=canonical_json(payload.input_evidence),
            status="waiting_result",
        )
    )
    db.add(loop_run)
    try:
        db.flush()
        evidence_source = str(
            payload.input_evidence.get("source")
            or payload.input_evidence.get("queue_class")
            or ""
        ).lower()
        queue_class = (
            "validation"
            if payload.source == "validation"
            or evidence_source in {
                "validation",
                "golden_regression",
                "paired_regression",
            }
            else "interactive"
        )
        enqueue_loop_evaluation_job(
            db,
            loop_run=loop_run,
            attempt=loop_run.attempts[0],
            queue_class=queue_class,
        )
        db.commit()
    except (IntegrityError, LoopContractError) as exc:
        db.rollback()
        existing = db.scalar(
            select(LoopRun).where(
                LoopRun.idempotency_key == payload.idempotency_key
            )
        )
        if isinstance(exc, LoopContractError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if existing is None or existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Loop 创建发生幂等冲突",
            )
        return _loop_payload(existing)
    db.refresh(loop_run)
    return _loop_payload(loop_run)


@app.get("/api/loops/{loop_id}")
@app.get("/api/loop-runs/{loop_id}")
def loop_detail(
    loop_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    loop_run = db.get(LoopRun, loop_id)
    if loop_run is None:
        raise HTTPException(status_code=404, detail="Loop 不存在")
    return _loop_payload(loop_run)


@app.post("/api/loops/{loop_id}/attempts/{business_round}/result")
@app.post("/api/loop-runs/{loop_id}/attempts/{business_round}/result")
def submit_loop_result(
    loop_id: int,
    business_round: int,
    payload: LoopResultRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        _assert_loop_request_is_safe(payload)
    except LoopContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    submitted_dimensions = [
        dimension.strip() for dimension in payload.target_dimensions
    ]
    if (
        any(not dimension for dimension in submitted_dimensions)
        or len(submitted_dimensions) != len(set(submitted_dimensions))
    ):
        raise HTTPException(
            status_code=409,
            detail="target_dimensions 不能包含空值或重复值",
        )
    if business_round not in ROUND_KIND:
        raise HTTPException(status_code=409, detail="最多三轮，禁止第四轮")
    loop_run = db.get(LoopRun, loop_id)
    if loop_run is None:
        raise HTTPException(status_code=404, detail="Loop 不存在")
    if payload.strategy_bundle_id != loop_run.strategy_bundle_id:
        raise HTTPException(status_code=409, detail="Loop 内禁止 StrategyBundle 漂移")
    _assert_bundle_versions(
        loop_run.strategy_bundle,
        model_id=payload.model_id,
        prompt_a_version=payload.prompt_a_version,
        prompt_b_version=payload.prompt_b_version,
    )
    attempt = db.scalar(
        select(LoopAttempt).where(
            LoopAttempt.loop_run_id == loop_run.id,
            LoopAttempt.business_round == business_round,
        )
    )
    content = payload.model_dump(exclude={"idempotency_key"})
    content["target_dimensions"] = submitted_dimensions
    fingerprint = request_fingerprint(content)
    if attempt is not None and attempt.status == "completed":
        if (
            attempt.result_idempotency_key == payload.idempotency_key
            and attempt.result_fingerprint == fingerprint
        ):
            return _loop_payload(loop_run)
        raise HTTPException(status_code=409, detail="完成的 LoopAttempt 不可变")
    if (
        attempt is None
        or loop_run.status != "waiting_result"
        or business_round != loop_run.current_round
    ):
        raise HTTPException(status_code=409, detail="禁止越序提交 Loop 轮次")

    expected_dimensions = json.loads(attempt.target_dimensions_json)
    try:
        validate_submission_scope(
            business_round=business_round,
            expected_kind=attempt.kind,
            expected_dimensions=expected_dimensions,
            submitted_kind=payload.kind,
            submitted_dimensions=submitted_dimensions,
        )
        validate_result_scope(
            business_round=business_round,
            target_dimensions=submitted_dimensions,
            normalized_result={
                **payload.normalized_result,
                **({"conflicts": payload.conflicts} if payload.conflicts else {}),
            },
        )
    except LoopContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    normalized_result = dict(payload.normalized_result)
    if business_round in (2, 3):
        normalized_result = normalize_targeted_model_result(
            normalized_result,
            business_round=business_round,
            target_dimensions=submitted_dimensions,
        )
    elif payload.conflicts:
        # Round 1 conflict declarations are conservative input only; later
        # rounds derive actual conflicts from adjacent server-normalized values.
        normalized_result["conflicts"] = payload.conflicts
    jobs = db.scalars(
        select(EvaluationJob)
        .where(EvaluationJob.loop_attempt_id == attempt.id)
        .order_by(
            EvaluationJob.technical_attempt.desc(),
            EvaluationJob.id.desc(),
        )
    ).all()
    if any(job.status == "processing" for job in jobs):
        raise HTTPException(
            status_code=409,
            detail="关联 EvaluationJob 正在执行，禁止人工结果覆盖",
        )
    if any(job.technical_attempt > 0 for job in jobs):
        raise HTTPException(
            status_code=409,
            detail="技术 recovery 链已启动，人工导入只能使用 attempt 0",
        )
    for job in jobs:
        if job.status == "queued":
            job.status = "canceled"
            job.stage = "manual_attach"
            job.finished_at = datetime.now(timezone.utc)
    try:
        advance_loop_attempt(
            db,
            loop_run=loop_run,
            attempt=attempt,
            normalized_result=normalized_result,
            result_idempotency_key=payload.idempotency_key,
            result_fingerprint=fingerprint,
            technical_attempt=0,
            cost=payload.cost,
            latency_ms=payload.latency_ms,
            next_queue_class=(
                jobs[0].origin_queue_class
                if jobs and jobs[0].origin_queue_class
                else "interactive"
            ),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent_attempt = db.scalar(
            select(LoopAttempt).where(
                LoopAttempt.loop_run_id == loop_id,
                LoopAttempt.business_round == business_round,
            )
        )
        concurrent_loop = db.get(LoopRun, loop_id)
        if (
            concurrent_attempt is not None
            and concurrent_loop is not None
            and concurrent_attempt.status == "completed"
            and concurrent_attempt.result_idempotency_key
            == payload.idempotency_key
            and concurrent_attempt.result_fingerprint == fingerprint
        ):
            return _loop_payload(concurrent_loop)
        raise HTTPException(
            status_code=409,
            detail="LoopAttempt 并发提交冲突",
        )
    except LoopContractError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(loop_run)
    return _loop_payload(loop_run)


def _breaker_payload(breaker: CircuitBreaker) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cooldown_until = breaker.cooldown_until
    if cooldown_until is not None and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    return {
        "id": breaker.id,
        "scope_type": breaker.scope_type,
        "scope_key": breaker.scope_key,
        "state": breaker.state,
        "failure_count": breaker.failure_count,
        "window_started_at": breaker.window_started_at,
        "last_failure_at": breaker.last_failure_at,
        "opened_at": breaker.opened_at,
        "cooldown_until": breaker.cooldown_until,
        "cooldown_elapsed": bool(
            cooldown_until is not None and cooldown_until <= now
        ),
        "reason": breaker.reason,
        "reset_by": breaker.reset_by,
        "reset_at": breaker.reset_at,
        "updated_at": breaker.updated_at,
    }


@app.get("/api/circuit-breakers")
def list_circuit_breakers(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    breakers = db.scalars(
        select(CircuitBreaker).order_by(
            CircuitBreaker.scope_type.asc(),
            CircuitBreaker.scope_key.asc(),
        )
    ).all()
    return {"items": [_breaker_payload(breaker) for breaker in breakers]}


@app.post("/api/circuit-breakers/{scope_type}/{scope_key}/open")
def open_circuit_breaker(
    scope_type: Literal["strategy", "batch"],
    scope_key: str,
    payload: BreakerOpenRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    breaker = db.scalar(
        select(CircuitBreaker).where(
            CircuitBreaker.scope_type == scope_type,
            CircuitBreaker.scope_key == scope_key,
        )
    )
    if breaker is None:
        breaker = CircuitBreaker(
            scope_type=scope_type,
            scope_key=scope_key,
        )
        db.add(breaker)
    now = datetime.now(timezone.utc)
    breaker.state = "open"
    breaker.opened_at = now
    breaker.cooldown_until = now + timedelta(
        seconds=payload.cooldown_seconds
    )
    breaker.reason = payload.reason
    breaker.reset_by = None
    breaker.reset_at = None
    db.commit()
    db.refresh(breaker)
    return _breaker_payload(breaker)


@app.post("/api/circuit-breakers/{scope_type}/{scope_key}/reset")
def reset_circuit_breaker(
    scope_type: Literal["strategy", "batch"],
    scope_key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    breaker = db.scalar(
        select(CircuitBreaker).where(
            CircuitBreaker.scope_type == scope_type,
            CircuitBreaker.scope_key == scope_key,
        )
    )
    if breaker is None:
        raise HTTPException(status_code=404, detail="Circuit breaker 不存在")
    breaker.state = "closed"
    breaker.failure_count = 0
    breaker.window_started_at = None
    breaker.last_failure_at = None
    breaker.opened_at = None
    breaker.cooldown_until = None
    breaker.reason = None
    breaker.reset_by = user.username
    breaker.reset_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(breaker)
    return _breaker_payload(breaker)


@app.get("/api/queues/status")
def queue_status(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    pending = {queue: 0 for queue in QUEUE_CLASSES}
    running = {queue: 0 for queue in QUEUE_CLASSES}
    dispatchable = {queue: 0 for queue in QUEUE_CLASSES}
    delayed = {queue: 0 for queue in QUEUE_CLASSES}
    credential_blocked = {queue: 0 for queue in QUEUE_CLASSES}
    control_blocked = {queue: 0 for queue in QUEUE_CLASSES}
    rows = db.execute(
        select(
            EvaluationJob.queue_class,
            EvaluationJob.status,
            func.count(EvaluationJob.id),
        )
        .where(EvaluationJob.status.in_(("queued", "processing")))
        .group_by(EvaluationJob.queue_class, EvaluationJob.status)
    ).all()
    for queue_class, status, count in rows:
        queue = queue_class or "production_batch"
        if queue not in pending:
            continue
        if status == "queued":
            pending[queue] = count
        else:
            running[queue] = count
    blocked = {queue: 0 for queue in QUEUE_CLASSES}
    open_breakers = set(
        db.execute(
            select(
                CircuitBreaker.scope_type,
                CircuitBreaker.scope_key,
            ).where(CircuitBreaker.state == "open")
        ).all()
    )
    configured_model = default_evaluation_model(db)
    control = db.get(EvaluationControl, 1)
    control_paused = bool(control and control.paused)
    now = datetime.now(timezone.utc)
    queued_jobs = db.scalars(
        select(EvaluationJob).where(EvaluationJob.status == "queued")
    ).all()
    if configured_model is None:
        configured_model = next(
            (
                model
                for job in queued_jobs
                if (
                    (model := job_primary_model(db, job)) is not None
                    and model_has_credentials(model)
                )
            ),
            None,
        )
    credentials_configured = configured_model is not None
    for job in queued_jobs:
        queue = job.queue_class or "production_batch"
        strategy_blocked = (
            job.strategy_bundle_id is not None
            and ("strategy", str(job.strategy_bundle_id)) in open_breakers
        )
        batch_blocked = bool(
            job.batch_key
            and ("batch", job.batch_key) in open_breakers
        )
        if queue not in blocked:
            continue
        breaker_blocked = strategy_blocked or batch_blocked
        retry_at = job.retry_after_at
        if retry_at is not None and retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        retry_delayed = retry_at is not None and retry_at > now
        if breaker_blocked:
            blocked[queue] += 1
        if retry_delayed:
            delayed[queue] += 1
        otherwise_dispatchable = not breaker_blocked and not retry_delayed
        job_credentials_configured = job_has_required_credentials(
            db,
            job,
            fallback_model=configured_model,
        )
        if otherwise_dispatchable and not job_credentials_configured:
            credential_blocked[queue] += 1
        if otherwise_dispatchable and control_paused:
            control_blocked[queue] += 1
        if (
            otherwise_dispatchable
            and job_credentials_configured
            and not control_paused
        ):
            dispatchable[queue] += 1
    policy = QueuePolicy(
        global_limit=(
            configured_model.max_concurrency
            if configured_model is not None
            else 2
        )
    )
    persisted = db.get(QueueSchedulerState, 1)
    scheduler = DeterministicQueueScheduler(
        policy,
        deficits=(
            {
                "validation": persisted.validation_deficit,
                "interactive": persisted.interactive_deficit,
                "production_batch": (
                    persisted.production_batch_deficit
                ),
                "canary": persisted.canary_deficit,
                "recovery": persisted.recovery_deficit,
            }
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else None
        ),
        dispatch_count=(
            persisted.dispatch_count
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else 0
        ),
        last_recovery_dispatch=(
            persisted.last_recovery_dispatch
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else None
        ),
    )
    snapshot = scheduler.snapshot(
        pending=dispatchable,
        running=running,
    )
    for item in snapshot["queues"]:
        queue = item["queue_class"]
        item["pending"] = pending[queue]
        item["pending_total"] = pending[queue]
        item["blocked_by_breaker"] = blocked[queue]
        item["blocked_by_credentials"] = credential_blocked[queue]
        item["blocked_by_control"] = control_blocked[queue]
        item["delayed_by_retry_after"] = delayed[queue]
        item["dispatchable_pending"] = dispatchable[queue]
    snapshot["credentials_configured"] = credentials_configured
    snapshot["control_paused"] = control_paused
    return snapshot


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token, token_hash = create_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    user.last_login_at = datetime.now(timezone.utc)
    db.add(SessionToken(token_hash=token_hash, user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_days * 24 * 3600,
        path="/",
    )
    return _user_payload(user)


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if session_cookie:
        db.query(SessionToken).filter(
            SessionToken.token_hash == hash_session_token(session_cookie)
        ).delete()
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return _user_payload(user)


@app.get("/api/auth/roles")
def list_roles(_user: User = Depends(admin_user)) -> dict[str, Any]:
    return {
        "items": [
            {"role": role, "label": ROLE_LABELS[role], "permissions": sorted(permissions)}
            for role, permissions in ROLE_PERMISSIONS.items()
        ]
    }


@app.get("/api/users")
def list_users(_user: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = db.scalars(select(User).order_by(User.id.asc())).all()
    return {"items": [_user_payload(user) for user in users]}


@app.post("/api/users")
def create_user(
    payload: UserCreateRequest,
    actor: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(User).where(User.username == payload.username)) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_admin=payload.role == "admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    append_audit_event(
        db,
        category="auth",
        action="user_created",
        subject_type="user",
        subject_id=str(user.id),
        actor=actor.username,
        event_key=f"user:{user.id}:created",
        payload={"username": user.username, "role": user.role},
    )
    db.commit()
    return _user_payload(user)


@app.patch("/api/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    actor: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if user.id == actor.id and (not payload.is_active or payload.role != "admin"):
        raise HTTPException(status_code=422, detail="不能移除当前登录账号的管理员权限或停用当前账号")
    if (not payload.is_active or payload.role != "admin") and user.role == "admin":
        enabled_admins = db.scalar(
            select(func.count()).select_from(User).where(User.is_active.is_(True), User.role == "admin")
        ) or 0
        if enabled_admins <= 1:
            raise HTTPException(status_code=422, detail="至少保留一个启用中的系统管理员")
    user.display_name = payload.display_name
    user.role = payload.role
    user.is_admin = payload.role == "admin"
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if not user.is_active or payload.password:
        db.query(SessionToken).filter(SessionToken.user_id == user.id).delete()
    append_audit_event(
        db, category="auth", action="user_updated", subject_type="user", subject_id=str(user.id),
        actor=actor.username, event_key=f"user:{user.id}:updated:{uuid.uuid4().hex}",
        payload={"role": user.role, "is_active": user.is_active, "password_reset": bool(payload.password)},
    )
    db.commit()
    db.refresh(user)
    return _user_payload(user)


@app.get("/api/model-nodes")
def list_model_nodes(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(ModelNodeBinding).order_by(ModelNodeBinding.node_key.asc())).all()
    return {
        "items": [
            {
                "node_key": row.node_key,
                "model_config_id": row.model_config_id,
                "category_key": row.category_key,
                "enabled": row.enabled,
                "model": _model_config_payload(row.model),
            }
            for row in rows
        ]
    }


@app.put("/api/model-nodes/{node_key}")
def update_model_node(
    node_key: str,
    payload: ModelNodeBindingRequest,
    actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if node_key not in MODEL_NODE_KEYS:
        raise HTTPException(status_code=422, detail="未知模型节点")
    config = db.get(ModelConfig, payload.model_config_id)
    if config is None or not config.active:
        raise HTTPException(status_code=422, detail="模型配置不存在或未启用")
    if payload.category_key is not None:
        _category_profile(db, payload.category_key)
    row = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == node_key, ModelNodeBinding.category_key == payload.category_key))
    if row is None:
        row = ModelNodeBinding(node_key=node_key)
        db.add(row)
    row.model_config_id = config.id
    row.category_key = payload.category_key
    row.enabled = payload.enabled
    row.updated_by = actor.username
    db.commit()
    db.refresh(row)
    return {"node_key": row.node_key, "model_config_id": row.model_config_id, "category_key": row.category_key, "enabled": row.enabled, "model": _model_config_payload(config)}


@app.get("/api/dashboard")
def dashboard(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset_count = db.scalar(
        select(func.count()).select_from(Asset).where(Asset.status != "deleted")
    ) or 0
    queued = db.scalar(
        select(func.count()).select_from(EvaluationJob).where(EvaluationJob.status == "queued")
    ) or 0
    processing = db.scalar(
        select(func.count()).select_from(EvaluationJob).where(EvaluationJob.status == "processing")
    ) or 0
    result_rows = db.scalars(
        select(EvaluationResult).order_by(EvaluationResult.created_at.desc())
    ).all()
    latest_results: dict[int, EvaluationResult] = {}
    for result in result_rows:
        latest_results.setdefault(result.asset_id, result)
    review_count = sum(1 for result in latest_results.values() if result.needs_review)
    levels: dict[str, int] = {}
    for result in latest_results.values():
        payload = _result_payload(result) or {}
        final_level = payload.get("final_level")
        if final_level:
            levels[final_level] = levels.get(final_level, 0) + 1
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    return {
        "asset_count": asset_count,
        "queued": queued,
        "processing": processing,
        "needs_review": review_count,
        "levels": levels,
        "model": {
            "name": model.name if model else "未配置",
            "model_id": model.model_id if model else "",
            "has_api_key": bool(model and model.encrypted_api_key),
        },
    }


def _upload_package_name(value: str | None, *, fallback: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        normalized = fallback
    if len(normalized) > 200:
        raise HTTPException(status_code=422, detail="素材包名称不能超过 200 个字符")
    return normalized


def _category_profile(
    db: Session,
    category_key: str,
    *,
    require_active: bool = False,
) -> EvaluationCategoryProfile:
    if CATEGORY_KEY_PATTERN.fullmatch(category_key) is None:
        raise HTTPException(status_code=422, detail="评测类目标识格式无效")
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key,
        )
    )
    if profile is None:
        defaults = CATEGORY_PROFILE_DEFAULTS.get(category_key)
        if defaults is None:
            raise HTTPException(status_code=404, detail="评测类目不存在")
        profile = EvaluationCategoryProfile(
            category_key=category_key,
            display_name=defaults["display_name"],
            allowed_mime_types_json=defaults["allowed_mime_types_json"],
            preprocess_config_json=defaults["preprocess_config_json"],
            pipeline_config_json=canonical_json(default_pipeline(category_key)),
            status="active",
            rubric_version=(
                "inspiration-rubric-v1"
                if category_key == "inspiration_image"
                else "rubric-v2.1"
            ),
            dimension_schema_key=(
                SPACE_SCHEMA_KEY
                if category_key in {"space_image", "inspiration_image"}
                else None
            ),
            dimension_schema_version=(
                ACTIVE_V13_VERSION
                if category_key in {"space_image", "inspiration_image"}
                else None
            ),
            created_by="compatibility-default",
        )
        db.add(profile)
        db.flush()
    if require_active and profile.status != "active":
        raise HTTPException(status_code=409, detail="评测类目未启用")
    return profile


def _profile_pipeline(
    profile: EvaluationCategoryProfile,
    db: Session | None = None,
    dimension_definition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        raw = json.loads(profile.pipeline_config_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if raw.get("schema_version") != "category-pipeline-v1":
        raw = legacy_preprocess_to_pipeline(
            profile.category_key,
            json.loads(profile.preprocess_config_json or "{}"),
        )
    definition_options = dimension_options_from_definition(dimension_definition)
    allowed_dimension_keys = (
        [item["key"] for item in definition_options]
        if definition_options
        else None
    )
    if (
        db is not None
        and profile.dimension_schema_key
        and profile.dimension_schema_version
    ):
        schema = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == profile.dimension_schema_key,
                DimensionSchema.version == profile.dimension_schema_version,
            )
        )
        if schema is not None:
            try:
                options = dimension_options_from_definition(
                    json.loads(schema.definition_json)
                )
            except json.JSONDecodeError:
                options = []
            if options:
                allowed_dimension_keys = [item["key"] for item in options]
    try:
        return validate_pipeline_config(
            raw,
            allowed_dimension_keys=(
                allowed_dimension_keys
                if allowed_dimension_keys is not None
                else [item["key"] for item in DIMENSION_OPTIONS]
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"类目流水线配置损坏：{exc}") from None


def _category_dimension_management_payload(
    db: Session,
    profile: EvaluationCategoryProfile,
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    """Expose one category's editable selection without mutating its schema."""

    schema_status = "unconfigured"
    schema_immutable = False
    schema_error: dict[str, str] | None = None
    options = [dict(item) for item in DIMENSION_OPTIONS]
    schema_key = profile.dimension_schema_key
    schema_version = profile.dimension_schema_version
    schema_hash: str | None = None
    if schema_key is not None and schema_version is not None:
        matches = db.scalars(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == schema_key,
                DimensionSchema.version == schema_version,
            )
        ).all()
        if len(matches) != 1:
            schema_status = "missing" if not matches else "ambiguous"
            schema_error = {
                "code": f"dimension_contract_{schema_status}",
                "message": "类目绑定的维度方案不存在或版本不唯一。",
            }
        else:
            schema = matches[0]
            schema_status = schema.status
            schema_immutable = schema.status in {"published", "retired"}
            try:
                definition = json.loads(schema.definition_json)
            except json.JSONDecodeError:
                definition = None
            if (
                not isinstance(definition, dict)
                or canonical_hash(definition) != schema.canonical_hash
            ):
                schema_error = {
                    "code": "dimension_contract_invalid",
                    "message": "类目绑定的维度方案内容或校验值损坏。",
                }
            else:
                resolved_options = dimension_options_from_definition(definition)
                if not resolved_options:
                    schema_error = {
                        "code": "dimension_contract_invalid",
                        "message": "类目绑定的维度方案没有可管理的维度定义。",
                    }
                else:
                    options = resolved_options
                    schema_hash = schema.canonical_hash

    selection: dict[str, Any] | None
    try:
        selection = dimension_selection_payload(
            pipeline,
            dimension_options=options,
            schema_key=schema_key,
            schema_version=schema_version,
            schema_hash=schema_hash,
        )
    except ValueError as exc:
        selection = None
        schema_error = schema_error or {
            "code": "dimension_selection_invalid",
            "message": str(exc),
        }
    return {
        "schema_version": "category-dimension-management-v1",
        "schema_status": schema_status,
        "schema_immutable": schema_immutable,
        "available_options": options,
        "selection": selection,
        "error": schema_error,
    }


def _category_profile_payload(
    db: Session,
    profile: EvaluationCategoryProfile,
) -> dict[str, Any]:
    pipeline = _profile_pipeline(profile, db)
    return {
        "id": profile.id,
        "category_key": profile.category_key,
        "display_name": profile.display_name,
        "description": profile.description,
        "status": profile.status,
        "allowed_mime_types": json.loads(profile.allowed_mime_types_json or "[]"),
        "preprocess_config": json.loads(profile.preprocess_config_json or "{}"),
        "pipeline_config": pipeline,
        "dimension_management": _category_dimension_management_payload(
            db,
            profile,
            pipeline,
        ),
        "pipeline_revision": profile.pipeline_revision,
        "prompt_a_id": profile.prompt_a_id,
        "prompt_b_id": profile.prompt_b_id,
        "model_config_id": profile.model_config_id,
        "automation_config": json.loads(profile.automation_config_json or "{}"),
        "automation_revision": profile.automation_revision,
        "rubric_version": profile.rubric_version,
        "dimension_schema_key": profile.dimension_schema_key,
        "dimension_schema_version": profile.dimension_schema_version,
        "created_by": profile.created_by,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _category_execution_snapshot(
    profile: EvaluationCategoryProfile,
    *,
    prompt_a_id: int,
    prompt_b_id: int | None,
    model_config: ModelConfig,
    pdf_summary_model_config: ModelConfig | None = None,
    dimension_contract: Any | None = None,
    dimension_mode_override: Literal["all", "none"] | None = None,
    rubric_version_override: str | None = None,
    v3_authoritative_bundle: dict[str, Any] | None = None,
) -> str:
    pipeline = _profile_pipeline(
        profile,
        dimension_definition=(
            dimension_contract.definition
            if dimension_contract is not None else None
        ),
    )
    if dimension_mode_override is not None:
        pipeline = dict(pipeline)
        pipeline["dimensions"] = {
            "enabled": dimension_mode_override != "none",
            "mode": dimension_mode_override,
            "selected_keys": [],
            "enabled_keys": [],
        }
    dimension_options = (
        dimension_options_from_definition(dimension_contract.definition)
        if dimension_contract is not None
        else list(DIMENSION_OPTIONS)
    )
    dimension_selection = dimension_selection_payload(
        pipeline,
        dimension_options=dimension_options,
        schema_key=(dimension_contract.schema_key if dimension_contract is not None else None),
        schema_version=(dimension_contract.version if dimension_contract is not None else None),
        schema_hash=(dimension_contract.canonical_hash if dimension_contract is not None else None),
    )
    return canonical_json(
        {
            "schema_version": "evaluation-category-profile-v2",
            "profile_id": profile.id,
            "category_key": profile.category_key,
            "display_name": profile.display_name,
            "allowed_mime_types": json.loads(
                profile.allowed_mime_types_json or "[]"
            ),
            "preprocess_config": json.loads(
                profile.preprocess_config_json or "{}"
            ),
            "pipeline_config": pipeline,
            "pipeline_revision": profile.pipeline_revision,
            "prompt_a_id": prompt_a_id,
            "prompt_b_id": prompt_b_id,
            "model_config_id": model_config.id,
            "model_config": build_model_config_snapshot(model_config),
            "pdf_summary_model_config_id": (
                pdf_summary_model_config.id if pdf_summary_model_config is not None else None
            ),
            "pdf_summary_model_config": (
                build_model_config_snapshot(pdf_summary_model_config)
                if pdf_summary_model_config is not None else None
            ),
            "rubric_version": rubric_version_override or profile.rubric_version,
            "dimension_schema_key": (
                dimension_contract.schema_key
                if dimension_contract is not None
                else profile.dimension_schema_key
            ),
            "dimension_schema_version": (
                dimension_contract.version
                if dimension_contract is not None
                else profile.dimension_schema_version
            ),
            "dimension_contract": (
                {
                    "schema_id": dimension_contract.schema_id,
                    "schema_key": dimension_contract.schema_key,
                    "version": dimension_contract.version,
                    "canonical_hash": dimension_contract.canonical_hash,
                    "definition": dimension_contract.definition,
                }
                if dimension_contract is not None else None
            ),
            "dimension_selection": dimension_selection,
            **(
                {"v3_authoritative_bundle": v3_authoritative_bundle}
                if v3_authoritative_bundle is not None
                else {}
            ),
            "profile_updated_at": (
                profile.updated_at.isoformat()
                if profile.updated_at is not None
                else None
            ),
        }
    )


@app.get("/api/evaluation-categories")
def list_evaluation_categories(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    for category_key in CATEGORY_KEYS:
        _category_profile(db, category_key)
    db.commit()
    profiles = db.scalars(
        select(EvaluationCategoryProfile).order_by(EvaluationCategoryProfile.id.asc())
    ).all()
    return {"items": [_category_profile_payload(db, profile) for profile in profiles]}


@app.get("/api/evaluation-categories/modules")
def evaluation_category_modules(
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    return pipeline_catalog_payload()


def _apply_category_update(
    *,
    db: Session,
    profile: EvaluationCategoryProfile,
    payload: EvaluationCategoryProfileUpdate,
    user: User,
) -> None:
    candidate = payload.pipeline_config
    if candidate is None:
        candidate = legacy_preprocess_to_pipeline(
            profile.category_key,
            payload.preprocess_config,
        )
    if profile.id is not None:
        current_pipeline = _profile_pipeline(profile)

        def dimension_signature(config: dict[str, Any]) -> tuple[bool, str, tuple[str, ...]]:
            dimensions = config.get("dimensions") or {}
            raw_keys = (
                dimensions.get("selected_keys")
                or dimensions.get("enabled_keys")
                or []
            )
            return (
                bool(dimensions.get("enabled", dimensions.get("mode") != "none")),
                str(dimensions.get("mode") or "all"),
                tuple(sorted(str(key) for key in raw_keys)),
            )

        changes_legacy_dimensions = (
            dimension_signature(candidate) != dimension_signature(current_pipeline)
            or payload.dimension_schema_key != profile.dimension_schema_key
            or payload.dimension_schema_version != profile.dimension_schema_version
        )
        if changes_legacy_dimensions:
            raise HTTPException(
                status_code=410,
                detail={
                    "code": "legacy_dimension_write_retired",
                    "message": (
                        "旧类目维度写入口已下线，请使用"
                        "“类目评测 v3 合同配置”。"
                    ),
                },
            )
    try:
        raw_mode = str((candidate.get("dimensions") or {}).get("mode") or "all")
        dimension_contract = resolve_published_dimension_contract(
            db,
            schema_key=payload.dimension_schema_key,
            version=payload.dimension_schema_version,
            require_configured=(payload.status == "active" and raw_mode != "none"),
        )
        pipeline = validate_pipeline_config(
            candidate,
            allowed_dimension_keys=(
                [item["key"] for item in dimension_options_from_definition(dimension_contract.definition)]
                if dimension_contract is not None
                else [item["key"] for item in DIMENSION_OPTIONS]
            ),
        )
    except ProductionDimensionContractError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    expected_mimes = set(allowed_mimes_for_pipeline(pipeline))
    if not set(payload.allowed_mime_types).issubset(expected_mimes):
        raise HTTPException(status_code=422, detail="类目 MIME 类型与流水线输入类型不匹配")
    if payload.status == "active" and not payload.allowed_mime_types:
        raise HTTPException(status_code=422, detail="启用类目前必须配置输入 MIME")
    prompt_mode = pipeline["prompt_mode"]
    if prompt_mode == "single" and payload.prompt_b_id is not None:
        raise HTTPException(status_code=422, detail="单提示词模式不能绑定 B 阶段版本")
    if prompt_mode == "ab" and (
        (payload.prompt_a_id is None) != (payload.prompt_b_id is None)
    ):
        raise HTTPException(status_code=422, detail="A/B 模式必须同时绑定 A、B 阶段版本")
    if prompt_mode == "follow" and (payload.prompt_a_id is not None or payload.prompt_b_id is not None):
        raise HTTPException(status_code=422, detail="跟随任务模式不能冻结类目提示词")
    for stage, prompt_id in (("A", payload.prompt_a_id), ("B", payload.prompt_b_id)):
        if prompt_id is None:
            continue
        prompt = db.get(PromptVersion, prompt_id)
        if prompt is None or prompt.stage != stage:
            raise HTTPException(status_code=422, detail=f"类目 {stage} 提示词必须是有效的 {stage} 阶段版本")
        if prompt.category_key != profile.category_key:
            raise HTTPException(
                status_code=422,
                detail=f"类目 {stage} 提示词属于其他评测类目",
            )
        if payload.status == "active" and prompt.status != "published":
            raise HTTPException(
                status_code=422,
                detail=f"启用类目只能绑定已通过二审发布的 {stage} 阶段提示词",
            )
        if prompt.rubric_version != payload.rubric_version.strip():
            raise HTTPException(status_code=422, detail=f"类目 {stage} 提示词与 rubric 版本不一致")
    if payload.model_config_id is not None:
        category_model = db.get(ModelConfig, payload.model_config_id)
        if category_model is None or not category_model.active:
            raise HTTPException(status_code=422, detail="类目模型配置不存在或未启用")
    dimension_mode = pipeline["dimensions"]["mode"]
    if dimension_mode == "none" and prompt_mode != "single":
        raise HTTPException(
            status_code=422,
            detail="关闭维度的仅提示词实验必须使用单提示词模式",
        )
    try:
        dimension_selection_payload(
            pipeline,
            dimension_options=(
                dimension_options_from_definition(dimension_contract.definition)
                if dimension_contract is not None
                else DIMENSION_OPTIONS
            ),
            schema_key=(
                dimension_contract.schema_key
                if dimension_contract is not None else None
            ),
            schema_version=(
                dimension_contract.version
                if dimension_contract is not None else None
            ),
            schema_hash=(
                dimension_contract.canonical_hash
                if dimension_contract is not None else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "dimension_selection_invalid",
                "message": str(exc),
            },
        ) from exc
    profile.display_name = payload.display_name.strip()
    profile.description = payload.description.strip()
    profile.status = payload.status
    profile.allowed_mime_types_json = canonical_json(payload.allowed_mime_types)
    profile.preprocess_config_json = canonical_json(payload.preprocess_config)
    next_pipeline = canonical_json(pipeline)
    if next_pipeline != (profile.pipeline_config_json or ""):
        profile.pipeline_config_json = next_pipeline
        profile.pipeline_revision += 1
    profile.prompt_a_id = payload.prompt_a_id
    profile.prompt_b_id = payload.prompt_b_id
    profile.model_config_id = payload.model_config_id
    profile.rubric_version = payload.rubric_version.strip()
    profile.dimension_schema_key = payload.dimension_schema_key
    profile.dimension_schema_version = payload.dimension_schema_version
    automation = dict(payload.automation_config)
    if automation:
        # Binding provenance is server-owned. Reviewers and other callers must
        # not be able to make an automatically resolved baseline look explicit.
        automation.pop("baseline_binding_source", None)
        if not isinstance(automation.get("enabled", True), bool):
            raise HTTPException(status_code=422, detail="类目自动化 enabled 必须是布尔值")
        for key, low, high in (("case_threshold", 1, 1000), ("cooldown_seconds", 0, 86400), ("max_candidates", 1, 5)):
            value = automation.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high):
                raise HTTPException(status_code=422, detail=f"类目自动化 {key} 超出允许范围")
        baseline_bundle_id = automation.get("baseline_strategy_bundle_id")
        if baseline_bundle_id is not None and effective_role(user) != "admin":
            raise HTTPException(
                status_code=403,
                detail="仅系统管理员可在后台高级设置显式选择基线 Bundle",
            )
        if baseline_bundle_id is not None and (
            not isinstance(baseline_bundle_id, int)
            or isinstance(baseline_bundle_id, bool)
            or baseline_bundle_id < 1
        ):
            raise HTTPException(
                status_code=422,
                detail="类目自动化 baseline_strategy_bundle_id 必须是正整数",
            )
        if baseline_bundle_id is not None:
            automation["baseline_binding_source"] = "explicit_admin"
        profile.automation_config_json = canonical_json(automation)
        profile.automation_revision += 1
        if baseline_bundle_id is not None:
            baseline_bundle = db.get(StrategyBundle, baseline_bundle_id)
            errors = (
                ["baseline_strategy_bundle_missing"]
                if baseline_bundle is None
                else category_bundle_contract_errors(
                    db,
                    profile=profile,
                    bundle=baseline_bundle,
                    require_complete=True,
                    require_prompt_b=True,
                    enforce_baseline_id=True,
                )
            )
            if errors:
                raise HTTPException(
                    status_code=422,
                    detail="基线 StrategyBundle 与类目 A/B、模型、Rubric 或维度合同不一致",
                )
    profile.created_by = profile.created_by or user.username


@app.post("/api/evaluation-categories", status_code=201)
def create_evaluation_category(
    payload: EvaluationCategoryProfileCreate,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.scalar(select(EvaluationCategoryProfile.id).where(EvaluationCategoryProfile.category_key == payload.category_key)) is not None:
        raise HTTPException(status_code=409, detail="评测类目标识已存在")
    profile = EvaluationCategoryProfile(
        category_key=payload.category_key,
        display_name=payload.display_name.strip(),
        description=payload.description.strip(),
        status="draft",
        allowed_mime_types_json=canonical_json(payload.allowed_mime_types),
        preprocess_config_json=canonical_json(payload.preprocess_config),
        pipeline_config_json="{}",
        pipeline_revision=0,
        created_by=user.username,
    )
    db.add(profile)
    _apply_category_update(db=db, profile=profile, payload=payload, user=user)
    db.commit()
    db.refresh(profile)
    return _category_profile_payload(db, profile)


@app.put("/api/evaluation-categories/{category_key}")
def update_evaluation_category(
    category_key: str,
    payload: EvaluationCategoryProfileUpdate,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _category_profile(db, category_key)
    _apply_category_update(db=db, profile=profile, payload=payload, user=user)
    db.commit()
    db.refresh(profile)
    return _category_profile_payload(db, profile)


def _validate_image_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None,
) -> tuple[str, int, int]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"{filename} 为空或超过 25MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            detected_mime = Image.MIME.get(
                image.format or "",
                content_type or "",
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{filename} 不是有效图片",
        ) from exc
    mime_type = detected_mime or content_type or "application/octet-stream"
    if mime_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=400,
            detail=f"{filename} 仅支持 JPG、PNG、WebP、GIF",
        )
    return mime_type, width, height


def _validate_asset_bytes(
    data: bytes,
    *,
    filename: str,
    content_type: str | None,
    pipeline: Mapping[str, Any],
    allowed_mime_types: set[str],
) -> tuple[str, int | None, int | None]:
    if pipeline.get("input_kind") == "pdf":
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail=f"{filename} 为空或超过 25MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail=f"{filename} 不是有效 PDF")
        if "application/pdf" not in allowed_mime_types:
            raise HTTPException(status_code=400, detail="该类目未允许 PDF MIME")
        return "application/pdf", None, None
    if content_type == "application/pdf":
        raise HTTPException(status_code=400, detail="PDF 必须选择 PDF 输入类目")
    mime_type, width, height = _validate_image_bytes(
        data, filename=filename, content_type=content_type
    )
    if mime_type not in allowed_mime_types:
        raise HTTPException(status_code=400, detail="文件 MIME 不在该类目允许范围内")
    return mime_type, width, height


def _relative_upload_name(filename: str | None, *, fallback: str) -> str:
    """Normalize an untrusted display name without echoing absolute paths."""

    raw = (filename or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    is_absolute = (
        path.is_absolute()
        or raw.startswith("//")
        or (len(raw) >= 2 and raw[1] == ":")
        or ".." in path.parts
    )
    parts = [
        part
        for part in path.parts
        if part not in {"", ".", "..", "/"} and not part.endswith(":")
    ]
    if is_absolute and parts:
        parts = parts[-1:]
    normalized = "/".join(parts) or fallback
    return normalized[-500:].lstrip("/") or fallback


def _unsupported_upload_reason(
    filename: str,
    *,
    allowed_suffixes: set[str],
) -> str | None:
    path = PurePosixPath(filename)
    lowered_parts = {part.lower() for part in path.parts}
    if (
        "__macosx" in lowered_parts
        or any(part.startswith(".") for part in path.parts)
        or path.name.lower() in {"thumbs.db", "desktop.ini"}
    ):
        return "隐藏或系统元数据"
    suffix = path.suffix.lower()
    if suffix not in allowed_suffixes:
        return f"当前类目不支持 {suffix or '无扩展名'} 格式"
    return None


def _upload_issue(filename: str, reason: str) -> dict[str, str]:
    return {"filename": filename, "reason": reason}


def _upload_failure_reason(exc: HTTPException) -> str:
    return exc.detail if isinstance(exc.detail, str) else "文件内容不符合当前类目输入合同"


def _no_valid_upload_error(
    *,
    skipped_files: list[dict[str, str]],
    failed_files: list[dict[str, str]],
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "no_valid_files",
            "message": "没有可上传的有效文件",
            "success_count": 0,
            "skipped_count": len(skipped_files),
            "failed_count": len(failed_files),
            "skipped_files": skipped_files,
            "failed_files": failed_files,
        },
    )


def _store_package_asset(
    *,
    db: Session,
    package: MaterialPackage,
    position: int,
    filename: str,
    content_type: str | None,
    data: bytes,
    actor: str,
    category_key: str,
    created_paths: list[Path],
) -> dict[str, Any]:
    normalized_name = _relative_upload_name(
        filename,
        fallback=f"file-{position}",
    )
    profile = _category_profile(db, category_key, require_active=True)
    mime_type, width, height = _validate_asset_bytes(
        data,
        filename=normalized_name,
        content_type=content_type,
        pipeline=_profile_pipeline(profile, db),
        allowed_mime_types=set(json.loads(profile.allowed_mime_types_json or "[]")),
    )
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Asset).where(Asset.sha256 == digest).order_by(Asset.id.desc())
    )
    if existing is not None:
        restored = existing.status == "deleted"
        if existing.category_key != category_key and not restored:
            raise HTTPException(
                status_code=409,
                detail="同一文件已归属其他评测类目，请先在素材管理中调整归属后再上传",
            )
        if restored:
            existing.status = "uploaded"
            existing.category_key = category_key
            append_audit_event(
                db,
                category="materials",
                action="asset_restored_by_upload",
                subject_type="asset",
                subject_id=existing.id,
                actor=actor,
                payload={"package_id": package.id, "sha256": digest},
                event_key=f"asset:{existing.id}:restored:package:{package.id}",
            )
        db.add(
            MaterialPackageItem(
                package_id=package.id,
                asset_id=existing.id,
                original_name=normalized_name,
                duplicate=True,
                position=position,
            )
        )
        return {
            **_asset_payload(existing, display_name=normalized_name),
            "duplicate": True,
            "restored": restored,
        }

    extension = (
        mimetypes.guess_extension(mime_type)
        or Path(normalized_name).suffix
        or ".jpg"
    )
    stored_name = f"{uuid.uuid4().hex}{extension.lower()}"
    stored_path = settings.upload_dir / stored_name
    stored_path.write_bytes(data)
    created_paths.append(stored_path)
    asset = Asset(
        original_name=normalized_name,
        stored_name=stored_name,
        mime_type=mime_type,
        size_bytes=len(data),
        width=width,
        height=height,
        sha256=digest,
        category_key=category_key,
    )
    db.add(asset)
    db.flush()
    db.add(
        MaterialPackageItem(
            package_id=package.id,
            asset_id=asset.id,
            original_name=normalized_name,
            duplicate=False,
            position=position,
        )
    )
    return {
        **_asset_payload(asset, display_name=normalized_name),
        "duplicate": False,
        "restored": False,
    }


def _upload_result(
    package: MaterialPackage,
    uploaded: list[dict[str, Any]],
    *,
    skipped_files: list[dict[str, str]] | None = None,
    failed_files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    skipped_files = skipped_files or []
    failed_files = failed_files or []
    return {
        "items": uploaded,
        "successful_files": [str(item["name"]) for item in uploaded],
        "skipped_files": skipped_files,
        "failed_files": failed_files,
        "summary": {
            "success_count": len(uploaded),
            "skipped_count": len(skipped_files),
            "failed_count": len(failed_files),
        },
        "package": {
            "id": package.id,
            "package_key": package.package_key,
            "name": package.name,
            "source": package.source,
            "category_key": package.category_key,
            "item_count": len(uploaded),
            "unique_asset_count": len({item["id"] for item in uploaded}),
            "duplicate_count": sum(
                1 for item in uploaded if item["duplicate"]
            ),
            "restored_count": sum(
                1 for item in uploaded if item.get("restored")
            ),
            "ignored_count": len(skipped_files),
            "failed_count": len(failed_files),
            "created_by": package.created_by,
            "created_at": package.created_at,
        },
    }


def _cleanup_failed_upload(db: Session, created_paths: list[Path]) -> None:
    db.rollback()
    for path in created_paths:
        path.unlink(missing_ok=True)


@app.post("/api/assets/upload")
async def upload_assets(
    files: list[UploadFile] = File(...),
    package_name: str | None = Form(default=None),
    category_key: str = Form(default="space_image"),
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _category_profile(db, category_key, require_active=True)
    pipeline = _profile_pipeline(profile, db)
    allowed_suffixes = set(pipeline["allowed_suffixes"])
    if not files:
        raise HTTPException(status_code=422, detail="至少选择一个素材")
    if len(files) > MAX_UPLOAD_FILES * 2:
        raise HTTPException(
            status_code=400,
            detail="文件夹条目过多，请移除无关文件或使用 ZIP",
        )
    candidates: list[tuple[UploadFile, str]] = []
    skipped_files: list[dict[str, str]] = []
    for position, upload in enumerate(files, start=1):
        relative_name = _relative_upload_name(
            upload.filename,
            fallback=f"file-{position}",
        )
        unsupported_reason = _unsupported_upload_reason(
            relative_name,
            allowed_suffixes=allowed_suffixes,
        )
        if unsupported_reason:
            skipped_files.append(_upload_issue(relative_name, unsupported_reason))
        else:
            candidates.append((upload, relative_name))
    if len(candidates) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"图片/文件夹单次最多上传 {MAX_UPLOAD_FILES} 个有效文件；更多素材请使用 ZIP",
        )
    if not candidates:
        raise _no_valid_upload_error(
            skipped_files=skipped_files,
            failed_files=[],
        )
    package = MaterialPackage(
        package_key=f"upload:{uuid.uuid4().hex}",
        name=_upload_package_name(
            package_name,
            fallback=(
                "素材包 "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        ),
        source="manual_upload",
        category_key=category_key,
        created_by=user.username,
    )
    db.add(package)
    db.flush()
    uploaded: list[dict[str, Any]] = []
    failed_files: list[dict[str, str]] = []
    created_paths: list[Path] = []
    try:
        for upload, relative_name in candidates:
            try:
                item = _store_package_asset(
                    db=db,
                    package=package,
                    position=len(uploaded) + 1,
                    filename=relative_name,
                    content_type=upload.content_type,
                    data=await upload.read(),
                    actor=user.username,
                    category_key=category_key,
                    created_paths=created_paths,
                )
            except HTTPException as exc:
                failed_files.append(
                    _upload_issue(relative_name, _upload_failure_reason(exc))
                )
                continue
            uploaded.append(item)
        if not uploaded:
            raise _no_valid_upload_error(
                skipped_files=skipped_files,
                failed_files=failed_files,
            )
        append_audit_event(
            db,
            category="materials",
            action="material_package_uploaded",
            subject_type="material_package",
            subject_id=package.id,
            actor=user.username,
            payload={
                "item_count": len(uploaded),
                "duplicate_count": sum(
                    1 for item in uploaded if item["duplicate"]
                ),
                "skipped_count": len(skipped_files),
                "failed_count": len(failed_files),
                "input_mode": "files_or_folder",
            },
            event_key=f"material-package:{package.id}:uploaded",
        )
        db.commit()
    except Exception:
        _cleanup_failed_upload(db, created_paths)
        raise
    return _upload_result(
        package,
        uploaded,
        skipped_files=skipped_files,
        failed_files=failed_files,
    )


def _nas_directory_candidates(source_uri: str, root: Path, max_files: int) -> list[str]:
    """Expand one NAS file/directory into normalized, revalidated URIs."""
    normalized = normalize_nas_uri(source_uri)
    base_path = resolve_nas_uri(normalized, root)
    if base_path.is_file():
        return [normalized]
    if not base_path.is_dir():
        raise NasStorageError("NAS_FILE_MISSING", "NAS 文件或目录不存在")
    base_parts = nas_relative_path(normalized).parts
    candidates: list[str] = []
    for path in base_path.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative_parts = base_parts + path.relative_to(base_path).parts
        candidates.append("nas://maps/" + "/".join(relative_parts))
        if len(candidates) > max_files:
            raise NasStorageError(
                "NAS_IMPORT_LIMIT_EXCEEDED",
                f"NAS 目录超过单次导入上限 {max_files} 个文件",
            )
    return candidates


def _nas_storage_http_error(exc: NasStorageError) -> HTTPException:
    status = 503 if exc.code in {"NAS_MOUNT_UNAVAILABLE", "NAS_MOUNT_INVALID"} else 400
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc)},
    )


@app.post("/api/assets/import-nas")
def import_nas_assets(
    payload: NasAssetImportRequest,
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _category_profile(db, payload.category_key, require_active=True)
    pipeline = _profile_pipeline(profile, db)
    allowed_mimes = set(json.loads(profile.allowed_mime_types_json or "[]"))
    root = settings.nas_maps_root
    if root is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "NAS_MOUNT_UNAVAILABLE", "message": "NAS 只读挂载未配置"},
        )
    try:
        candidates = _nas_directory_candidates(
            payload.source_uri,
            Path(root),
            payload.max_files,
        )
    except NasStorageError as exc:
        raise _nas_storage_http_error(exc) from exc
    if not candidates:
        raise HTTPException(status_code=400, detail="NAS 目录中没有可导入文件")

    package = MaterialPackage(
        package_key=f"nas:{uuid.uuid4().hex}",
        name=_upload_package_name(
            payload.package_name,
            fallback=nas_relative_path(candidates[0]).parts[0]
            if len(candidates) == 1
            else f"NAS 素材包 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        ),
        # The database CHECK constraint intentionally stays backward compatible;
        # audit input_mode identifies the NAS reference without adding a second
        # package-source enum that older deployments cannot read.
        source="production_import",
        category_key=payload.category_key,
        created_by=user.username,
    )
    db.add(package)
    db.flush()
    uploaded: list[dict[str, Any]] = []
    skipped_files: list[dict[str, str]] = []
    failed_files: list[dict[str, str]] = []
    try:
        for source_uri in candidates:
            display_name = source_uri.rsplit("/", 1)[-1]
            suffix = Path(display_name).suffix.lower()
            if suffix not in set(pipeline["allowed_suffixes"]):
                skipped_files.append(_upload_issue(display_name, "当前类目不支持该文件格式"))
                continue
            try:
                info = inspect_nas_file(source_uri, Path(root))
                if info.mime_type not in allowed_mimes:
                    skipped_files.append(
                        _upload_issue(display_name, "文件 MIME 类型与当前评测类目不匹配")
                    )
                    continue
                existing = db.scalar(
                    select(Asset).where(Asset.source_uri == info.uri)
                )
                if existing is not None:
                    if existing.category_key != payload.category_key:
                        failed_files.append(
                            _upload_issue(display_name, "同一 NAS 来源已归属其他评测类目")
                        )
                        continue
                    if existing.sha256 != info.sha256:
                        failed_files.append(
                            _upload_issue(display_name, "NAS 文件哈希已变化，未覆盖原素材")
                        )
                        continue
                    restored = existing.status == "deleted"
                    if restored:
                        existing.status = "uploaded"
                    db.add(
                        MaterialPackageItem(
                            package_id=package.id,
                            asset_id=existing.id,
                            original_name=info.original_name,
                            duplicate=True,
                            position=len(uploaded) + 1,
                        )
                    )
                    uploaded.append(
                        {
                            **_asset_payload(existing, display_name=info.original_name),
                            "duplicate": True,
                            "restored": restored,
                        }
                    )
                    continue

                extension = mimetypes.guess_extension(info.mime_type) or suffix or ".bin"
                uri_key = hashlib.sha256(info.uri.encode("utf-8")).hexdigest()[:8]
                asset = Asset(
                    original_name=info.original_name,
                    stored_name=f"nas-{info.sha256[:32]}-{uri_key}{extension.lower()}",
                    storage_backend="nas_maps",
                    source_uri=info.uri,
                    mime_type=info.mime_type,
                    size_bytes=info.size_bytes,
                    width=info.width,
                    height=info.height,
                    sha256=info.sha256,
                    category_key=payload.category_key,
                )
                db.add(asset)
                db.flush()
                record_asset_version(
                    db,
                    source_system="nas_maps",
                    source_content_id=info.uri,
                    source_version=f"sha256:{info.sha256}",
                    asset=asset,
                    occurred_at=datetime.now(timezone.utc),
                )
                db.add(
                    MaterialPackageItem(
                        package_id=package.id,
                        asset_id=asset.id,
                        original_name=info.original_name,
                        duplicate=False,
                        position=len(uploaded) + 1,
                    )
                )
                uploaded.append(
                    {
                        **_asset_payload(asset, display_name=info.original_name),
                        "duplicate": False,
                        "restored": False,
                    }
                )
            except NasStorageError as exc:
                failed_files.append(_upload_issue(display_name, f"{exc.code}: {exc}"))
        if not uploaded:
            db.rollback()
            raise _no_valid_upload_error(
                skipped_files=skipped_files,
                failed_files=failed_files,
            )
        append_audit_event(
            db,
            category="materials",
            action="material_package_imported",
            subject_type="material_package",
            subject_id=package.id,
            actor=user.username,
            payload={
                "item_count": len(uploaded),
                "duplicate_count": sum(1 for item in uploaded if item["duplicate"]),
                "skipped_count": len(skipped_files),
                "failed_count": len(failed_files),
                "input_mode": "nas_reference",
                "source_uri": normalize_nas_uri(payload.source_uri),
                "storage_backend": "nas_maps",
            },
            event_key=f"material-package:{package.id}:nas-imported",
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _upload_result(
        package,
        uploaded,
        skipped_files=skipped_files,
        failed_files=failed_files,
    )


@app.post("/api/material-packages/import-archive")
async def import_material_package_archive(
    archive: UploadFile = File(...),
    package_name: str | None = Form(default=None),
    category_key: str = Form(default="space_image"),
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _category_profile(db, category_key, require_active=True)
    pipeline = _profile_pipeline(profile, db)
    allowed_suffixes = set(pipeline["allowed_suffixes"])
    filename = archive.filename or "archive.zip"
    if Path(filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="压缩包仅支持 ZIP 格式")
    await archive.seek(0)
    if not zipfile.is_zipfile(archive.file):
        raise HTTPException(status_code=400, detail="上传文件不是有效 ZIP 压缩包")
    await archive.seek(0)
    package = MaterialPackage(
        package_key=f"archive:{uuid.uuid4().hex}",
        name=_upload_package_name(
            package_name,
            fallback=Path(filename).stem or "ZIP 素材包",
        ),
        source="manual_upload",
        category_key=category_key,
        created_by=user.username,
    )
    db.add(package)
    db.flush()
    uploaded: list[dict[str, Any]] = []
    created_paths: list[Path] = []
    skipped_files: list[dict[str, str]] = []
    failed_files: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(archive.file) as bundle:
            archive_infos = bundle.infolist()
            if len(archive_infos) > MAX_ARCHIVE_IMAGES * 2:
                raise HTTPException(
                    status_code=400,
                    detail="ZIP 文件条目过多，请拆分素材包",
                )
            for info in archive_infos:
                normalized_path = info.filename.replace("\\", "/")
                path = PurePosixPath(normalized_path)
                if path.is_absolute() or ".." in path.parts:
                    display_name = _relative_upload_name(
                        normalized_path,
                        fallback="archive-entry",
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"ZIP 包含不安全路径：{display_name}",
                    )
            file_infos = [item for item in archive_infos if not item.is_dir()]
            image_infos: list[zipfile.ZipInfo] = []
            total_uncompressed = 0
            for info in file_infos:
                normalized_path = info.filename.replace("\\", "/")
                display_name = _relative_upload_name(
                    normalized_path,
                    fallback="archive-entry",
                )
                if info.flag_bits & 0x1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"ZIP 内文件不能加密：{display_name}",
                    )
                ratio = info.file_size / max(info.compress_size, 1)
                if (
                    info.file_size > 1024 * 1024
                    and ratio > MAX_ARCHIVE_COMPRESSION_RATIO
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=f"ZIP 压缩比异常：{display_name}",
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail="ZIP 解压后总大小超过 30GB，请拆分素材包",
                    )
                unsupported_reason = _unsupported_upload_reason(
                    display_name,
                    allowed_suffixes=allowed_suffixes,
                )
                if unsupported_reason:
                    skipped_files.append(
                        _upload_issue(display_name, unsupported_reason)
                    )
                    continue
                if info.file_size <= 0 or info.file_size > MAX_UPLOAD_BYTES:
                    failed_files.append(
                        _upload_issue(display_name, "文件为空或超过 25MB")
                    )
                    continue
                image_infos.append(info)
            if not image_infos:
                raise _no_valid_upload_error(
                    skipped_files=skipped_files,
                    failed_files=failed_files,
                )
            if len(image_infos) > MAX_ARCHIVE_IMAGES:
                raise HTTPException(
                    status_code=400,
                    detail=f"单个 ZIP 最多包含 {MAX_ARCHIVE_IMAGES} 张图片",
                )
            for info in image_infos:
                relative_name = _relative_upload_name(
                    info.filename,
                    fallback="archive-entry",
                )
                with bundle.open(info) as source:
                    data = source.read(MAX_UPLOAD_BYTES + 1)
                try:
                    item = _store_package_asset(
                        db=db,
                        package=package,
                        position=len(uploaded) + 1,
                        filename=relative_name,
                        content_type=mimetypes.guess_type(info.filename)[0],
                        data=data,
                        actor=user.username,
                        category_key=category_key,
                        created_paths=created_paths,
                    )
                except HTTPException as exc:
                    failed_files.append(
                        _upload_issue(relative_name, _upload_failure_reason(exc))
                    )
                    continue
                uploaded.append(item)
            if not uploaded:
                raise _no_valid_upload_error(
                    skipped_files=skipped_files,
                    failed_files=failed_files,
                )
        append_audit_event(
            db,
            category="materials",
            action="material_package_uploaded",
            subject_type="material_package",
            subject_id=package.id,
            actor=user.username,
            payload={
                "item_count": len(uploaded),
                "duplicate_count": sum(
                    1 for item in uploaded if item["duplicate"]
                ),
                "ignored_count": len(skipped_files),
                "failed_count": len(failed_files),
                "input_mode": "zip_archive",
            },
            event_key=f"material-package:{package.id}:uploaded",
        )
        db.commit()
    except (zipfile.BadZipFile, RuntimeError) as exc:
        _cleanup_failed_upload(db, created_paths)
        raise HTTPException(status_code=400, detail="ZIP 压缩包无法读取") from exc
    except Exception:
        _cleanup_failed_upload(db, created_paths)
        raise
    return _upload_result(
        package,
        uploaded,
        skipped_files=skipped_files,
        failed_files=failed_files,
    )


def _asset_evaluation_status(
    db: Session,
    asset: Asset,
    *,
    prompt_id: int | None,
    prompt_a_id: int | None,
    prompt_b_id: int | None,
) -> str:
    jobs = db.scalars(
        select(EvaluationJob)
        .where(
            EvaluationJob.asset_id == asset.id,
            EvaluationJob.baseline_regression_item_id.is_(None),
        )
        .order_by(EvaluationJob.created_at.desc(), EvaluationJob.id.desc())
    ).all()

    def is_current(job: EvaluationJob) -> bool:
        if prompt_id is not None:
            return job.prompt_a_id == prompt_id and job.prompt_b_id is None
        if prompt_a_id is not None or prompt_b_id is not None:
            return (
                job.prompt_a_id == prompt_a_id
                and job.prompt_b_id == prompt_b_id
            )
        return False

    current_jobs = [job for job in jobs if is_current(job)]
    if any(job.status == "processing" for job in current_jobs):
        return "running"
    if any(job.status in {"queued", "paused"} for job in current_jobs):
        return "queued"
    if any(job.status == "completed" for job in current_jobs):
        return "evaluated_current"
    if any(job.status in {"failed", "canceled", "cancelled"} for job in current_jobs):
        return "failed"
    if jobs:
        return "evaluated_old"
    return "not_evaluated"


@app.post("/api/material-packages")
def create_material_package_from_assets(
    payload: MaterialPackageCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset_ids = payload.asset_ids
    assets = db.scalars(
        select(Asset).where(
            Asset.id.in_(asset_ids),
            Asset.status != "deleted",
        )
    ).all()
    assets_by_id = {asset.id: asset for asset in assets}
    if set(assets_by_id) != set(asset_ids):
        raise HTTPException(
            status_code=404,
            detail="部分素材不存在或已删除，请刷新后重试",
        )
    if any(asset.category_key != payload.category_key for asset in assets_by_id.values()):
        raise HTTPException(status_code=422, detail="素材所属通道与素材包类目不一致")
    allowed_mimes = set(
        json.loads(_category_profile(db, payload.category_key, require_active=True).allowed_mime_types_json or "[]")
    )
    if any(asset.mime_type not in allowed_mimes for asset in assets_by_id.values()):
        raise HTTPException(status_code=422, detail="素材 MIME 类型与评测类目不匹配")
    package = MaterialPackage(
        package_key=f"selection:{uuid.uuid4().hex}",
        name=payload.name.strip(),
        source="manual_upload",
        category_key=payload.category_key,
        created_by=user.username,
    )
    db.add(package)
    db.flush()
    for position, asset_id in enumerate(asset_ids, start=1):
        asset = assets_by_id[asset_id]
        db.add(
            MaterialPackageItem(
                package_id=package.id,
                asset_id=asset.id,
                original_name=asset.original_name,
                duplicate=False,
                position=position,
            )
        )
    append_audit_event(
        db,
        category="materials",
        action="material_package_created_from_selection",
        subject_type="material_package",
        subject_id=package.id,
        actor=user.username,
        payload={
            "item_count": len(asset_ids),
            "asset_ids": asset_ids,
        },
        event_key=f"material-package:{package.id}:selection-created",
    )
    db.commit()
    return {
        "id": package.id,
        "package_key": package.package_key,
        "name": package.name,
        "source": package.source,
        "category_key": package.category_key,
        "item_count": len(asset_ids),
        "unique_asset_count": len(asset_ids),
        "active_asset_count": len(asset_ids),
        "removed_asset_count": 0,
        "duplicate_count": 0,
        "created_by": package.created_by,
        "created_at": package.created_at,
        "status_summary": {
            status: 0
            for status in (
                "not_evaluated",
                "evaluated_old",
                "evaluated_current",
                "queued",
                "running",
                "failed",
            )
        },
    }


@app.get("/api/material-packages")
def list_material_packages(
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    prompt_id: int | None = None,
    prompt_a_id: int | None = None,
    prompt_b_id: int | None = None,
    category_key: str | None = None,
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(MaterialPackage).order_by(
        MaterialPackage.created_at.desc(), MaterialPackage.id.desc()
    )
    statement = statement.where(MaterialPackage.status != "deleted")
    if created_from is not None:
        statement = statement.where(MaterialPackage.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(MaterialPackage.created_at <= created_to)
    if category_key is not None:
        _category_profile(db, category_key)
        statement = statement.where(MaterialPackage.category_key == category_key)
    packages = db.scalars(statement.limit(min(max(limit, 1), 500))).all()
    items = []
    for package in packages:
        status_summary: Counter[str] = Counter()
        active_package_items = [
            item for item in package.items if item.asset.status != "deleted"
        ]
        for package_item in active_package_items:
            status_summary[
                _asset_evaluation_status(
                    db,
                    package_item.asset,
                    prompt_id=prompt_id,
                    prompt_a_id=prompt_a_id,
                    prompt_b_id=prompt_b_id,
                )
            ] += 1
        items.append(
            {
                "id": package.id,
                "package_key": package.package_key,
                "name": package.name,
                "source": package.source,
                "category_key": package.category_key,
                "status": package.status,
                "item_count": len(package.items),
                "unique_asset_count": len(
                    {item.asset_id for item in package.items}
                ),
                "active_asset_count": len(
                    {item.asset_id for item in active_package_items}
                ),
                "removed_asset_count": len(
                    {
                        item.asset_id
                        for item in package.items
                        if item.asset.status == "deleted"
                    }
                ),
                "duplicate_count": sum(
                    1 for item in package.items if item.duplicate
                ),
                "created_by": package.created_by,
                "created_at": package.created_at,
                "status_summary": {
                    status: status_summary.get(status, 0)
                    for status in (
                        "not_evaluated",
                        "evaluated_old",
                        "evaluated_current",
                        "queued",
                        "running",
                        "failed",
                    )
                },
            }
        )
    return {"items": items}


@app.get("/api/assets")
def list_assets(
    limit: int = 100,
    offset: int = 0,
    package_id: int | None = None,
    category_key: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    prompt_id: int | None = None,
    prompt_a_id: int | None = None,
    prompt_b_id: int | None = None,
    exclude_evaluated_current: bool = False,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(Asset).where(Asset.status != "deleted")
    if category_key is not None:
        _category_profile(db, category_key)
        statement = statement.where(Asset.category_key == category_key)
    if package_id is not None:
        statement = statement.join(
            MaterialPackageItem,
            MaterialPackageItem.asset_id == Asset.id,
        ).where(MaterialPackageItem.package_id == package_id)
    if created_from is not None:
        statement = statement.where(Asset.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(Asset.created_at <= created_to)
    ordered_statement = statement.order_by(Asset.created_at.desc(), Asset.id.desc())
    if exclude_evaluated_current:
        assets = db.scalars(ordered_statement).unique().all()
        total = None
    else:
        id_statement = statement.with_only_columns(Asset.id).distinct()
        total = db.scalar(
            select(func.count()).select_from(id_statement.subquery())
        ) or 0
        assets = db.scalars(
            ordered_statement
            .offset(max(0, offset))
            .limit(min(1000, max(1, limit)))
        ).unique().all()
    package_names: dict[int, str] = {}
    if package_id is not None and assets:
        package_items = db.scalars(
            select(MaterialPackageItem)
            .where(
                MaterialPackageItem.package_id == package_id,
                MaterialPackageItem.asset_id.in_([asset.id for asset in assets]),
            )
            .order_by(MaterialPackageItem.position.asc())
        ).all()
        for package_item in package_items:
            package_names.setdefault(
                package_item.asset_id,
                package_item.original_name,
            )
    payloads = []
    for asset in assets:
        evaluation_status = _asset_evaluation_status(
            db,
            asset,
            prompt_id=prompt_id,
            prompt_a_id=prompt_a_id,
            prompt_b_id=prompt_b_id,
        )
        if exclude_evaluated_current and evaluation_status == "evaluated_current":
            continue
        payloads.append(
            {
                **_asset_payload(
                    asset,
                    display_name=package_names.get(asset.id),
                ),
                "evaluation_status": evaluation_status,
            }
        )
    if total is not None:
        return {"items": payloads, "total": total}
    total = len(payloads)
    return {
        "items": payloads[max(0, offset):max(0, offset) + min(1000, limit)],
        "total": total,
    }


@app.get("/api/assets/{asset_id}/file")
def asset_file(
    asset_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    try:
        path = resolve_asset_path(asset, settings)
    except NasStorageError as exc:
        if exc.code == "NAS_FILE_MISSING":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise _nas_storage_http_error(exc) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件缺失")
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)


@app.get("/api/assets/{asset_id}")
def asset_detail(
    asset_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    return _asset_payload(asset)


@app.patch("/api/assets/{asset_id}/category")
def update_asset_category(
    asset_id: int,
    payload: AssetCategoryUpdateRequest,
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    _category_profile(db, payload.category_key, require_active=True)
    active_job = db.scalar(
        select(EvaluationJob.id).where(
            EvaluationJob.asset_id == asset.id,
            EvaluationJob.status.in_(("queued", "processing", "paused")),
        ).limit(1)
    )
    if active_job is not None:
        raise HTTPException(status_code=409, detail="素材仍有排队或运行中的任务，暂不能修改所属通道")
    previous = asset.category_key
    asset.category_key = payload.category_key
    append_audit_event(
        db, category="materials", action="asset_category_updated", subject_type="asset",
        subject_id=asset.id, actor=user.username,
        payload={"from": previous, "to": payload.category_key},
        event_key=f"asset:{asset.id}:category:{asset.category_key}",
    )
    db.commit()
    return _asset_payload(asset)


@app.delete("/api/assets/{asset_id}")
def delete_asset(
    asset_id: int,
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    if asset.status == "deleted":
        return {
            "id": asset.id,
            "deleted": True,
            "history_retained": True,
        }
    active_job = db.scalar(
        select(EvaluationJob.id)
        .where(
            EvaluationJob.asset_id == asset.id,
            EvaluationJob.status.in_(("queued", "processing", "paused")),
        )
        .limit(1)
    )
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail="素材仍有排队或运行中的任务，请先取消任务再删除",
        )
    previous_status = asset.status
    asset.status = "deleted"
    append_audit_event(
        db,
        category="materials",
        action="asset_deleted",
        subject_type="asset",
        subject_id=asset.id,
        actor=user.username,
        payload={
            "previous_status": previous_status,
            "history_retained": True,
            "binary_retained": True,
        },
        event_key=f"asset:{asset.id}:deleted",
    )
    db.commit()
    return {
        "id": asset.id,
        "deleted": True,
        "history_retained": True,
    }


def _soft_delete_assets(
    db: Session,
    *,
    asset_ids: list[int],
    actor: str,
    source: str,
) -> dict[str, Any]:
    assets = db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()
    if len({asset.id for asset in assets}) != len(set(asset_ids)):
        raise HTTPException(status_code=404, detail="部分素材不存在，请刷新后重试")
    active_ids = [asset.id for asset in assets if asset.status != "deleted"]
    blocked = db.scalars(
        select(EvaluationJob.asset_id).where(
            EvaluationJob.asset_id.in_(active_ids),
            EvaluationJob.status.in_(("queued", "processing", "paused")),
        ).distinct()
    ).all()
    if blocked:
        raise HTTPException(status_code=409, detail={"message": "部分素材仍有排队或运行中的任务，请先取消任务", "asset_ids": blocked})
    for asset in assets:
        if asset.status == "deleted":
            continue
        previous = asset.status
        asset.status = "deleted"
        append_audit_event(
            db, category="materials", action="asset_deleted", subject_type="asset",
            subject_id=asset.id, actor=actor,
            payload={"previous_status": previous, "history_retained": True, "binary_retained": True, "source": source},
            event_key=f"asset:{asset.id}:deleted:{source}",
        )
    db.commit()
    return {"deleted": len(active_ids), "already_deleted": len(assets) - len(active_ids), "asset_ids": active_ids, "history_retained": True}


@app.post("/api/assets/bulk-delete")
def bulk_delete_assets(
    payload: AssetBulkDeleteRequest,
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _soft_delete_assets(db, asset_ids=payload.asset_ids, actor=user.username, source="bulk")


@app.delete("/api/material-packages/{package_id}")
def delete_material_package(
    package_id: int,
    user: User = Depends(_permission_user("assets:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    package = db.get(MaterialPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="素材包不存在")
    if package.status == "deleted":
        return {"package_id": package.id, "deleted": True, "deleted_assets": 0, "history_retained": True}
    asset_ids = list(dict.fromkeys(item.asset_id for item in package.items))
    if not asset_ids:
        package.status = "deleted"
        append_audit_event(
            db,
            category="materials",
            action="material_package_deleted",
            subject_type="material_package",
            subject_id=package.id,
            actor=user.username,
            payload={"asset_ids": [], "history_retained": True},
            event_key=f"material-package:{package.id}:deleted",
        )
        db.commit()
        return {"package_id": package.id, "deleted": True, "deleted_assets": 0, "history_retained": True}
    result = _soft_delete_assets(db, asset_ids=asset_ids, actor=user.username, source=f"package:{package_id}")
    package.status = "deleted"
    append_audit_event(
        db, category="materials", action="material_package_deleted", subject_type="material_package",
        subject_id=package.id, actor=user.username,
        payload={"asset_ids": asset_ids, "history_retained": True},
        event_key=f"material-package:{package.id}:deleted",
    )
    db.commit()
    return {"package_id": package.id, "deleted": True, **result}


def _required_active_v3_bundle(db: Session, category_key: str) -> dict[str, Any]:
    """Resolve the sole authoritative contract for every newly created run."""

    try:
        return v3_authoritative_category(db, category_key)
    except V3AuthoritativeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _required_baseline_v3_bundle(
    db: Session,
    category_key: str,
    candidate_revision_id: int | None,
) -> dict[str, Any]:
    """Freeze the active contract or one current candidate-chain revision."""

    active_bundle = _required_active_v3_bundle(db, category_key)
    if candidate_revision_id is None:
        return active_bundle
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key,
            CategoryEvaluationV3Config.status == "active",
        )
    )
    if projected is None or projected.projected_revision_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_revision_projection_missing",
                "message": "现役合同缺少可核验的修订投影，不能冻结候选回归",
            },
        )
    candidate = db.get(CategoryEvaluationV3Revision, candidate_revision_id)
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "candidate_revision_not_found",
                "message": "候选修订不存在",
            },
        )
    if candidate.category_key != category_key:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "candidate_revision_category_mismatch",
                "message": "候选修订属于其他类目",
            },
        )
    if candidate.status != "candidate":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_revision_status_invalid",
                "message": "只有未发布候选修订可以用于候选回归",
            },
        )
    # Revisions link child -> parent, so an activated revision sits at the tip of
    # its chain and is never an ancestor of a candidate. Walking upward from the
    # candidate to the active revision therefore fails for every candidate once a
    # new revision is published. Lineage is now recorded for traceability instead
    # of blocking the run: the operator keeps manual control over which graded
    # matcher version a regression exercises.
    active_chain: set[int] = set()
    cursor = db.get(
        CategoryEvaluationV3Revision,
        projected.projected_revision_id,
    )
    while cursor is not None and cursor.id not in active_chain:
        active_chain.add(cursor.id)
        if cursor.parent_revision_id is None:
            break
        cursor = db.get(
            CategoryEvaluationV3Revision,
            cursor.parent_revision_id,
        )

    lineage = "diverged"
    ancestor: CategoryEvaluationV3Revision | None = candidate
    seen: set[int] = set()
    while ancestor is not None and ancestor.id not in seen:
        if ancestor.id in active_chain:
            lineage = "on_active_chain"
            break
        seen.add(ancestor.id)
        if ancestor.parent_revision_id is None:
            break
        parent = db.get(
            CategoryEvaluationV3Revision,
            ancestor.parent_revision_id,
        )
        if parent is None or parent.category_key != category_key:
            break
        ancestor = parent

    frozen = revision_bundle(candidate)
    frozen.update(
        {
            "config_revision": candidate.revision,
            "candidate_revision_id": candidate.id,
            "base_projected_revision_id": projected.projected_revision_id,
            "base_projected_contract_hash": projected.contract_hash,
            "candidate_lineage": lineage,
        }
    )
    return frozen


@app.get("/api/evaluations")
def list_evaluations(
    limit: int = 100,
    offset: int = 0,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    results = db.scalars(
        select(EvaluationResult)
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .offset(max(0, offset))
        .limit(min(1000, limit))
    ).all()
    sampling = _review_sampling_decisions(db)
    total = db.scalar(select(func.count()).select_from(EvaluationResult)) or 0
    return {
        "items": [
            _evaluation_asset_payload(result, sampling.get(result.id)) for result in results
        ],
        "total": total,
    }


@app.get("/api/evaluations/{evaluation_id}")
def evaluation_detail(
    evaluation_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = db.get(EvaluationResult, evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    sampling = _review_sampling_decisions(db)
    return _evaluation_asset_payload(result, sampling.get(result.id))


def _enqueue_jobs(
    payload: EnqueueRequest,
    db: Session,
    *,
    require_configuration: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    profile = _category_profile(db, payload.category_key, require_active=True)
    v3_bundle = _required_active_v3_bundle(db, payload.category_key)
    pipeline = _profile_pipeline(profile)
    assets = db.scalars(
        select(Asset).where(
            Asset.id.in_(payload.asset_ids),
            Asset.status != "deleted",
        )
    ).all()
    if len(assets) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=404, detail="部分图片不存在或已删除")
    if any(asset.category_key != payload.category_key for asset in assets):
        raise HTTPException(status_code=422, detail="所选素材包含其他评测类目，请先调整素材所属通道")
    allowed_mimes = set(json.loads(profile.allowed_mime_types_json or "[]"))
    if any(asset.mime_type not in allowed_mimes for asset in assets):
        raise HTTPException(status_code=422, detail="素材 MIME 类型与评测类目不匹配")

    def selected_prompt(stage: str, prompt_id: int | None) -> PromptVersion:
        if prompt_id is not None:
            prompt = db.get(PromptVersion, prompt_id)
            if (
                not prompt
                or prompt.stage != stage
                or prompt.category_key != payload.category_key
                or prompt.status == "archived"
                or prompt.pipeline_scope not in {"full_pipeline", "shared"}
            ):
                raise HTTPException(status_code=400, detail=f"提示词 {stage} 版本无效")
            return prompt
        prompt = db.scalar(
            select(PromptVersion)
            .where(
                PromptVersion.category_key == payload.category_key,
                PromptVersion.stage == stage,
                PromptVersion.status == "published",
                PromptVersion.pipeline_scope.in_(("full_pipeline", "shared")),
            )
            .order_by(PromptVersion.created_at.desc())
            .limit(1)
        )
        if not prompt:
            raise HTTPException(status_code=400, detail=f"没有可用的提示词 {stage} 发布版本")
        return prompt

    prompt_mode = pipeline["prompt_mode"]
    explicit_pair = payload.prompt_a_id is not None or payload.prompt_b_id is not None
    if prompt_mode == "single" and explicit_pair:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_mode_mismatch",
                "message": "该类目固定使用单提示词，请选择单提示词版本。",
            },
        )
    if prompt_mode == "ab" and payload.prompt_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_mode_mismatch",
                "message": "该类目固定使用 A/B 提示词，请同时选择 A 与 B 版本。",
            },
        )
    profile_single_id = (
        profile.prompt_a_id
        if not explicit_pair
        and payload.prompt_id is None
        and profile.prompt_a_id is not None
        and profile.prompt_b_id is None
        else None
    )
    selected_single_id = payload.prompt_id or profile_single_id
    selected_a_id = (
        payload.prompt_a_id
        if explicit_pair
        else profile.prompt_a_id
    )
    selected_b_id = (
        payload.prompt_b_id
        if explicit_pair
        else profile.prompt_b_id
    )
    if prompt_mode != "follow" and selected_single_id is None and selected_a_id is None:
        raise HTTPException(
            status_code=409,
            detail="请先为该评测类目绑定专属提示词",
        )
    if prompt_mode == "single" and selected_single_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_mode_mismatch",
                "message": "该类目固定使用单提示词，但冻结方案不是单提示词合同。",
            },
        )
    if prompt_mode == "ab" and (
        selected_single_id is not None
        or selected_a_id is None
        or selected_b_id is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prompt_mode_mismatch",
                "message": "该类目固定使用 A/B 提示词，但冻结方案缺少完整 A/B 合同。",
            },
        )
    single_prompt = db.get(PromptVersion, selected_single_id) if selected_single_id else None
    if selected_single_id and (
        single_prompt is None
        or single_prompt.stage != "A"
        or single_prompt.category_key != payload.category_key
    ):
        raise HTTPException(status_code=400, detail="单提示词版本无效")
    prompt_a = None if single_prompt else selected_prompt("A", selected_a_id)
    prompt_b = None if single_prompt else selected_prompt("B", selected_b_id)
    selected_rubrics = {
        prompt.rubric_version
        for prompt in (single_prompt, prompt_a, prompt_b)
        if prompt is not None
    }
    if selected_rubrics != {profile.rubric_version}:
        raise HTTPException(
            status_code=409,
            detail="类目 rubric 与所选提示词版本不一致",
        )
    frozen_prompt_a_id = single_prompt.id if single_prompt else prompt_a.id
    frozen_prompt_b_id = None if single_prompt else prompt_b.id
    main_binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "evaluation_main", ModelNodeBinding.enabled.is_(True), ModelNodeBinding.category_key == payload.category_key))
    if main_binding is None:
        main_binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "evaluation_main", ModelNodeBinding.enabled.is_(True), ModelNodeBinding.category_key.is_(None)))
    selected_model = (
        db.get(ModelConfig, profile.model_config_id)
        if profile.model_config_id is not None
        else main_binding.model if main_binding is not None and main_binding.model.active
        else db.scalar(
            select(ModelConfig)
            .where(ModelConfig.active.is_(True))
            .order_by(ModelConfig.id.asc())
        )
    )
    if selected_model is None and require_configuration:
        raise HTTPException(
            status_code=409,
            detail="类目冻结方案缺少已启用的主评测模型",
        )
    if selected_model is None:
        selected_model = ModelConfig(active=True)
        db.add(selected_model)
        db.flush()
    pdf_binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "pdf_summary", ModelNodeBinding.enabled.is_(True), ModelNodeBinding.category_key == payload.category_key))
    if pdf_binding is None:
        pdf_binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "pdf_summary", ModelNodeBinding.enabled.is_(True), ModelNodeBinding.category_key.is_(None)))
    summary_required = "document.multimodal_summary" in active_modules(pipeline)
    pdf_summary_model = (
        pdf_binding.model
        if summary_required and pdf_binding is not None and pdf_binding.model.active
        else selected_model if summary_required else None
    )
    category_profile_snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=frozen_prompt_a_id,
        prompt_b_id=frozen_prompt_b_id,
        model_config=selected_model,
        pdf_summary_model_config=pdf_summary_model,
        v3_authoritative_bundle=v3_bundle,
    )
    jobs = []
    queue_class = (
        "interactive"
        if payload.manual_recheck
        else payload.queue_class or "production_batch"
    )
    batch_key = f"enqueue:{uuid.uuid4().hex}"
    for asset in assets:
        job = EvaluationJob(
            asset_id=asset.id,
            category_key=payload.category_key,
            category_profile_snapshot_json=category_profile_snapshot,
            prompt_a_id=frozen_prompt_a_id,
            prompt_b_id=frozen_prompt_b_id,
            queue_class=queue_class,
            origin_queue_class=queue_class,
            batch_key=batch_key,
        )
        asset.status = "queued"
        db.add(job)
        db.flush()
        jobs.append(job.id)
    if commit:
        db.commit()
    return {
        "job_ids": jobs,
        "batch_key": batch_key,
        "queue_class": queue_class,
        "category_key": payload.category_key,
        "category_profile_snapshot": category_profile_snapshot,
    }


@app.post("/api/jobs/enqueue")
def enqueue_jobs(
    payload: EnqueueRequest,
    _user: User = Depends(_permission_user("jobs:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = _enqueue_jobs(payload, db)
    return {
        key: value
        for key, value in result.items()
        if key != "category_profile_snapshot"
    }


def _enqueue_production_assets(
    db: Session,
    asset_ids: list[int],
    category_key: str,
) -> dict[str, Any]:
    return _enqueue_jobs(
        EnqueueRequest(
            asset_ids=asset_ids,
            category_key=category_key,
            queue_class="production_batch",
        ),
        db,
        require_configuration=True,
        commit=False,
    )


app.include_router(
    build_evaluation_production_router(
        current_user,
        _permission_user("jobs:write"),
        _enqueue_production_assets,
    )
)


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    jobs = db.scalars(
        select(EvaluationJob).order_by(EvaluationJob.created_at.desc()).limit(min(limit, 200))
    ).all()
    result_versions = {
        result.job_id: result
        for result in db.scalars(
            select(EvaluationResult).where(EvaluationResult.job_id.in_([job.id for job in jobs]))
        ).all()
    } if jobs else {}
    return {
        "items": [
            {
                "id": job.id,
                "asset_id": job.asset_id,
                "asset_name": job.asset.original_name,
                "category_key": job.category_key,
                "prompt_a_version": (
                    job.prompt_a.version if job.prompt_a and job.prompt_b else
                    result_versions[job.id].prompt_a_version if job.id in result_versions else None
                ),
                "prompt_b_version": (
                    job.prompt_b.version if job.prompt_b else
                    result_versions[job.id].prompt_b_version if job.id in result_versions else None
                ),
                "prompt_version": (
                    job.prompt_a.version if job.prompt_a and not job.prompt_b else None
                ),
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "attempts": job.attempts,
                "queue_class": job.queue_class,
                "origin_queue_class": job.origin_queue_class,
                "parent_job_id": job.parent_job_id,
                "technical_attempt": job.technical_attempt,
                "technical_error_type": job.technical_error_type,
                "retry_after_at": job.retry_after_at,
                "batch_key": job.batch_key,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]
    }


def _evaluation_control(db: Session) -> EvaluationControl:
    control = db.get(EvaluationControl, 1)
    if control is None:
        control = EvaluationControl(id=1)
        db.add(control)
        db.flush()
    return control


@app.get("/api/jobs/control")
def get_job_control(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    counts = dict(
        db.execute(
            select(EvaluationJob.status, func.count(EvaluationJob.id)).group_by(EvaluationJob.status)
        ).all()
    )
    return {
        "paused": control.paused,
        "queued_count": counts.get("queued", 0),
        "processing_count": counts.get("processing", 0),
        "paused_count": counts.get("paused", 0),
        "active_count": sum(counts.get(status, 0) for status in ("queued", "processing", "paused")),
        "updated_at": control.updated_at,
    }


class JobScopeRequest(BaseModel):
    """按 run 收窄操作范围；两个字段都为空时退回全局（保持旧行为）。"""

    baseline_run_ids: list[int] | None = None
    prompt_run_ids: list[int] | None = None


def _scoped_job_conditions(
    baseline_run_ids: list[int],
    prompt_run_ids: list[int],
) -> list[Any]:
    """把 run id 翻译成 EvaluationJob 的过滤条件（基线回归与提示词回归各一条）。"""
    conditions: list[Any] = []
    if baseline_run_ids:
        conditions.append(
            EvaluationJob.baseline_regression_item_id.in_(
                select(BaselineRegressionItem.id).where(
                    BaselineRegressionItem.run_id.in_(baseline_run_ids)
                )
            )
        )
    if prompt_run_ids:
        conditions.append(
            EvaluationJob.regression_item_id.in_(
                select(PromptRegressionItem.id).where(
                    PromptRegressionItem.run_id.in_(prompt_run_ids)
                )
            )
        )
    return conditions


def _collect_scoped_jobs(
    db: Session,
    status_filter: Any,
    conditions: list[Any],
) -> list[EvaluationJob]:
    """按多个作用域条件取 job 并去重（一个 job 只可能属于其中一类，但不依赖这个前提）。"""
    jobs: list[EvaluationJob] = []
    seen: set[int] = set()
    for condition in conditions:
        for job in db.scalars(select(EvaluationJob).where(status_filter, condition)).all():
            if job.id not in seen:
                seen.add(job.id)
                jobs.append(job)
    return jobs


@app.post("/api/jobs/control/pause")
def pause_all_jobs(
    payload: JobScopeRequest | None = None,
    _user: User = Depends(_permission_user("jobs:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    baseline_run_ids = list(payload.baseline_run_ids or []) if payload else []
    prompt_run_ids = list(payload.prompt_run_ids or []) if payload else []
    scoped = bool(baseline_run_ids or prompt_run_ids)
    # 按 run 暂停时不动全局开关，否则会顺带把其他 run 一起冻住。
    if not scoped:
        control.paused = True

    # 只暂停 queued。processing 的 job 模型调用已经在飞、也已经计费：把它拍成
    # paused 会让 worker 侧的 status == "processing" 守卫使后续进度与完成写入
    # 全部静默 no-op，而 EvaluationResult 早已落库；resume 再把它打回 queued，
    # 同一素材就被重新评测一次，产生第二条结果和第二次 API 计费。
    # 让进行中的任务自然收尾——全局暂停已由 control.paused 阻止领取新任务，
    # 按 run 暂停则由这些 job 自己的 paused 状态阻止。
    still_running = 0
    if scoped:
        conditions = _scoped_job_conditions(baseline_run_ids, prompt_run_ids)
        jobs = _collect_scoped_jobs(db, EvaluationJob.status == "queued", conditions)
        for job in jobs:
            job.status = "paused"
            job.stage = "paused"
            job.worker_id = None
        affected = len(jobs)
        still_running = len(
            _collect_scoped_jobs(db, EvaluationJob.status == "processing", conditions)
        )
    else:
        result = db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.status == "queued")
            .values(status="paused", stage="paused", worker_id=None)
        )
        affected = int(result.rowcount or 0)
        still_running = int(
            db.scalar(
                select(func.count(EvaluationJob.id)).where(
                    EvaluationJob.status == "processing"
                )
            )
            or 0
        )
    db.commit()
    return {
        "ok": True,
        "affected": affected,
        "still_running": still_running,
        "scope": "runs" if scoped else "global",
    }


@app.post("/api/jobs/control/resume")
def resume_all_jobs(
    payload: JobScopeRequest | None = None,
    _user: User = Depends(_permission_user("jobs:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    baseline_run_ids = list(payload.baseline_run_ids or []) if payload else []
    prompt_run_ids = list(payload.prompt_run_ids or []) if payload else []
    scoped = bool(baseline_run_ids or prompt_run_ids)
    # 按 run 恢复不该把全局开关一起打开：别人刻意暂停的队列不能被顺带恢复。
    if not scoped:
        control.paused = False

    status_filter = EvaluationJob.status == "paused"
    if scoped:
        paused_jobs = _collect_scoped_jobs(
            db, status_filter, _scoped_job_conditions(baseline_run_ids, prompt_run_ids)
        )
    else:
        paused_jobs = list(db.scalars(select(EvaluationJob).where(status_filter)).all())
    for job in paused_jobs:
        job.status = "queued"
        job.stage = "waiting"
        job.worker_id = None
        job.asset.status = "queued"
    db.commit()
    return {
        "ok": True,
        "affected": len(paused_jobs),
        "scope": "runs" if scoped else "global",
    }


class JobCancelRequest(JobScopeRequest):
    """按 run 收窄取消范围；两个字段都为空时退回全局取消（保持旧行为）。"""


@app.post("/api/jobs/control/cancel")
def cancel_all_jobs(
    payload: JobCancelRequest | None = None,
    _user: User = Depends(_permission_user("jobs:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    baseline_run_ids = list(payload.baseline_run_ids or []) if payload else []
    prompt_run_ids = list(payload.prompt_run_ids or []) if payload else []
    scoped = bool(baseline_run_ids or prompt_run_ids)
    # 全局取消没有 run 作用域：一次点击会把当时所有 queued/processing/paused 的 job
    # 全部判失败，跨 run、跨基准集连带击穿（2026-08-23 的 run50-54 就是这样连废的）。
    # 传入 run id 时只取消这些 run 自己的 job，并且不复位 paused——
    # 按 run 取消不该顺带把整个队列恢复成运行态。
    if not scoped:
        control.paused = False
    status_filter = EvaluationJob.status.in_(("queued", "processing", "paused"))
    if scoped:
        active_jobs = _collect_scoped_jobs(
            db, status_filter, _scoped_job_conditions(baseline_run_ids, prompt_run_ids)
        )
    else:
        active_jobs = list(db.scalars(select(EvaluationJob).where(status_filter)).all())
    now = datetime.now(timezone.utc)
    for job in active_jobs:
        if job.regression_item_id is not None:
            fail_regression_item(
                db,
                job.regression_item_id,
                "technical:operator_canceled",
            )
        if job.baseline_regression_item_id is not None:
            fail_baseline_item(
                db,
                item_id=job.baseline_regression_item_id,
                error_code="technical:operator_canceled",
                job_id=job.id,
            )
        has_result = db.scalar(
            select(EvaluationResult.id)
            .where(EvaluationResult.asset_id == job.asset_id)
            .limit(1)
        )
        job.status = "canceled"
        job.stage = "canceled"
        job.worker_id = None
        job.finished_at = now
        job.asset.status = "evaluated" if has_result else "uploaded"
    db.commit()
    return {
        "ok": True,
        "affected": len(active_jobs),
        # 让调用方能确认这次到底是全局取消还是按 run 取消。
        "scope": "runs" if scoped else "global",
        "baseline_run_ids": baseline_run_ids,
        "prompt_run_ids": prompt_run_ids,
    }


@app.get("/api/model-config")
def get_model_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "evaluation_main", ModelNodeBinding.enabled.is_(True)))
    config = binding.model if binding is not None else db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc()))
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return _model_config_payload(config)


def _model_config_payload(config: ModelConfig) -> dict[str, Any]:
    try:
        capabilities = json.loads(config.capabilities_json or "[]")
    except json.JSONDecodeError:
        capabilities = []
    return {
        "id": config.id,
        "name": config.name,
        "provider": config.provider,
        "protocol": config.protocol,
        "capabilities": capabilities if isinstance(capabilities, list) else [],
        "description": config.description,
        "base_url": config.base_url,
        "api_path": config.api_path,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "max_concurrency": config.max_concurrency,
        "structured_output": config.structured_output,
        "high_risk_review_enabled": config.high_risk_review_enabled,
        "thinking_mode": getattr(config, "thinking_mode", "auto") or "auto",
        "input_micros_per_million_tokens": config.input_micros_per_million_tokens,
        "output_micros_per_million_tokens": config.output_micros_per_million_tokens,
        "max_input_tokens": config.max_input_tokens,
        "benchmark_enabled": config.benchmark_enabled,
        "active": config.active,
        "has_api_key": bool(config.encrypted_api_key),
        "api_key_mask": "••••••••" if config.encrypted_api_key else "",
        "updated_at": config.updated_at,
    }


def _registry_capabilities(entry: ModelRegistryEntry) -> list[str]:
    try:
        capabilities = json.loads(entry.capabilities_json or "[]")
    except json.JSONDecodeError:
        capabilities = []
    return [str(item) for item in capabilities] if isinstance(capabilities, list) else []


def _registry_payload(entry: ModelRegistryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "role": entry.role,
        "name": entry.name,
        "provider": entry.provider,
        "protocol": entry.protocol,
        "capabilities": _registry_capabilities(entry),
        "description": entry.description,
        "base_url": entry.base_url,
        "api_path": entry.api_path,
        "model_id": entry.model_id,
        "temperature": entry.temperature,
        "max_tokens": entry.max_tokens,
        "timeout_seconds": entry.timeout_seconds,
        "max_retries": entry.max_retries,
        "max_concurrency": entry.max_concurrency,
        "max_requests_per_minute": entry.max_requests_per_minute,
        "max_input_tokens": entry.max_input_tokens,
        "input_micros_per_million_tokens": entry.input_micros_per_million_tokens,
        "output_micros_per_million_tokens": entry.output_micros_per_million_tokens,
        "monthly_budget_micros": entry.monthly_budget_micros,
        "thinking_mode": entry.thinking_mode,
        "level": entry.level,
        "structured_output": entry.structured_output,
        "active": entry.active,
        "source_model_config_id": entry.source_model_config_id,
        "source_optimizer_config_id": entry.source_optimizer_config_id,
        "has_api_key": bool(entry.encrypted_api_key),
        "api_key_mask": "••••••••" if entry.encrypted_api_key else "",
        "created_by": entry.created_by,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _copy_model_source_to_registry(
    entry: ModelRegistryEntry, config: ModelConfig
) -> None:
    entry.name = config.name
    entry.provider = config.provider
    entry.protocol = config.protocol
    entry.capabilities_json = config.capabilities_json
    entry.description = config.description
    entry.base_url = config.base_url
    entry.api_path = config.api_path
    entry.model_id = config.model_id
    entry.encrypted_api_key = config.encrypted_api_key
    entry.temperature = config.temperature
    entry.max_tokens = config.max_tokens
    entry.timeout_seconds = config.timeout_seconds
    entry.max_retries = config.max_retries
    entry.max_concurrency = config.max_concurrency
    entry.max_input_tokens = config.max_input_tokens
    entry.input_micros_per_million_tokens = config.input_micros_per_million_tokens
    entry.output_micros_per_million_tokens = config.output_micros_per_million_tokens
    entry.thinking_mode = getattr(config, "thinking_mode", "auto") or "auto"
    entry.structured_output = config.structured_output
    entry.active = config.active


def _copy_optimizer_source_to_registry(
    entry: ModelRegistryEntry, config: OptimizerConfig
) -> None:
    entry.name = config.name
    entry.provider = config.provider
    entry.protocol = config.protocol
    entry.capabilities_json = config.capabilities_json
    if entry.created_by in {"compatibility", "migration"}:
        entry.description = ""
    entry.base_url = config.base_url
    entry.api_path = config.api_path
    entry.model_id = config.model_id
    entry.encrypted_api_key = config.encrypted_api_key
    entry.temperature = config.temperature
    entry.max_tokens = config.max_tokens
    entry.timeout_seconds = config.timeout_seconds
    entry.max_retries = config.max_retries
    entry.max_input_tokens = config.max_input_tokens
    entry.input_micros_per_million_tokens = config.input_micros_per_million_tokens
    entry.output_micros_per_million_tokens = config.output_micros_per_million_tokens
    entry.structured_output = config.structured_output
    if entry.created_by in {"compatibility", "migration"}:
        entry.max_concurrency = 8
        entry.thinking_mode = "auto"
        entry.active = True


def _ensure_model_registry_entries(db: Session) -> list[ModelRegistryEntry]:
    entries = db.scalars(select(ModelRegistryEntry).order_by(ModelRegistryEntry.id.asc())).all()
    by_model = {entry.source_model_config_id: entry for entry in entries if entry.source_model_config_id is not None}
    by_optimizer = {entry.source_optimizer_config_id: entry for entry in entries if entry.source_optimizer_config_id is not None}
    changed = False
    for config in db.scalars(select(ModelConfig).order_by(ModelConfig.id.asc())).all():
        entry = by_model.get(config.id)
        if entry is None:
            entry = ModelRegistryEntry(
                role="main" if config.active else "benchmark",
                source_model_config_id=config.id,
                created_by="compatibility",
            )
            db.add(entry)
            entries.append(entry)
            changed = True
        _copy_model_source_to_registry(entry, config)
    for config in db.scalars(select(OptimizerConfig).order_by(OptimizerConfig.id.asc())).all():
        entry = by_optimizer.get(config.id)
        if entry is None:
            entry = ModelRegistryEntry(
                role="tuning",
                source_optimizer_config_id=config.id,
                created_by="compatibility",
            )
            db.add(entry)
            entries.append(entry)
            changed = True
        _copy_optimizer_source_to_registry(entry, config)
    if changed:
        db.commit()
        entries = db.scalars(select(ModelRegistryEntry).order_by(ModelRegistryEntry.id.asc())).all()
    return entries


def _apply_registry_payload(
    entry: ModelRegistryEntry,
    payload: ModelRegistryUpdate,
    *,
    encrypted_api_key: str | None = None,
) -> None:
    for field in (
        "role", "name", "provider", "protocol", "description", "base_url", "api_path",
        "model_id", "temperature", "max_tokens", "timeout_seconds", "max_retries",
        "max_concurrency", "max_requests_per_minute", "max_input_tokens",
        "input_micros_per_million_tokens", "output_micros_per_million_tokens",
        "monthly_budget_micros", "thinking_mode", "level", "structured_output", "active",
    ):
        setattr(entry, field, getattr(payload, field))
    entry.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    if encrypted_api_key is not None:
        entry.encrypted_api_key = encrypted_api_key


@app.get("/api/model-registry")
def list_model_registry(
    role: Literal["main", "tuning", "benchmark"] | None = None,
    active: bool | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entries = _ensure_model_registry_entries(db)
    if role is not None:
        entries = [entry for entry in entries if entry.role == role]
    if active is not None:
        entries = [entry for entry in entries if entry.active is active]
    return {"items": [_registry_payload(entry) for entry in entries]}


@app.post("/api/model-registry")
def create_model_registry_entry(
    payload: ModelRegistryUpdate,
    actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ensure_model_registry_entries(db)
    if payload.role == "tuning":
        source: ModelConfig | OptimizerConfig = OptimizerConfig(
            name=payload.name,
            provider=payload.provider,
            protocol=payload.protocol,
            capabilities_json=json.dumps(payload.capabilities, ensure_ascii=False),
            base_url=payload.base_url,
            api_path=payload.api_path,
            model_id=payload.model_id,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
            structured_output=payload.structured_output,
            input_micros_per_million_tokens=payload.input_micros_per_million_tokens,
            output_micros_per_million_tokens=payload.output_micros_per_million_tokens,
            max_input_tokens=payload.max_input_tokens,
        )
        db.add(source)
    else:
        source = ModelConfig(
            name=payload.name,
            provider=payload.provider,
            protocol=payload.protocol,
            capabilities_json=json.dumps(payload.capabilities, ensure_ascii=False),
            description=payload.description,
            base_url=payload.base_url,
            api_path=payload.api_path,
            model_id=payload.model_id,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
            max_concurrency=payload.max_concurrency,
            structured_output=payload.structured_output,
            thinking_mode=payload.thinking_mode,
            input_micros_per_million_tokens=payload.input_micros_per_million_tokens,
            output_micros_per_million_tokens=payload.output_micros_per_million_tokens,
            max_input_tokens=payload.max_input_tokens,
            benchmark_enabled=payload.role == "benchmark",
            active=payload.active,
        )
        db.add(source)
    db.flush()
    entry = ModelRegistryEntry(
        role=payload.role,
        source_model_config_id=source.id if isinstance(source, ModelConfig) else None,
        source_optimizer_config_id=source.id if isinstance(source, OptimizerConfig) else None,
        created_by=actor.username,
    )
    _apply_registry_payload(entry, payload)
    db.add(entry)
    db.flush()
    protected = _protected_api_key(
        payload.api_key, account=f"model-registry-{entry.id}"
    )
    if protected is not None:
        source.encrypted_api_key = protected
        entry.encrypted_api_key = protected
    db.commit()
    db.refresh(entry)
    return _registry_payload(entry)


@app.put("/api/model-registry/{entry_id}")
def update_model_registry_entry(
    entry_id: int,
    payload: ModelRegistryUpdate,
    actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entry = db.get(ModelRegistryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="模型注册项不存在")
    if entry.role == "tuning" and payload.role != "tuning":
        raise HTTPException(status_code=422, detail="调优模型不能切换为主模型角色")
    if entry.role != "tuning" and payload.role == "tuning":
        raise HTTPException(status_code=422, detail="主模型不能直接切换为调优模型角色")
    protected = _protected_api_key(payload.api_key, account=f"model-registry-{entry.id}")
    _apply_registry_payload(entry, payload, encrypted_api_key=protected)
    if entry.source_model_config_id is not None:
        source = db.get(ModelConfig, entry.source_model_config_id)
        if source is not None:
            _copy_registry_to_model_source(entry, source)
            if protected is not None:
                source.encrypted_api_key = protected
    if entry.source_optimizer_config_id is not None:
        source = db.get(OptimizerConfig, entry.source_optimizer_config_id)
        if source is not None:
            _copy_registry_to_optimizer_source(entry, source)
            if protected is not None:
                source.encrypted_api_key = protected
    entry.created_by = entry.created_by or actor.username
    db.commit()
    db.refresh(entry)
    return _registry_payload(entry)


def _copy_registry_to_model_source(entry: ModelRegistryEntry, source: ModelConfig) -> None:
    for field in (
        "name", "provider", "protocol", "description", "base_url", "api_path", "model_id",
        "temperature", "max_tokens", "timeout_seconds", "max_retries", "max_concurrency",
        "structured_output", "thinking_mode", "input_micros_per_million_tokens",
        "output_micros_per_million_tokens", "max_input_tokens", "active",
    ):
        setattr(source, field, getattr(entry, field))
    source.capabilities_json = entry.capabilities_json
    source.benchmark_enabled = entry.role == "benchmark"


def _copy_registry_to_optimizer_source(entry: ModelRegistryEntry, source: OptimizerConfig) -> None:
    for field in (
        "name", "provider", "protocol", "base_url", "api_path", "model_id", "temperature",
        "max_tokens", "timeout_seconds", "max_retries", "structured_output",
        "input_micros_per_million_tokens", "output_micros_per_million_tokens", "max_input_tokens",
    ):
        setattr(source, field, getattr(entry, field))
    source.capabilities_json = entry.capabilities_json


@app.post("/api/model-registry/{entry_id}/activate")
def activate_model_registry_entry(
    entry_id: int,
    actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entry = db.get(ModelRegistryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="模型注册项不存在")
    entry.active = True
    if entry.source_model_config_id is not None:
        source = db.get(ModelConfig, entry.source_model_config_id)
        if source is not None:
            source.active = True
    db.commit()
    db.refresh(entry)
    return _registry_payload(entry)


@app.post("/api/model-registry/{entry_id}/deactivate")
def deactivate_model_registry_entry(
    entry_id: int,
    actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entry = db.get(ModelRegistryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="模型注册项不存在")
    entry.active = False
    if entry.source_model_config_id is not None:
        source = db.get(ModelConfig, entry.source_model_config_id)
        if source is not None:
            source.active = False
    db.commit()
    db.refresh(entry)
    return _registry_payload(entry)


@app.post("/api/model-registry/{entry_id}/test")
def test_model_registry_entry(
    entry_id: int,
    _actor: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entry = db.get(ModelRegistryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="模型注册项不存在")
    if not entry.encrypted_api_key:
        raise HTTPException(status_code=422, detail="请先配置 API Key")
    if not entry.base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Base URL 必须使用 HTTP(S)")
    return {"ok": True, "message": "连接参数校验通过；本地测试未发起真实批量调用"}


@app.get("/api/model-registry/{entry_id}/bindings")
def list_model_registry_bindings(
    entry_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    entry = db.get(ModelRegistryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="模型注册项不存在")
    rows = []
    if entry.source_model_config_id is not None:
        rows = db.scalars(
            select(ModelNodeBinding)
            .where(ModelNodeBinding.model_config_id == entry.source_model_config_id)
            .order_by(ModelNodeBinding.node_key.asc(), ModelNodeBinding.category_key.asc())
        ).all()
    return {
        "items": [
            {
                "node_key": row.node_key,
                "category_key": row.category_key,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    }


@app.get("/api/model-configs")
def list_model_configs(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    configs = db.scalars(select(ModelConfig).order_by(ModelConfig.id.asc())).all()
    return {"items": [_model_config_payload(config) for config in configs]}


@app.post("/api/model-configs")
def create_benchmark_model_config(
    payload: BenchmarkModelConfigCreate,
    _user: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = ModelConfig(provider=payload.provider, active=True)
    db.add(config)
    db.flush()
    for field in (
        "name", "protocol", "base_url", "api_path", "model_id", "description",
        "max_tokens", "timeout_seconds", "max_retries", "max_concurrency",
        "structured_output", "high_risk_review_enabled", "thinking_mode",
        "input_micros_per_million_tokens",
        "output_micros_per_million_tokens", "max_input_tokens",
        "benchmark_enabled",
    ):
        setattr(config, field, getattr(payload, field))
    config.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=f"model-config-{config.id}",
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
    db.commit()
    db.refresh(config)
    return _model_config_payload(config)


@app.put("/api/model-configs/{config_id}")
def update_benchmark_model_config(
    config_id: int,
    payload: BenchmarkModelConfigCreate,
    _user: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = db.get(ModelConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="横评模型配置不存在")
    for field in (
        "provider", "name", "protocol", "base_url", "api_path", "model_id", "description",
        "temperature", "max_tokens", "timeout_seconds", "max_retries",
        "max_concurrency", "structured_output", "high_risk_review_enabled",
        "thinking_mode",
        "input_micros_per_million_tokens",
        "output_micros_per_million_tokens", "max_input_tokens",
        "benchmark_enabled",
    ):
        setattr(config, field, getattr(payload, field))
    config.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=f"model-config-{config.id}",
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
    db.commit()
    db.refresh(config)
    return _model_config_payload(config)


def _sampling_policy_payload(policy: SamplingPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": f"smart-sampling-v1.1/policy-{policy.revision}",
        "revision": policy.revision,
        "sample_rate": policy.sample_rate,
        "low_confidence_threshold": policy.low_confidence_threshold,
        "medium_confidence_threshold": policy.medium_confidence_threshold,
        "cold_start_required_count": policy.cold_start_required_count,
        "high_level_required_from": policy.high_level_required_from,
        "updated_by": policy.updated_by,
        "updated_at": policy.updated_at,
    }


def _review_workflow_policy_payload(
    policy: ReviewWorkflowPolicy,
) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": f"review-workflow-v1/policy-{policy.revision}",
        "revision": policy.revision,
        "initial_reviewers": policy.initial_reviewers,
        "supported_reviewer_counts": [1, 3, 5, 7, 9],
        "updated_by": policy.updated_by,
        "updated_at": policy.updated_at,
    }


@app.get("/api/sampling-policy")
def get_sampling_policy(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    policy = db.get(SamplingPolicy, 1)
    if not policy:
        policy = SamplingPolicy(id=1)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return _sampling_policy_payload(policy)


@app.get("/api/review-workflow-policy")
def get_review_workflow_policy(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    policy = db.get(ReviewWorkflowPolicy, 1)
    if not policy:
        policy = ReviewWorkflowPolicy(id=1, initial_reviewers=1)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return _review_workflow_policy_payload(policy)


@app.put("/api/review-workflow-policy")
def update_review_workflow_policy(
    payload: ReviewWorkflowPolicyUpdate,
    user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.get(ReviewWorkflowPolicy, 1)
    if not policy:
        policy = ReviewWorkflowPolicy(id=1)
        db.add(policy)
    policy.initial_reviewers = payload.initial_reviewers
    policy.revision = (policy.revision or 0) + 1
    policy.updated_by = user.display_name
    db.commit()
    db.refresh(policy)
    return _review_workflow_policy_payload(policy)


@app.put("/api/sampling-policy")
def update_sampling_policy(
    payload: SamplingPolicyUpdate,
    user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.get(SamplingPolicy, 1)
    if not policy:
        policy = SamplingPolicy(id=1)
        db.add(policy)
    for field in (
        "sample_rate",
        "low_confidence_threshold",
        "medium_confidence_threshold",
        "cold_start_required_count",
        "high_level_required_from",
    ):
        setattr(policy, field, getattr(payload, field))
    policy.revision = (policy.revision or 0) + 1
    policy.updated_by = user.display_name
    db.commit()
    db.refresh(policy)
    return _sampling_policy_payload(policy)


@app.put("/api/model-config")
def update_model_config(
    payload: ModelConfigUpdate,
    _user: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not config:
        config = ModelConfig()
        db.add(config)
    for field in (
        "name", "protocol", "description",
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
        "thinking_mode",
        "input_micros_per_million_tokens",
        "output_micros_per_million_tokens",
        "max_input_tokens",
        "benchmark_enabled",
    ):
        setattr(config, field, getattr(payload, field))
    config.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
    db.commit()
    return {"ok": True}


@app.post("/api/model-config/test")
async def test_model_config(
    _user: User = Depends(_permission_user("models:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        text = await DoubaoClient(config).test_connection()
        return {"ok": True, "message": text or "连接成功"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _optimizer_config_payload(config: OptimizerConfig) -> dict[str, Any]:
    try:
        capabilities = json.loads(config.capabilities_json or "[]")
    except json.JSONDecodeError:
        capabilities = []
    return {
        "id": config.id,
        "name": config.name,
        "provider": config.provider,
        "protocol": config.protocol,
        "capabilities": capabilities if isinstance(capabilities, list) else [],
        "base_url": config.base_url,
        "api_path": config.api_path,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "structured_output": config.structured_output,
        "input_micros_per_million_tokens": config.input_micros_per_million_tokens,
        "output_micros_per_million_tokens": config.output_micros_per_million_tokens,
        "max_input_tokens": config.max_input_tokens,
        "has_api_key": bool(config.encrypted_api_key),
        "api_key_mask": "••••••••" if config.encrypted_api_key else "",
        "updated_at": config.updated_at,
    }


@app.get("/api/optimizer-config")
def get_optimizer_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        config = OptimizerConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return _optimizer_config_payload(config)


@app.put("/api/optimizer-config")
def update_optimizer_config(
    payload: OptimizerConfigUpdate,
    _user: User = Depends(_permission_user("models:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        config = OptimizerConfig()
        db.add(config)
    for field in (
        "name",
        "provider", "protocol",
        "base_url",
        "api_path",
        "model_id",
        "temperature",
        "max_tokens",
        "timeout_seconds",
        "max_retries",
        "structured_output",
        "input_micros_per_million_tokens",
        "output_micros_per_million_tokens",
        "max_input_tokens",
    ):
        setattr(config, field, getattr(payload, field))
    config.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
    db.commit()
    return {"ok": True}


@app.post("/api/optimizer-config/test")
async def test_optimizer_config(
    _user: User = Depends(_permission_user("models:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        raise HTTPException(status_code=404, detail="提示词诊断模型配置不存在")
    try:
        text = await DoubaoClient(config).test_connection()
        return {"ok": True, "message": text or "连接成功"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/historical-corrections/preview")
async def preview_historical_corrections(
    files: list[UploadFile] = File(...),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    if not files or len(files) > 10:
        raise HTTPException(status_code=422, detail="每次仅允许预览 1 至 10 个 XLSX")
    uploads: list[tuple[str, bytes]] = []
    total_bytes = 0
    for upload in files:
        filename = Path(upload.filename or "").name
        if not filename or Path(filename).suffix.casefold() != ".xlsx":
            raise HTTPException(status_code=422, detail="仅允许上传 .xlsx 文件")
        content = await upload.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{filename} 超过 25 MB")
        total_bytes += len(content)
        if total_bytes > 64 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="本次 XLSX 总大小超过 64 MB")
        uploads.append((filename, content))
    try:
        return preview_historical_workbooks(uploads)
    except ImportPreflightError as exc:
        raise HTTPException(status_code=422, detail=exc.as_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/prompts")
def list_prompts(
    category_key: str | None = None,
    pipeline_scope: Literal["full_pipeline", "baseline_regression", "shared"] | None = None,
    include_archived: bool = False,
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    statement = select(PromptVersion)
    if category_key is not None:
        _category_profile(db, category_key)
        statement = statement.where(PromptVersion.category_key == category_key)
    if pipeline_scope is not None:
        statement = statement.where(
            PromptVersion.pipeline_scope.in_((pipeline_scope, "shared"))
        )
    if not include_archived:
        statement = statement.where(PromptVersion.status != "archived")
    prompts = db.scalars(statement.order_by(PromptVersion.created_at.desc())).all()
    return {
        "items": [
            {
                "id": prompt.id,
                "category_key": prompt.category_key,
                "pipeline_scope": prompt.pipeline_scope,
                "stage": prompt.stage,
                "name": prompt.name,
                "version": prompt.version,
                "system_prompt": prompt.system_prompt,
                "user_prompt": prompt.user_prompt,
                "rubric_version": prompt.rubric_version,
                "status": prompt.status,
                "source": prompt.source,
                "source_optimization_run_id": prompt.source_optimization_run_id,
                "rollback_prompt_id": prompt.rollback_prompt_id,
                "canary_status": prompt.canary_status,
                "metrics": _prompt_version_metrics(db, prompt),
                "change_note": prompt.change_note,
                "created_by": prompt.created_by,
                "created_at": prompt.created_at,
                "updated_at": prompt.updated_at,
            }
            for prompt in prompts
        ]
    }


def _prompt_has_references(db: Session, prompt_id: int) -> bool:
    return bool(
        db.scalar(select(EvaluationJob.id).where(
            (EvaluationJob.prompt_a_id == prompt_id)
            | (EvaluationJob.prompt_b_id == prompt_id)
        ).limit(1))
        or db.scalar(select(EvaluationCategoryProfile.id).where(
            (EvaluationCategoryProfile.prompt_a_id == prompt_id)
            | (EvaluationCategoryProfile.prompt_b_id == prompt_id)
        ).limit(1))
        or db.scalar(select(EvaluationPackage.id).where(
            (EvaluationPackage.prompt_a_id == prompt_id)
            | (EvaluationPackage.prompt_b_id == prompt_id)
        ).limit(1))
    )


def _apply_prompt_payload(prompt: PromptVersion, payload: PromptUpdateRequest) -> None:
    for field in (
        "category_key", "pipeline_scope", "stage", "name", "version",
        "system_prompt", "user_prompt", "rubric_version", "change_note",
    ):
        setattr(prompt, field, getattr(payload, field))


@app.put("/api/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    payload: PromptUpdateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if prompt.status == "archived":
        raise HTTPException(status_code=409, detail="已归档提示词不可编辑")
    if prompt.status == "published":
        raise HTTPException(status_code=409, detail="已发布提示词不可原地修改，请使用另存为")
    if _prompt_has_references(db, prompt_id):
        raise HTTPException(
            status_code=409,
            detail="提示词已被任务冻结，不能原地修改，请使用另存为",
        )
    _category_profile(db, payload.category_key)
    duplicate = db.scalar(select(PromptVersion).where(
        PromptVersion.category_key == payload.category_key,
        PromptVersion.stage == payload.stage,
        PromptVersion.version == payload.version,
        PromptVersion.id != prompt_id,
        PromptVersion.status != "archived",
    ))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="提示词版本号已存在")
    _apply_prompt_payload(prompt, payload)
    prompt.created_by = user.username
    db.commit()
    db.refresh(prompt)
    return {"id": prompt.id, "status": prompt.status}


@app.post("/api/prompts/{prompt_id}/clone")
def clone_prompt(
    prompt_id: int,
    payload: PromptCloneRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source = db.get(PromptVersion, prompt_id)
    if source is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    _category_profile(db, payload.category_key)
    duplicate = db.scalar(select(PromptVersion).where(
        PromptVersion.category_key == payload.category_key,
        PromptVersion.stage == payload.stage,
        PromptVersion.version == payload.version,
    ))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="提示词版本号已存在")
    clone = PromptVersion(**payload.model_dump(), status="draft", created_by=user.username)
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return {"id": clone.id, "status": clone.status}


@app.delete("/api/prompts/{prompt_id}")
def archive_prompt(
    prompt_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if prompt.status == "published":
        binding_column = (
            EvaluationCategoryProfile.prompt_a_id
            if prompt.stage == "A"
            else EvaluationCategoryProfile.prompt_b_id
        )
        active_binding = db.scalar(
            select(EvaluationCategoryProfile.id).where(
                EvaluationCategoryProfile.status == "active",
                binding_column == prompt.id,
            ).limit(1)
        )
        if active_binding is not None:
            raise HTTPException(status_code=409, detail="活动类目仍绑定此已发布版本，请先切换类目发布版本")
    # Archive is deliberately soft: historical bundles/jobs keep their FK and
    # frozen contents while the version disappears from active selectors.
    prompt.status = "archived"
    prompt.change_note = (f"{prompt.change_note}\n由 {user.username} 归档").strip()
    db.commit()
    return {"ok": True, "id": prompt.id, "status": prompt.status}


def _prompt_version_metrics(
    db: Session,
    prompt: PromptVersion,
    *,
    batch_key: str | None = None,
) -> dict[str, Any]:
    statement = select(EvaluationResult).join(
        EvaluationJob, EvaluationJob.id == EvaluationResult.job_id
    ).where(EvaluationJob.category_key == prompt.category_key)
    if prompt.stage == "A":
        statement = statement.where(
            EvaluationResult.prompt_a_version == prompt.version
        )
    else:
        statement = statement.where(
            EvaluationResult.prompt_b_version == prompt.version
        )
    if batch_key:
        statement = statement.where(EvaluationJob.batch_key == batch_key)
    results = db.scalars(statement).all()
    reviewed: list[tuple[EvaluationResult, HumanReview]] = []
    for result in results:
        final_review = None
        if (
            result.review_panel is not None
            and result.review_panel.status == "completed"
            and result.review_panel.final_review_id is not None
        ):
            final_review = db.get(
                HumanReview, result.review_panel.final_review_id
            )
        elif result.review_stage == "completed" and result.reviews:
            final_review = sorted(
                result.reviews,
                key=lambda review: (
                    _aware(review.created_at),
                    review.id or 0,
                ),
            )[-1]
        if final_review is not None:
            reviewed.append((result, final_review))

    reviewed_count = len(reviewed)
    corrected_sample_count = sum(
        1 for _, review in reviewed if review.decision != "approved"
    )
    dimension_totals: Counter[str] = Counter()
    dimension_correct: Counter[str] = Counter()
    grade_correct = 0
    for result, review in reviewed:
        model_scores = _model_dimension_scores(result)
        correction_by_key = {
            str(item.get("field_key")): item
            for item in json.loads(review.corrections_json or "[]")
            if item.get("target_type") == "dimension"
        }
        for key in model_scores:
            dimension_totals[key] += 1
            correction = correction_by_key.get(key)
            if (
                correction is None
                or correction.get("human_value") == model_scores[key]
            ):
                dimension_correct[key] += 1
        final_level = (
            review.corrected_level
            if review.decision == "corrected" and review.corrected_level
            else result.level
        )
        if review.decision != "rejected" and final_level == result.level:
            grade_correct += 1

    total = len(results)
    return {
        "schema_version": "prompt-version-metrics-v1",
        "prompt_id": prompt.id,
        "prompt_version": prompt.version,
        "frozen_task_set": {
            "batch_key": batch_key,
            "scope": "explicit_batch" if batch_key else "all_version_runs",
        },
        "sample_accuracy": (
            1 - corrected_sample_count / reviewed_count
            if reviewed_count
            else None
        ),
        "dimension_accuracy": {
            key: dimension_correct[key] / total_count
            for key, total_count in sorted(dimension_totals.items())
            if total_count
        },
        "grade_accuracy": (
            grade_correct / reviewed_count if reviewed_count else None
        ),
        "review_coverage": reviewed_count / total if total else 0,
        "sample_size_n": reviewed_count,
        "total_evaluations": total,
        "corrected_sample_count": corrected_sample_count,
        "unreviewed_not_counted_as_correct": True,
    }


@app.get("/api/prompts/{prompt_id}/metrics")
def get_prompt_version_metrics(
    prompt_id: int,
    batch_key: str | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    return _prompt_version_metrics(db, prompt, batch_key=batch_key)


def _prompt_metric_snapshot_payload(
    snapshot: PromptMetricSnapshot,
) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "prompt_id": snapshot.prompt_id,
        "task_set_key": snapshot.task_set_key,
        "task_set_hash": snapshot.task_set_hash,
        "evaluation_ids": json.loads(snapshot.evaluation_ids_json),
        "metrics": json.loads(snapshot.metrics_json),
        "total_count": snapshot.total_count,
        "reviewed_count": snapshot.reviewed_count,
        "created_by": snapshot.created_by,
        "created_at": snapshot.created_at,
    }


@app.get("/api/prompts/{prompt_id}/metric-snapshots")
def list_prompt_metric_snapshots(
    prompt_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(PromptVersion, prompt_id) is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    snapshots = db.scalars(
        select(PromptMetricSnapshot)
        .where(PromptMetricSnapshot.prompt_id == prompt_id)
        .order_by(
            PromptMetricSnapshot.created_at.desc(),
            PromptMetricSnapshot.id.desc(),
        )
    ).all()
    return {
        "items": [
            _prompt_metric_snapshot_payload(snapshot)
            for snapshot in snapshots
        ]
    }


@app.post("/api/prompts/{prompt_id}/metric-snapshots")
def create_prompt_metric_snapshot(
    prompt_id: int,
    payload: PromptMetricSnapshotRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    statement = select(EvaluationResult).join(
        EvaluationJob, EvaluationJob.id == EvaluationResult.job_id
    )
    if prompt.stage == "A":
        statement = statement.where(
            EvaluationResult.prompt_a_version == prompt.version
        )
    else:
        statement = statement.where(
            EvaluationResult.prompt_b_version == prompt.version
        )
    if payload.batch_key:
        statement = statement.where(
            EvaluationJob.batch_key == payload.batch_key
        )
    else:
        statement = statement.where(
            EvaluationResult.id.in_(payload.evaluation_ids)
        )
    results = list(db.scalars(statement).all())
    expected_count = (
        len(payload.evaluation_ids)
        if payload.evaluation_ids
        else len(results)
    )
    if not results or len(results) != expected_count:
        raise HTTPException(
            status_code=409,
            detail="冻结任务集包含不存在或不属于该提示词版本的评测结果",
        )
    evaluation_ids = sorted(result.id for result in results)
    task_hash = frozen_task_set_hash(evaluation_ids)
    existing = db.scalar(
        select(PromptMetricSnapshot).where(
            PromptMetricSnapshot.prompt_id == prompt.id,
            PromptMetricSnapshot.task_set_hash == task_hash,
        )
    )
    if existing is not None:
        if existing.task_set_key != payload.task_set_key:
            raise HTTPException(
                status_code=409,
                detail="相同冻结任务集已使用其他业务键登记",
            )
        return _prompt_metric_snapshot_payload(existing)
    panels = {
        panel.evaluation_id: panel
        for panel in db.scalars(
            select(ReviewPanel).where(
                ReviewPanel.evaluation_id.in_(evaluation_ids)
            )
        ).all()
    }
    review_ids = [
        panel.final_review_id
        for panel in panels.values()
        if panel.final_review_id is not None
    ]
    reviews = {
        review.id: review
        for review in db.scalars(
            select(HumanReview).where(HumanReview.id.in_(review_ids))
        ).all()
    }
    metrics = calculate_prompt_metrics(
        results,
        panels_by_evaluation=panels,
        reviews_by_id=reviews,
    )
    snapshot = PromptMetricSnapshot(
        prompt_id=prompt.id,
        task_set_key=payload.task_set_key,
        task_set_hash=task_hash,
        evaluation_ids_json=json.dumps(evaluation_ids),
        metrics_json=json.dumps(metrics, ensure_ascii=False),
        total_count=len(results),
        reviewed_count=int(metrics["reviewed_sample_count"]),
        created_by=user.username,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return _prompt_metric_snapshot_payload(snapshot)


@app.get("/api/agent-plans")
def list_agent_plans(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plans = db.scalars(
        select(AgentPlanVersion).order_by(
            AgentPlanVersion.created_at.desc(),
            AgentPlanVersion.id.desc(),
        )
    ).all()
    return {
        "items": [
            {
                "id": plan.id,
                "name": plan.name,
                "version": plan.version,
                "plan": json.loads(plan.plan_json),
                "status": plan.status,
                "created_by": plan.created_by,
                "created_at": plan.created_at,
            }
            for plan in plans
        ]
    }


@app.post("/api/prompts")
def create_prompt(
    payload: PromptCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    _category_profile(db, payload.category_key)
    exists = db.scalar(select(PromptVersion).where(
        PromptVersion.category_key == payload.category_key,
        PromptVersion.stage == payload.stage,
        PromptVersion.version == payload.version,
        PromptVersion.status != "archived",
    ))
    if exists:
        raise HTTPException(status_code=409, detail="提示词版本号已存在")
    prompt = PromptVersion(**payload.model_dump(), status="draft", created_by=user.username)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return {"id": prompt.id}


@app.post("/api/prompts/{prompt_id}/publish")
def publish_prompt(
    prompt_id: int,
    payload: PromptPublishRequest | None = None,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if prompt.status == "archived":
        raise HTTPException(status_code=409, detail="已归档提示词不可发布")
    if payload is not None and payload.pipeline_scope is not None:
        prompt.pipeline_scope = payload.pipeline_scope
    legacy_regression_id = db.scalar(
        select(PromptRegressionRun.id)
        .where(PromptRegressionRun.trigger_prompt_id == prompt.id)
        .order_by(PromptRegressionRun.id.desc())
        .limit(1)
    )
    if (
        prompt.source_optimization_run_id is not None
        or prompt.source_automation_run_id is not None
        or legacy_regression_id is not None
    ):
        evaluation_package = db.scalar(
            select(EvaluationPackage)
            .where(
                (EvaluationPackage.prompt_a_id == prompt.id)
                | (EvaluationPackage.prompt_b_id == prompt.id),
                EvaluationPackage.status.in_(("approved", "published")),
            )
            .order_by(EvaluationPackage.id.desc())
            .limit(1)
        )
        if evaluation_package is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "优化候选必须先形成完整评测包并完成人工二审；"
                    "旧配对回归批准不能绕过评测包发布门禁"
                ),
            )
        published_package, duplicate = publish_evaluation_package(
            db,
            package=evaluation_package,
            actor=user.username,
            note="通过兼容提示词发布接口触发",
        )
        return {
            "ok": True,
            "regression_run_ids": [],
            "evaluation_package_id": published_package.id,
            "duplicate": duplicate,
        }
    binding_column = (
        EvaluationCategoryProfile.prompt_a_id
        if prompt.stage == "A"
        else EvaluationCategoryProfile.prompt_b_id
    )
    active_bindings = int(
        db.scalar(
            select(func.count())
            .select_from(EvaluationCategoryProfile)
            .where(
                EvaluationCategoryProfile.status == "active",
                binding_column == prompt.id,
            )
        )
        or 0
    )
    if active_bindings:
        raise HTTPException(
            status_code=409,
            detail="活动类目使用中的提示词必须通过类目评测包执行发布",
        )
    # Legacy manual publication can make an unbound draft discoverable, but it
    # must not archive versions that remain executable baselines elsewhere.
    prompt.rollback_prompt_id = None
    prompt.status = "published"
    db.flush()
    db.commit()
    return {"ok": True, "regression_run_ids": []}


@app.post("/api/prompts/{prompt_id}/rollback")
def rollback_prompt(
    prompt_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current = db.get(PromptVersion, prompt_id)
    if current is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if current.status != "published":
        raise HTTPException(status_code=409, detail="只能回滚当前已发布版本")
    target = (
        db.get(PromptVersion, current.rollback_prompt_id)
        if current.rollback_prompt_id is not None
        else None
    )
    if target is None or target.stage != current.stage:
        raise HTTPException(status_code=409, detail="当前版本没有可验证的回滚指针")
    binding_column = (
        EvaluationCategoryProfile.prompt_a_id
        if current.stage == "A"
        else EvaluationCategoryProfile.prompt_b_id
    )
    active_bindings = int(
        db.scalar(
            select(func.count())
            .select_from(EvaluationCategoryProfile)
            .where(
                EvaluationCategoryProfile.status == "active",
                binding_column.in_((current.id, target.id)),
            )
        )
        or 0
    )
    if active_bindings:
        raise HTTPException(
            status_code=409,
            detail="活动类目使用中的提示词必须通过类目评测包执行回滚",
        )
    current.status = "archived"
    target.status = "published"
    target.rollback_prompt_id = current.id
    target.change_note = (
        f"{target.change_note}\n由 {user.username} 从 {current.version} 人工回滚"
    ).strip()
    db.commit()
    return {
        "ok": True,
        "published_prompt_id": target.id,
        "published_version": target.version,
        "rolled_back_from_id": current.id,
        "canary_status": target.canary_status,
    }


@app.post("/api/prompts/ai-revise")
async def ai_revise_prompt(
    payload: PromptAiReviseRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    prompt = db.get(PromptVersion, payload.prompt_id)
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not prompt or not config:
        raise HTTPException(status_code=404, detail="提示词或模型配置不存在")
    system = (
        "你是提示词编辑助手。只输出合法JSON，字段为 system_prompt、user_prompt、change_note。"
        "保持原有业务规则和输出JSON结构，不得删除安全边界，不得直接发布。"
    )
    user_text = json.dumps(
        {
            "stage": prompt.stage,
            "current_system_prompt": prompt.system_prompt,
            "current_user_prompt": prompt.user_prompt,
            "revision_instruction": payload.instruction,
        },
        ensure_ascii=False,
    )
    try:
        response = await DoubaoClient(config).chat_json(system, user_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "system_prompt": str(response.parsed.get("system_prompt", "")),
        "user_prompt": str(response.parsed.get("user_prompt", "")),
        "change_note": str(response.parsed.get("change_note", "AI 修改草案")),
    }


def _optimization_payload(run: PromptOptimizationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "base_prompt_id": run.base_prompt_id,
        "category_key": run.base_prompt.category_key,
        "base_prompt_version": run.base_prompt.version,
        "sample_set_id": run.sample_set_id,
        "sample_set_name": run.sample_set.name,
        "optimizer_model_id": run.optimizer_model_id,
        "status": run.status,
        "progress": run.progress,
        "sample_count": run.sample_count,
        "corrected_count": run.corrected_count,
        "diagnosis": json.loads(run.diagnosis_json or "{}"),
        "stage_audit": stage_audit_payload(run),
        "candidate_system_prompt": run.candidate_system_prompt,
        "candidate_user_prompt": run.candidate_user_prompt,
        "change_note": run.change_note,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@app.get("/api/prompt-optimizations")
def list_prompt_optimizations(
    limit: int = 20,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(PromptOptimizationRun)
        .order_by(PromptOptimizationRun.created_at.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return {"items": [_optimization_payload(run) for run in runs]}


@app.get("/api/prompt-optimizations/{run_id}")
def get_prompt_optimization(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptOptimizationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="提示词优化任务不存在")
    return _optimization_payload(run)


@app.post(
    "/api/prompt-optimizations/{run_id}/materialize-and-validate"
)
def materialize_prompt_optimization_and_validate(
    run_id: int,
    payload: PromptOptimizationMaterializeRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptOptimizationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="提示词优化任务不存在")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="提示词优化任务尚未成功完成")
    if not run.candidate_system_prompt.strip() or not run.candidate_user_prompt.strip():
        raise HTTPException(status_code=409, detail="优化任务没有可物化的候选提示词")
    if run.base_prompt.stage != "B":
        raise HTTPException(status_code=409, detail="当前只支持物化 B 阶段优化候选")
    if run.sample_set.kind != "golden" or run.sample_set.status != "locked":
        raise HTTPException(
            status_code=409, detail="候选验证必须使用已锁定黄金样本集"
        )

    existing = db.scalar(
        select(PromptVersion).where(
            PromptVersion.source_optimization_run_id == run.id
        )
    )
    if existing is not None:
        if existing.version != payload.version:
            raise HTTPException(
                status_code=409, detail="该优化任务已物化为其他不可变版本"
            )
        regression_ids = list(
            db.scalars(
                select(PromptRegressionRun.id)
                .where(
                    PromptRegressionRun.trigger_prompt_id == existing.id,
                    PromptRegressionRun.regression_mode == "paired",
                )
                .order_by(PromptRegressionRun.id.asc())
            ).all()
        )
        if regression_ids:
            return {
                "prompt_id": existing.id,
                "paired_regression_ids": regression_ids,
            }

    version_conflict = db.scalar(
        select(PromptVersion).where(PromptVersion.version == payload.version)
    )
    if version_conflict and version_conflict is not existing:
        raise HTTPException(status_code=409, detail="提示词版本号已存在")
    baseline = db.get(StrategyBundle, payload.baseline_strategy_bundle_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="基线 StrategyBundle 不存在")
    if baseline.prompt_b_version != run.base_prompt.version:
        raise HTTPException(
            status_code=409, detail="基线策略与优化任务的原始提示词不一致"
        )

    prompt_a_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "A",
            PromptVersion.version == baseline.prompt_a_version,
        )
    ).all()
    if len(prompt_a_matches) != 1:
        raise HTTPException(status_code=409, detail="基线 A 提示词无法唯一解析")
    model_matches = db.scalars(
        select(ModelConfig).where(
            ModelConfig.model_id == baseline.model_id,
            ModelConfig.active.is_(True),
        )
    ).all()
    if len(model_matches) != 1:
        raise HTTPException(status_code=409, detail="基线模型配置无法唯一解析")
    if baseline.sampling_policy_revision is None:
        policy = None
    else:
        policy = db.scalar(
            select(SamplingPolicy).where(
                SamplingPolicy.revision == baseline.sampling_policy_revision
            )
        )
        if policy is None:
            raise HTTPException(status_code=409, detail="基线抽样策略无法解析")

    candidate = existing or PromptVersion(
        category_key=run.base_prompt.category_key,
        stage="B",
        name=payload.name or f"{run.base_prompt.name} 优化候选 #{run.id}",
        version=payload.version,
        system_prompt=run.candidate_system_prompt,
        user_prompt=run.candidate_user_prompt,
        rubric_version=baseline.rubric_version,
        status="draft",
        source="optimizer",
        source_optimization_run_id=run.id,
        change_note=run.change_note,
        created_by=user.username,
    )
    if existing is None:
        db.add(candidate)
        db.flush()
    candidate_bundle = get_or_create_bundle(
        db=db,
        model_config=model_matches[0],
        prompt_a=prompt_a_matches[0],
        prompt_b=candidate,
        rubric_version=baseline.rubric_version,
        engine_version=baseline.engine_version,
        risk_review_version=baseline.risk_review_version,
        sampling_policy=policy,
    )
    if candidate_bundle.model_config_snapshot != baseline.model_config_snapshot:
        raise HTTPException(
            status_code=409,
            detail="当前模型配置已漂移，不能把提示词差异伪装成单变量回归",
        )

    regression = create_paired_regression(
        PairedRegressionCreateRequest(
            name=f"优化候选 #{run.id} 发布前配对回归",
            sample_set_id=run.sample_set_id,
            baseline_strategy_bundle_id=baseline.id,
            candidate_strategy_bundle_id=candidate_bundle.id,
            trigger_prompt_id=candidate.id,
            samples=payload.samples,
            metric_rules_version=payload.metric_rules_version,
            aesthetic_accuracy_max_drop=payload.aesthetic_accuracy_max_drop,
            whole_image_accuracy_max_drop=payload.whole_image_accuracy_max_drop,
            level_consistency_max_drop=payload.level_consistency_max_drop,
        ),
        user=user,
        db=db,
    )
    regression_run = db.get(PromptRegressionRun, int(regression["id"]))
    if regression_run is None:
        raise HTTPException(status_code=500, detail="候选配对回归创建后无法回查")
    return {
        "prompt_id": candidate.id,
        "paired_regression_ids": [regression_run.id],
    }


@app.post("/api/prompt-optimizations")
def create_prompt_optimization(
    payload: PromptOptimizationCreateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    prompt = db.get(PromptVersion, payload.prompt_id)
    sample_set = db.get(SampleSet, payload.sample_set_id)
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not prompt or not sample_set:
        raise HTTPException(status_code=404, detail="提示词或样本集不存在")
    if prompt.stage != "B":
        raise HTTPException(status_code=400, detail="样本驱动优化目前用于调用 B 的美感维度提示词")
    if prompt.category_key != sample_set.category_key:
        raise HTTPException(status_code=409, detail="提示词与样本集属于不同评测类目")
    if not config or not config.encrypted_api_key:
        raise HTTPException(status_code=400, detail="请先在模型配置中填写提示词诊断模型 API Key")
    if not sample_set.items:
        raise HTTPException(status_code=400, detail="样本集还没有图片")
    active_run = db.scalar(
        select(PromptOptimizationRun).where(
            PromptOptimizationRun.status.in_(["queued", "running"])
        )
    )
    if active_run:
        raise HTTPException(status_code=409, detail="已有提示词优化任务正在运行")
    run = PromptOptimizationRun(
        base_prompt_id=prompt.id,
        sample_set_id=sample_set.id,
        optimizer_model_id=config.model_id,
        created_by=user.username,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(run_prompt_optimization, run.id)
    return {"id": run.id}


def _review_standard_answer(
    *,
    decision: str,
    corrected_level: str | None,
    corrected_score: float | None,
    corrections: list[dict[str, Any]],
) -> str:
    normalized_corrections = sorted(
        corrections,
        key=lambda item: (
            str(item.get("target_type") or ""),
            str(item.get("field_key") or ""),
            json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
    )
    return json.dumps(
        {
            "decision": decision,
            "corrected_level": corrected_level,
            "corrected_score": corrected_score,
            "corrections": normalized_corrections,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _requires_secondary_review(evaluation: EvaluationResult, decision: str) -> bool:
    if decision in {"corrected", "rejected"}:
        return True
    if evaluation.level in {"L4", "L5"} or evaluation.needs_review:
        return True
    risk_review = json.loads(evaluation.risk_review_json or "{}")
    return bool(
        risk_review.get("triggered")
        or risk_review.get("verdict") in {"uncertain", "error", "corrected"}
    )


def _model_dimension_scores(evaluation: EvaluationResult) -> dict[str, int]:
    from .dimension_deduction_bridge import dimension_result_map

    aesthetic = json.loads(evaluation.aesthetic_json or "{}")
    dimensions = dimension_result_map(aesthetic.get("dimensions"))
    return {
        str(key): int(value["grade"])
        for key, value in dimensions.items()
        if isinstance(value, dict)
        and isinstance(value.get("grade"), (int, float))
        and 1 <= int(value["grade"]) <= 5
    }


def _vote_dimension_scores(
    evaluation: EvaluationResult,
    review: HumanReview,
) -> dict[str, int]:
    scores = _model_dimension_scores(evaluation)
    for correction in json.loads(review.corrections_json or "[]"):
        if correction.get("target_type") != "dimension":
            continue
        value = correction.get("human_value")
        if isinstance(value, int) and 1 <= value <= 5:
            scores[str(correction.get("field_key"))] = value
    return scores


def _strict_majority(values: list[Any]) -> Any | None:
    if not values:
        return None
    value, count = Counter(values).most_common(1)[0]
    return value if count > len(values) // 2 else None


def _panel_payload(
    panel: ReviewPanel,
    *,
    reviewer_name: str | None = None,
) -> dict[str, Any]:
    panel_votes = [
        review
        for review in panel.evaluation.reviews
        if review.panel_id == panel.id
        and review.review_round == panel.review_round
    ]
    my_vote = next(
        (
            review
            for review in panel_votes
            if reviewer_name and review.reviewer_name == reviewer_name
        ),
        None,
    )
    reveal_votes = panel.status == "completed"
    return {
        "id": panel.id,
        "evaluation_id": panel.evaluation_id,
        "required_reviewers": panel.required_reviewers,
        "submitted_count": len(panel_votes),
        "status": panel.status,
        "revision": panel.revision,
        "review_round": panel.review_round,
        "evaluation_review_stage": panel.evaluation.review_stage,
        "evaluation_review_revision": panel.evaluation.review_revision,
        "my_vote": (
            {
                "id": my_vote.id,
                "decision": my_vote.decision,
                "note": my_vote.note,
                "corrections": json.loads(my_vote.corrections_json or "[]"),
                "created_at": my_vote.created_at,
            }
            if my_vote
            else None
        ),
        "votes": (
            [
                {
                    "id": review.id,
                    "reviewer_name": review.reviewer_name,
                    "decision": review.decision,
                    "note": review.note,
                    "corrections": json.loads(
                        review.corrections_json or "[]"
                    ),
                    "created_at": review.created_at,
                }
                for review in panel_votes
            ]
            if reveal_votes
            else []
        ),
        "final_truth": (
            json.loads(panel.final_truth_json or "{}")
            if reveal_votes
            else None
        ),
        "blind_answers_hidden": not reveal_votes,
        "completed_at": panel.completed_at,
    }


def _claim_review_panel_revision_or_409(
    db: Session,
    *,
    panel: ReviewPanel,
    expected_revision: int,
) -> int:
    try:
        return claim_review_panel_revision(
            db,
            panel_id=panel.id,
            expected_revision=expected_revision,
        )
    except ReviewPanelRevisionConflict:
        db.rollback()
        current_revision = db.scalar(
            select(ReviewPanel.revision).where(ReviewPanel.id == panel.id)
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVIEW_PANEL",
                "message": "初审组修订号已变化，请刷新后重试",
                "revision": current_revision,
            },
        ) from None


def _evaluation_aesthetic_and_dimension_schema(
    evaluation: EvaluationResult,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    aesthetic = (
        json.loads(evaluation.aesthetic_json)
        if evaluation.aesthetic_json
        else None
    )
    definition = dimension_schema_from_strategy_snapshot(
        evaluation.strategy_snapshot_json,
        aesthetic=aesthetic,
    )
    selection = dimension_selection_from_job_snapshot(
        evaluation.job.category_profile_snapshot_json
        if evaluation.job is not None
        else None
    )
    return aesthetic, (
        project_dimension_definition(definition, selection)
        if selection is not None
        else definition
    )


def _review_level_thresholds(
    evaluation: EvaluationResult,
) -> dict[str, float] | None:
    """Return frozen V3 minimum-score thresholds for human correction replay."""
    try:
        scoring = json.loads(evaluation.scoring_json or "{}")
    except json.JSONDecodeError:
        scoring = {}
    if not isinstance(scoring, dict):
        scoring = {}
    context = scoring.get("v3_context")
    contract = context.get("contract") if isinstance(context, dict) else None
    foundation = contract.get("aesthetic_foundation") if isinstance(contract, dict) else None
    thresholds = foundation.get("score_thresholds") if isinstance(foundation, dict) else None
    if not isinstance(thresholds, list):
        return None
    resolved: dict[str, float] = {}
    for item in thresholds:
        if not isinstance(item, dict):
            return None
        level = item.get("level")
        minimum = item.get("min_score")
        if level not in {"L1", "L2", "L3", "L4", "L5"}:
            return None
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
            return None
        resolved[level] = float(minimum)
    return resolved or None


def _calculate_review_dimension_score(
    evaluation: EvaluationResult,
    dimension_corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    v3_recalculated = calculate_v3_review_corrected_score(
        evaluation, dimension_corrections
    )
    if v3_recalculated is not None:
        return v3_recalculated
    aesthetic, dimension_schema = _evaluation_aesthetic_and_dimension_schema(
        evaluation
    )
    return calculate_corrected_score(
        json.loads(evaluation.precheck_json),
        aesthetic,
        dimension_corrections,
        dimension_schema=dimension_schema,
        level_thresholds=_review_level_thresholds(evaluation),
    )


def _finalize_review_panel(
    db: Session,
    *,
    evaluation: EvaluationResult,
    panel: ReviewPanel,
    expected_panel_revision: int,
    decision: str,
    corrections: list[dict[str, Any]],
    reviewer_name: str,
    note: str,
    final_stage: Literal["initial"],
    resolution_mode: Literal[
        "single_reviewer", "majority_consensus", "lead_adjudication"
    ],
    resolved_dimensions: dict[str, Any] | None = None,
    resolved_key_fields: dict[str, Any] | None = None,
) -> HumanReview:
    corrected_score = None
    corrected_level = None
    if decision == "corrected":
        dimension_corrections = [
            correction
            for correction in corrections
            if correction.get("target_type") == "dimension"
        ]
        if dimension_corrections:
            recalculated = _calculate_review_dimension_score(
                evaluation,
                dimension_corrections,
            )
            corrected_score = recalculated.get("score")
            corrected_level = recalculated.get("level")
        else:
            corrected_score = evaluation.score
            corrected_level = evaluation.level
        if corrected_score is None or corrected_level is None:
            raise HTTPException(
                status_code=400, detail="初审共识无法计算正式等级"
            )
    _claim_review_panel_revision_or_409(
        db,
        panel=panel,
        expected_revision=expected_panel_revision,
    )
    final_review = HumanReview(
        evaluation_id=evaluation.id,
        reviewer_name=reviewer_name,
        review_round=panel.review_round,
        stage=final_stage,
        decision=decision,
        corrected_level=corrected_level,
        corrected_score=corrected_score,
        note=note,
        corrections_json=json.dumps(corrections, ensure_ascii=False),
    )
    db.add(final_review)
    db.flush()
    truth = {
        "schema_version": "review-panel-truth-v1",
        "decision": decision,
        "corrected_level": corrected_level,
        "corrected_score": corrected_score,
        "corrections": corrections,
        "dimensions": resolved_dimensions or {},
        "key_fields": resolved_key_fields or {},
        "panel_id": panel.id,
        "review_round": panel.review_round,
        "required_reviewers": panel.required_reviewers,
        "resolution_mode": resolution_mode,
    }
    panel.final_review_id = final_review.id
    panel.final_truth_json = json.dumps(truth, ensure_ascii=False)
    panel.status = "completed"
    panel.completed_at = datetime.now(timezone.utc)
    evaluation.review_stage = "completed"
    evaluation.review_revision += 1
    evaluation.needs_review = decision == "rejected"
    evaluation.updated_at = datetime.now(timezone.utc)
    if decision == "corrected":
        _add_to_category_golden_set(
            db,
            evaluation=evaluation,
            truth=truth,
            actor=reviewer_name,
        )
        try:
            mechanism_snapshot = json.loads(
                evaluation.strategy_snapshot_json or "{}"
            )
        except json.JSONDecodeError:
            mechanism_snapshot = {}
        on_final_review_completed(
            db,
            evaluation=evaluation,
            final_review=final_review,
            mechanism_snapshot=(
                mechanism_snapshot
                if isinstance(mechanism_snapshot, dict)
                else {}
            ),
            actor=reviewer_name,
        )
    return final_review


def _add_to_category_golden_set(
    db: Session,
    *,
    evaluation: EvaluationResult,
    truth: dict[str, Any],
    actor: str,
) -> None:
    """Persist every final correction into its isolated system golden set."""
    category_key = evaluation.job.category_key
    sample_set = db.scalar(
        select(SampleSet).where(
            SampleSet.category_key == category_key,
            SampleSet.kind == "golden",
            SampleSet.name == f"系统黄金集·{category_key}",
        )
    )
    if sample_set is None:
        sample_set = SampleSet(
            name=f"系统黄金集·{category_key}",
            description="由最终人工纠偏自动沉淀；按类目隔离维护。",
            kind="golden",
            status="locked",
            category_key=category_key,
            created_by="automation",
        )
        db.add(sample_set)
        db.flush()
    existing = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.sample_set_id == sample_set.id,
            SampleSetItem.asset_id == evaluation.asset_id,
        )
    )
    if existing is not None and existing.source_result_id == evaluation.id:
        return
    source_payload = _result_payload(evaluation) or {}
    category = (
        (source_payload.get("precheck") or {}).get("classification") or {}
    ).get("primary_category") or "无法判断"
    expected_level = truth.get("corrected_level") or evaluation.level
    truth_json = json.dumps(truth, ensure_ascii=False)
    if existing is None:
        item = SampleSetItem(
            sample_set_id=sample_set.id,
            asset_id=evaluation.asset_id,
            source_result_id=evaluation.id,
            expected_level=expected_level,
            expected_category=str(category),
            truth_json=truth_json,
            truth_revision=1,
            truth_updated_by=actor,
            truth_updated_at=datetime.now(timezone.utc),
            added_by=actor,
        )
        db.add(item)
        db.flush()
    else:
        item = existing
        item.source_result_id = evaluation.id
        item.expected_level = expected_level
        item.expected_category = str(category)
        item.truth_json = truth_json
        item.truth_revision += 1
        item.truth_updated_by = actor
        item.truth_updated_at = datetime.now(timezone.utc)
    db.add(
        SampleTruthRevision(
            sample_item_id=item.id,
            revision=item.truth_revision,
            truth_json=item.truth_json,
            reason=(
                "最终人工纠偏自动沉淀为类目黄金样本"
                if item.truth_revision == 1
                else "同一素材形成新的最终人工真值"
            ),
            reviewer_name=actor,
        )
    )


@app.post("/api/evaluations/{evaluation_id}/review-panel/open")
def open_review_panel(
    evaluation_id: int,
    payload: ReviewPanelOpenRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    policy = db.get(ReviewWorkflowPolicy, 1)
    required_reviewers = (
        payload.required_reviewers
        if payload.required_reviewers is not None
        else policy.initial_reviewers
        if policy is not None
        else 1
    )
    existing_panel = db.scalar(
        select(ReviewPanel).where(
            ReviewPanel.evaluation_id == evaluation.id
        )
    )
    if existing_panel is not None:
        if (
            payload.required_reviewers is not None
            and existing_panel.required_reviewers != required_reviewers
        ):
            raise HTTPException(
                status_code=409, detail="初审组已经按其他人数冻结"
            )
        return _panel_payload(existing_panel)
    if evaluation.review_stage == "completed":
        raise HTTPException(status_code=409, detail="该结果已经形成最终真值")
    panel = ReviewPanel(
        evaluation_id=evaluation.id,
        required_reviewers=required_reviewers,
        status="collecting",
    )
    db.add(panel)
    db.commit()
    db.refresh(panel)
    return _panel_payload(panel)


@app.post("/api/evaluations/{evaluation_id}/review-panel/reopen")
def reopen_review_panel(
    evaluation_id: int,
    payload: ReviewPanelReopenRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    panel = evaluation.review_panel if evaluation is not None else None
    if evaluation is None or panel is None:
        raise HTTPException(status_code=404, detail="初审组不存在")
    if evaluation.review_stage != "completed" or panel.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVIEW_NOT_COMPLETED",
                "message": "只有已完成的人工结果可以创建新修订",
                "review_stage": evaluation.review_stage,
                "review_revision": evaluation.review_revision,
            },
        )
    if evaluation.review_revision != payload.expected_review_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVIEW_SNAPSHOT",
                "message": "审核修订号已变化，请刷新后重试",
                "review_stage": evaluation.review_stage,
                "review_revision": evaluation.review_revision,
            },
        )

    next_evaluation_revision = evaluation.review_revision + 1
    next_panel_revision = panel.revision + 1
    next_round = panel.review_round + 1
    claimed_evaluation = db.execute(
        update(EvaluationResult)
        .where(
            EvaluationResult.id == evaluation.id,
            EvaluationResult.review_stage == "completed",
            EvaluationResult.review_revision == payload.expected_review_revision,
        )
        .values(
            review_stage="initial",
            review_revision=next_evaluation_revision,
            needs_review=True,
            updated_at=datetime.now(timezone.utc),
        )
    )
    claimed_panel = db.execute(
        update(ReviewPanel)
        .where(
            ReviewPanel.id == panel.id,
            ReviewPanel.status == "completed",
            ReviewPanel.revision == panel.revision,
            ReviewPanel.review_round == panel.review_round,
        )
        .values(
            status="collecting",
            revision=next_panel_revision,
            review_round=next_round,
            final_review_id=None,
            final_truth_json="{}",
            completed_at=None,
        )
    )
    if claimed_evaluation.rowcount != 1 or claimed_panel.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVIEW_SNAPSHOT",
                "message": "审核记录已被其他操作更新，请刷新后重试",
            },
        )
    db.commit()
    db.expire_all()
    reopened = db.get(ReviewPanel, panel.id)
    assert reopened is not None
    return _panel_payload(reopened)


@app.get("/api/evaluations/{evaluation_id}/review-panel")
def get_review_panel(
    evaluation_id: int,
    reviewer_name: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    panel = db.scalar(
        select(ReviewPanel).where(
            ReviewPanel.evaluation_id == evaluation_id
        )
    )
    if not panel:
        raise HTTPException(status_code=404, detail="该结果没有初审组")
    # reviewer_name remains parseable for old clients but never selects identity.
    return _panel_payload(panel, reviewer_name=user.username)


@app.get("/api/review-panels")
def list_review_panels(
    status: Literal["collecting", "lead_adjudication", "completed"] | None = None,
    limit: int = 200,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(ReviewPanel).order_by(
        ReviewPanel.created_at.desc(), ReviewPanel.id.desc()
    )
    if status is not None:
        statement = statement.where(ReviewPanel.status == status)
    panels = db.scalars(statement.limit(min(max(limit, 1), 500))).all()
    return {
        "items": [
            {
                **_panel_payload(panel),
                "asset": _asset_payload(panel.evaluation.asset),
                "evaluation": _result_payload(panel.evaluation),
            }
            for panel in panels
        ]
    }


@app.post("/api/evaluations/{evaluation_id}/review-panel/votes")
def submit_review_panel_vote(
    evaluation_id: int,
    payload: ReviewPanelVoteRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    panel = (
        db.scalar(
            select(ReviewPanel).where(
                ReviewPanel.evaluation_id == evaluation_id
            )
        )
        if evaluation
        else None
    )
    if not evaluation or not panel:
        raise HTTPException(status_code=404, detail="初审组不存在")
    if panel.status != "collecting":
        raise HTTPException(status_code=409, detail="初审组当前不接受普通投票")
    if db.scalar(
        select(HumanReview).where(
            HumanReview.panel_id == panel.id,
            HumanReview.reviewer_name == user.username,
            HumanReview.review_round == panel.review_round,
        )
    ):
        raise HTTPException(status_code=409, detail="当前审核员已经提交盲审")
    correction_data = [item.model_dump() for item in payload.corrections]
    if payload.decision == "corrected":
        try:
            dimension_corrections = [
                item for item in correction_data
                if item.get("target_type") == "dimension"
            ]
            if dimension_corrections:
                recalculated = _calculate_review_dimension_score(
                    evaluation,
                    dimension_corrections,
                )
                corrected_score = recalculated.get("score")
                corrected_level = recalculated.get("level")
            else:
                corrected_score = evaluation.score
                corrected_level = evaluation.level
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        corrected_score = None
        corrected_level = None
    claimed_revision = _claim_review_panel_revision_or_409(
        db,
        panel=panel,
        expected_revision=payload.expected_panel_revision,
    )
    vote = HumanReview(
        evaluation_id=evaluation.id,
        panel_id=panel.id,
        panel_revision=payload.expected_panel_revision,
        review_round=panel.review_round,
        reviewer_name=user.username,
        stage="initial",
        decision=payload.decision,
        corrected_level=corrected_level,
        corrected_score=corrected_score,
        note=payload.note,
        corrections_json=json.dumps(correction_data, ensure_ascii=False),
    )
    db.add(vote)
    evaluation.review_revision += 1
    db.flush()
    votes = list(
        db.scalars(
            select(HumanReview)
            .where(
                HumanReview.panel_id == panel.id,
                HumanReview.review_round == panel.review_round,
            )
            .order_by(HumanReview.id.asc())
        ).all()
    )
    resolution = resolve_panel_consensus(
        evaluation,
        votes,
        required_reviewers=panel.required_reviewers,
    )
    if resolution["status"] == "lead_adjudication":
        panel.status = "lead_adjudication"
        # 主审裁决仍属于初审工作台，不复用历史 arbitration 状态。
        evaluation.review_stage = "initial"
    elif resolution["status"] == "completed":
        corrections = list(resolution["corrections"])
        final_decision = (
            "rejected"
            if resolution["decision"] == "rejected"
            else "corrected"
            if corrections
            else "approved"
        )
        _finalize_review_panel(
            db,
            evaluation=evaluation,
            panel=panel,
            expected_panel_revision=claimed_revision,
            decision=final_decision,
            corrections=corrections,
            reviewer_name=(
                "初审单人定案"
                if panel.required_reviewers == 1
                else "初审组共识"
            ),
            note=(
                "单人初审提交后形成最终人工真值"
                if panel.required_reviewers == 1
                else (
                    f"{panel.required_reviewers} 人独立盲审后"
                    "形成逐字段严格多数共识"
                )
            ),
            final_stage="initial",
            resolution_mode=(
                "single_reviewer"
                if panel.required_reviewers == 1
                else "majority_consensus"
            ),
            resolved_dimensions=resolution["dimensions"],
            resolved_key_fields=resolution["key_fields"],
        )
    db.commit()
    db.expire(evaluation, ["reviews"])
    db.refresh(panel)
    return _panel_payload(panel, reviewer_name=user.username)


@app.post("/api/evaluations/{evaluation_id}/review-panel/lead-adjudication")
def adjudicate_review_panel(
    evaluation_id: int,
    payload: ReviewPanelAdjudicationRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    panel = (
        db.scalar(
            select(ReviewPanel).where(
                ReviewPanel.evaluation_id == evaluation_id
            )
        )
        if evaluation
        else None
    )
    if not evaluation or not panel:
        raise HTTPException(status_code=404, detail="初审组不存在")
    if panel.status != "lead_adjudication":
        raise HTTPException(status_code=409, detail="当前不需要主审裁决")
    _finalize_review_panel(
        db,
        evaluation=evaluation,
        panel=panel,
        expected_panel_revision=payload.expected_panel_revision,
        decision=payload.decision,
        corrections=[item.model_dump() for item in payload.corrections],
        reviewer_name=user.username,
        note=payload.note or "初审工作台主审裁决",
        final_stage="initial",
        resolution_mode="lead_adjudication",
        resolved_dimensions=review_truth(
            evaluation,
            decision=payload.decision,
            corrected_level=None,
            corrected_score=None,
            corrections=[item.model_dump() for item in payload.corrections],
        )["dimensions"],
        resolved_key_fields=review_truth(
            evaluation,
            decision=payload.decision,
            corrected_level=None,
            corrected_score=None,
            corrections=[item.model_dump() for item in payload.corrections],
        )["key_fields"],
    )
    db.commit()
    db.refresh(panel)
    return _panel_payload(panel, reviewer_name=user.username)


@app.get("/api/optimization-cases")
def list_optimization_cases(
    status: Literal[
        "pending", "batched", "processing", "completed", "failed"
    ] | None = None,
    limit: int = 200,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(OptimizationCaseQueue).order_by(
        OptimizationCaseQueue.created_at.desc(),
        OptimizationCaseQueue.id.desc(),
    )
    if status is not None:
        statement = statement.where(
            OptimizationCaseQueue.status == status
        )
    cases = db.scalars(statement.limit(min(max(limit, 1), 500))).all()
    return {
        "items": [
            {
                "id": case.id,
                "idempotency_key": case.idempotency_key,
                "evaluation_id": case.evaluation_id,
                "final_review_id": case.final_review_id,
                "source_type": case.source_type,
                "source_event_id": case.source_event_id,
                "category_key": case.category_key,
                "prompt_version": case.prompt_version,
                "severity": case.severity,
                "case": json.loads(case.case_json),
                "status": case.status,
                "attempt_count": case.attempt_count,
                "next_attempt_at": case.next_attempt_at,
                "last_error": case.last_error,
                "automation_run_id": case.automation_run_id,
                "created_at": case.created_at,
                "updated_at": case.updated_at,
            }
            for case in cases
        ]
    }


def _automation_policy_payload(
    policy: AutomationPolicy, db: Session
) -> dict[str, Any]:
    adapter = configured_optimization_adapter(db)
    runtime = automation_runtime_status(db, policy)
    return {
        "id": policy.id,
        "enabled": policy.enabled,
        "dry_run": policy.dry_run,
        "revision": policy.revision,
        "case_threshold": policy.case_threshold,
        "immediate_severities": json.loads(
            policy.immediate_severities_json or "[]"
        ),
        "daily_budget_micros": policy.daily_budget_micros,
        "cooldown_seconds": policy.cooldown_seconds,
        "max_candidates": policy.max_candidates,
        "lease_seconds": policy.lease_seconds,
        "max_attempts": policy.max_attempts,
        "base_retry_seconds": policy.base_retry_seconds,
        "last_triggered_at": policy.last_triggered_at,
        "updated_by": policy.updated_by,
        "updated_at": policy.updated_at,
        "budget": automation_budget_status(db, policy),
        "real_model_calls_enabled": adapter is not None,
        "runtime": runtime,
        "auto_publish_enabled": False,
    }


@app.get("/api/automation-policy")
def get_automation_policy(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.get(AutomationPolicy, 1)
    if policy is None:
        policy = AutomationPolicy(id=1)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return _automation_policy_payload(policy, db)


@app.put("/api/automation-policy")
def update_automation_policy(
    payload: AutomationPolicyUpdate,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.get(AutomationPolicy, 1)
    if policy is None:
        policy = AutomationPolicy(id=1)
        db.add(policy)
    for key, value in payload.model_dump(
        exclude={"immediate_severities"}
    ).items():
        setattr(policy, key, value)
    policy.immediate_severities_json = canonical_json(
        payload.immediate_severities
    )
    policy.revision += 1
    policy.updated_by = user.username
    db.commit()
    db.refresh(policy)
    return _automation_policy_payload(policy, db)


def _automation_run_payload(
    run: AutomationOptimizationRun,
) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_key": run.run_key,
        "base_prompt_version": run.base_prompt_version,
        "category_key": run.category_key,
        "policy_revision": run.policy_revision,
        "status": run.status,
        "lifecycle_status": automation_lifecycle_status(run.status),
        "dry_run": run.dry_run,
        "trigger_reason": run.trigger_reason,
        "case_ids": json.loads(run.case_ids_json),
        "frozen_input": json.loads(run.frozen_input_json),
        "result": json.loads(run.result_json or "{}"),
        "candidate_count": run.candidate_count,
        "estimated_cost_micros": run.estimated_cost_micros,
        "actual_cost_micros": run.actual_cost_micros,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
        "retryable": run.retryable,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "publishes_automatically": False,
    }


@app.get("/api/automation-runs")
def list_automation_runs(
    limit: int = 200,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(AutomationOptimizationRun)
        .order_by(
            AutomationOptimizationRun.created_at.desc(),
            AutomationOptimizationRun.id.desc(),
        )
        .limit(min(max(limit, 1), 500))
    ).all()
    return {"items": [_automation_run_payload(run) for run in runs]}


@app.post("/api/automation-runs/consume")
def consume_automation_runs(
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = consume_optimization_queue_once(
        db,
        worker_id=f"manual:{user.username}",
    )
    db.commit()
    return result


def _production_feedback_payload(
    event: ProductionFeedbackEvent,
    case: OptimizationCaseQueue | None,
) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "event_type": event.event_type,
        "source_system": event.source_system,
        "occurred_at": event.occurred_at,
        "payload_hash": event.payload_hash,
        "payload": json.loads(event.payload_json),
        "status": event.status,
        "optimization_case_id": case.id if case else None,
        "received_by": event.received_by,
        "received_at": event.received_at,
        "writes_production_database": False,
    }


@app.post("/api/production-feedback-events")
def create_production_feedback_event(
    payload: ProductionFeedbackRequest,
    sender: str = Depends(production_feedback_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        event, case, duplicate = ingest_production_feedback(
            db,
            event_id=payload.event_id,
            schema_version=payload.schema_version,
            event_type=payload.event_type,
            source_system=payload.source_system,
            occurred_at=payload.occurred_at,
            payload=payload.payload,
            received_by=sender,
        )
    except FeedbackConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    db.refresh(event)
    db.refresh(case)
    result = _production_feedback_payload(event, case)
    result["duplicate"] = duplicate
    return result


@app.get("/api/production-feedback-events")
def list_production_feedback_events(
    limit: int = 200,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    events = db.scalars(
        select(ProductionFeedbackEvent)
        .order_by(
            ProductionFeedbackEvent.received_at.desc(),
            ProductionFeedbackEvent.id.desc(),
        )
        .limit(min(max(limit, 1), 500))
    ).all()
    case_by_event = {
        case.source_event_id: case
        for case in db.scalars(
            select(OptimizationCaseQueue).where(
                OptimizationCaseQueue.source_event_id.in_(
                    [event.id for event in events]
                )
            )
        ).all()
    } if events else {}
    return {
        "items": [
            _production_feedback_payload(
                event, case_by_event.get(event.id)
            )
            for event in events
        ]
    }


@app.get("/api/production-feedback-config-status")
def production_feedback_config_status(
    _user: User = Depends(admin_user),
) -> dict[str, Any]:
    return {
        "configured": settings.production_feedback_token is not None,
        "authentication": "dedicated_bearer_token",
        "browser_session_accepted": False,
    }


def _content_record_payload(record: ContentRecord) -> dict[str, Any]:
    content_key = record.content_key
    if content_key is None and record.identity_status == "legacy_unverified":
        content_key = f"{record.source_system}:{record.source_content_id}"
    return {
        "id": record.id,
        "source_system": record.source_system,
        "content_id": record.source_content_id,
        "content_key": content_key,
        "category_key": record.category_key,
        "content_version": record.source_version,
        "source_res_type": record.source_res_type,
        "source_ll_id": record.source_ll_id,
        "source_res_id": record.source_res_id,
        "identity_status": record.identity_status,
        "identity_hash": record.identity_hash,
        "identity_verification_id": record.identity_verification_id,
        "asset_id": record.asset_id,
        "status": record.status,
        "source_occurred_at": record.source_occurred_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@app.post("/api/content-ingress/events")
def create_content_ingress_event(
    payload: ContentIngressRequest,
    _sender: str = Depends(content_ingress_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        event, record, duplicate = ingest_content_event(
            db,
            event_id=payload.event_id,
            schema_version=payload.schema_version,
            event_type=payload.event_type,
            source_system=payload.source_system,
            occurred_at=payload.occurred_at,
            payload=payload.payload,
            received_by=_sender,
        )
        if (
            payload.schema_version == "content-ingress-v2"
            and record.content_key is None
        ):
            package, package_created, routing_status = None, False, "blocked_identity"
            append_audit_event(
                db,
                category="content_ingress",
                action="blocked_identity",
                subject_type="content_ingress_event",
                subject_id=event.event_id,
                actor=_sender,
                payload={
                    "content_record_id": record.id,
                    "identity_status": record.identity_status,
                    "workflow_kind": "incremental",
                },
                event_key=f"content-ingress:{event.event_id}:blocked-identity",
            )
        else:
            package, package_created, routing_status = (
                route_content_event_to_incremental_package(
                    db,
                    event=event,
                    record=record,
                    duplicate=duplicate,
                    actor=_sender,
                )
            )
    except LabelIntegrationConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "INGRESS_EVENT_CONFLICT", "message": str(exc)}) from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    db.refresh(event)
    db.refresh(record)
    return {
        "event_id": event.event_id,
        "duplicate": duplicate,
        "event_status": event.status,
        "content": _content_record_payload(record),
        "material_required": record.status == "awaiting_material",
        "workflow_kind": "incremental",
        "material_package_id": package.id if package is not None else None,
        "package_created": package_created,
        "routing_status": routing_status,
        "writes_evaluation_job": False,
    }


@app.get("/api/content-ingress/records")
def list_content_ingress_records(
    limit: int = 200,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    records = db.scalars(
        select(ContentRecord).order_by(ContentRecord.updated_at.desc(), ContentRecord.id.desc()).limit(min(max(limit, 1), 500))
    ).all()
    return {"items": [_content_record_payload(record) for record in records]}


@app.get("/api/label-releases")
def list_label_releases(
    status: Literal["pending_review", "approved", "published", "rejected"] | None = None,
    limit: int = 200,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(LabelRelease).order_by(LabelRelease.requested_at.desc(), LabelRelease.id.desc())
    if status is not None:
        statement = statement.where(LabelRelease.status == status)
    releases = db.scalars(statement.limit(min(max(limit, 1), 500))).all()
    published_by_release = {
        item.release_id: item
        for item in db.scalars(
            select(PublishedLabel).where(PublishedLabel.release_id.in_([item.id for item in releases]))
        ).all()
    } if releases else {}
    return {"items": [release_payload(item, published_by_release.get(item.id)) for item in releases]}


@app.post("/api/semantic-tag-facts/{evaluation_id}/approve")
def approve_semantic_tag_facts(
    evaluation_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        facts = approve_semantic_facts(db, evaluation_id=evaluation_id, actor=user.username)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    db.commit()
    return {
        "evaluation_id": evaluation_id,
        "approved_count": len(facts),
        "facts": [
            {
                "id": fact.id,
                "asset_version_id": fact.asset_version_id,
                "field_key": fact.field_key,
                "fact_version": fact.fact_version,
                "field_status": fact.field_status,
                "status": fact.status,
                "payload_hash": fact.payload_hash,
            }
            for fact in facts
        ],
    }


@app.post("/api/label-releases")
def request_label_release(
    payload: LabelReleaseRequest,
    user: User = Depends(require_permission("releases:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        release, duplicate = create_release(
            db,
            release_key=payload.release_key,
            evaluation_id=payload.evaluation_id,
            content_key=payload.content_key,
            requested_by=user.username,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(LabelRelease).where(
                LabelRelease.release_key == payload.release_key
            )
        )
        if (
            existing is not None
            and existing.evaluation_id == payload.evaluation_id
            and (
                payload.content_key is None
                or existing.content_key == payload.content_key
            )
        ):
            return {"duplicate": True, "release": release_payload(existing)}
        raise HTTPException(
            status_code=409,
            detail="发布请求发生并发冲突，请刷新后重试",
        ) from None
    db.commit()
    db.refresh(release)
    return {"duplicate": duplicate, "release": release_payload(release)}


@app.post("/api/label-releases/{release_id}/approve-and-publish")
def approve_and_publish_label_release(
    release_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(LabelRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    try:
        published, duplicate = publish_release(db, release=release, actor=user.username)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        concurrent_release = db.get(LabelRelease, release_id)
        concurrent_published = db.scalar(
            select(PublishedLabel).where(
                PublishedLabel.release_id == release_id
            )
        )
        if (
            concurrent_release is not None
            and concurrent_published is not None
            and concurrent_release.status == "published"
        ):
            return {
                "duplicate": True,
                "release": release_payload(
                    concurrent_release,
                    concurrent_published,
                ),
            }
        raise HTTPException(
            status_code=409,
            detail="正式标签版本发生并发冲突，请刷新后重试",
        ) from None
    db.commit()
    db.refresh(release)
    db.refresh(published)
    return {"duplicate": duplicate, "release": release_payload(release, published)}


@app.post("/api/published-labels/{published_label_id}/rollback")
def rollback_published_label(
    published_label_id: int,
    payload: LabelRollbackRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target = db.get(PublishedLabel, published_label_id)
    if target is None:
        raise HTTPException(status_code=404, detail="历史发布标签不存在")
    target_release_id = target.release_id
    try:
        release, published, duplicate = rollback_release(
            db, target=target, rollback_key=payload.rollback_key, actor=user.username
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        concurrent_release = db.scalar(
            select(LabelRelease).where(
                LabelRelease.release_key == payload.rollback_key
            )
        )
        concurrent_published = (
            db.scalar(
                select(PublishedLabel).where(
                    PublishedLabel.release_id == concurrent_release.id
                )
            )
            if concurrent_release is not None
            else None
        )
        if (
            concurrent_release is not None
            and concurrent_release.source_release_id == target_release_id
            and concurrent_published is not None
        ):
            return {
                "duplicate": True,
                "release": release_payload(
                    concurrent_release,
                    concurrent_published,
                ),
            }
        raise HTTPException(
            status_code=409,
            detail="回滚版本发生并发冲突，请刷新后重试",
        ) from None
    db.commit()
    db.refresh(release)
    db.refresh(published)
    return {"duplicate": duplicate, "release": release_payload(release, published)}


@app.get("/api/integration-status")
def integration_status(_user: User = Depends(require_permission("releases:read"))) -> dict[str, Any]:
    return {
        "upstream_content_ingress": {
            "configured": settings.content_ingress_token is not None,
            "schema_versions": ["content-ingress-v1", "content-ingress-v2"],
            "events": ["content.created", "content.updated", "content.deleted"],
            "material_fetch": False,
        },
        "downstream_label_consumer": {
            "configured": settings.label_consumer_token is not None,
            "schema_version": "label-change-event-v1",
            "read_model": "published_labels",
            "cursor_api": "/api/consumer/v1/changes",
        },
        "external_writes_enabled": False,
    }


def _tag_demand_contract_payload(contract: TagDemandContract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "contract_key": contract.contract_key,
        "version": contract.version,
        "status": contract.status,
        "definition": json.loads(contract.definition_json),
        "contract_hash": contract.contract_hash,
        "approved_by": contract.approved_by,
        "approved_at": contract.approved_at,
        "created_by": contract.created_by,
        "created_at": contract.created_at,
    }


def _source_identity_verification_payload(
    verification: SourceIdentityVerification,
) -> dict[str, Any]:
    return {
        "id": verification.id,
        "contract_key": verification.contract_key,
        "source_system": verification.source_system,
        "key_fields": json.loads(verification.key_fields_json),
        "result": verification.result,
        "probe_hash": verification.probe_hash,
        "data_window": verification.data_window,
        "scoped_row_count": verification.scoped_row_count,
        "duplicate_key_count": verification.duplicate_key_count,
        "res_id_conflict_count": verification.res_id_conflict_count,
        "status": verification.status,
        "created_by": verification.created_by,
        "approved_by": verification.approved_by,
        "created_at": verification.created_at,
        "approved_at": verification.approved_at,
    }


@app.get("/api/source-identity-verifications")
def list_source_identity_verifications(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(SourceIdentityVerification).order_by(
            SourceIdentityVerification.created_at.desc(),
            SourceIdentityVerification.id.desc(),
        )
    ).all()
    return {"items": [_source_identity_verification_payload(row) for row in rows]}


@app.post("/api/source-identity-verifications", status_code=201)
def create_source_identity_verification(
    payload: SourceIdentityVerificationCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = SourceIdentityVerification(
        contract_key=payload.contract_key,
        source_system=payload.source_system,
        key_fields_json=canonical_json(list(payload.key_fields)),
        result=payload.result,
        probe_hash=payload.probe_hash,
        data_window=payload.data_window,
        scoped_row_count=payload.scoped_row_count,
        duplicate_key_count=payload.duplicate_key_count,
        res_id_conflict_count=payload.res_id_conflict_count,
        status="draft",
        created_by=user.username,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        category="source_identity_verification",
        action="created",
        subject_type="source_identity_verification",
        subject_id=str(row.id),
        actor=user.username,
        payload={
            "contract_key": row.contract_key,
            "source_system": row.source_system,
            "result": row.result,
            "probe_hash": row.probe_hash,
            "side_effects": {"sql_executed": False, "contract_activated": False},
        },
        event_key=f"source-identity-verification:created:{row.id}",
    )
    db.commit()
    db.refresh(row)
    return _source_identity_verification_payload(row)


@app.post("/api/source-identity-verifications/{verification_id}/approve")
def approve_source_identity_verification(
    verification_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(SourceIdentityVerification, verification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="源身份签认证据不存在")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="只有 draft 身份证据可以批准")
    try:
        key_fields = tuple(json.loads(row.key_fields_json))
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=409, detail="身份签认 key_fields 无效") from None
    if row.result != "verified":
        raise HTTPException(status_code=409, detail="冲突探查结果不能批准为 verified")
    if row.duplicate_key_count != 0 or row.res_id_conflict_count != 0:
        raise HTTPException(status_code=409, detail="重复键或 res_id 冲突未清零")
    if key_fields != ("res_type", "ll_id"):
        raise HTTPException(status_code=409, detail="身份签认键必须为 res_type + ll_id")

    candidates = db.scalars(
        select(TagDemandContract).where(
            TagDemandContract.contract_key == row.contract_key,
            TagDemandContract.status.in_(("draft", "candidate")),
        )
    ).all()
    matches_contract = False
    for contract in candidates:
        try:
            definition = validate_tag_demand_contract(
                json.loads(contract.definition_json)
            )
        except (SemanticTagContractError, json.JSONDecodeError):
            continue
        if (
            definition.schema_version == "tag-demand-contract-v2"
            and definition.source_identity is not None
            and definition.source_identity.source_system == row.source_system
            and definition.source_identity.identity_fields == key_fields
        ):
            matches_contract = True
            break
    if not matches_contract:
        raise HTTPException(
            status_code=409,
            detail="没有匹配的 draft/candidate v2 字段合同",
        )

    previous = db.scalars(
        select(SourceIdentityVerification).where(
            SourceIdentityVerification.contract_key == row.contract_key,
            SourceIdentityVerification.source_system == row.source_system,
            SourceIdentityVerification.status == "approved",
            SourceIdentityVerification.id != row.id,
        )
    ).all()
    for item in previous:
        item.status = "superseded"
    db.flush()
    row.status = "approved"
    row.approved_by = user.username
    row.approved_at = datetime.now(timezone.utc)
    append_audit_event(
        db,
        category="source_identity_verification",
        action="approved",
        subject_type="source_identity_verification",
        subject_id=str(row.id),
        actor=user.username,
        payload={
            "contract_key": row.contract_key,
            "source_system": row.source_system,
            "probe_hash": row.probe_hash,
            "superseded_ids": [item.id for item in previous],
            "side_effects": {"sql_executed": False, "contract_activated": False},
        },
        event_key=f"source-identity-verification:approved:{row.id}",
    )
    db.commit()
    db.refresh(row)
    return _source_identity_verification_payload(row)


@app.post("/api/source-identity-verifications/{verification_id}/reject")
def reject_source_identity_verification(
    verification_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(SourceIdentityVerification, verification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="源身份签认证据不存在")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="只有 draft 身份证据可以拒绝")
    row.status = "rejected"
    row.approved_by = user.username
    row.approved_at = datetime.now(timezone.utc)
    append_audit_event(
        db,
        category="source_identity_verification",
        action="rejected",
        subject_type="source_identity_verification",
        subject_id=str(row.id),
        actor=user.username,
        payload={"probe_hash": row.probe_hash},
        event_key=f"source-identity-verification:rejected:{row.id}",
    )
    db.commit()
    db.refresh(row)
    return _source_identity_verification_payload(row)


@app.get("/api/tag-demand-contracts")
def list_tag_demand_contracts(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contracts = db.scalars(
        select(TagDemandContract).order_by(
            TagDemandContract.contract_key.asc(),
            TagDemandContract.version.desc(),
        )
    ).all()
    active_versions = {
        contract.contract_key: contract.version
        for contract in contracts
        if contract.status == "active"
    }
    return {
        "items": [_tag_demand_contract_payload(contract) for contract in contracts],
        "active_versions": active_versions,
    }


@app.get("/api/tag-demand-contracts/{contract_id}")
def get_tag_demand_contract(
    contract_id: int,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = db.get(TagDemandContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="标签需求合同不存在")
    return _tag_demand_contract_payload(contract)


@app.post("/api/tag-demand-contracts", status_code=201)
def create_tag_demand_contract(
    payload: TagDemandContractCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        definition = validate_tag_demand_contract(payload.definition)
    except SemanticTagContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    latest = db.scalar(
        select(TagDemandContract)
        .where(TagDemandContract.contract_key == payload.contract_key)
        .order_by(TagDemandContract.version.desc())
        .limit(1)
    )
    version = (latest.version + 1) if latest else 1
    canonical_definition = definition.model_dump(mode="json")
    contract = TagDemandContract(
        contract_key=payload.contract_key,
        version=version,
        status=payload.status,
        definition_json=canonical_json(canonical_definition),
        contract_hash=canonical_contract_hash(definition),
        created_by=user.username,
    )
    db.add(contract)
    db.flush()
    db.commit()
    db.refresh(contract)
    return _tag_demand_contract_payload(contract)


@app.post(
    "/api/tag-demand-contracts/{contract_id}/bind-source-identity-verification"
)
def bind_source_identity_verification(
    contract_id: int,
    payload: SourceIdentityVerificationBindRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source = db.get(TagDemandContract, contract_id)
    if source is None:
        raise HTTPException(status_code=404, detail="标签需求合同不存在")
    if source.status not in {"draft", "candidate"}:
        raise HTTPException(status_code=409, detail="只有 draft/candidate 合同可以绑定证据")
    try:
        definition = validate_tag_demand_contract(json.loads(source.definition_json))
    except (SemanticTagContractError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=f"源合同无效：{exc}") from None
    if definition.schema_version != "tag-demand-contract-v2":
        raise HTTPException(status_code=409, detail="只有 v2 合同可以绑定身份签认证据")
    verification = db.get(SourceIdentityVerification, payload.verification_id)
    if verification is None or verification.status != "approved":
        raise HTTPException(status_code=409, detail="身份签认证据未批准或不存在")
    if definition.source_identity is None:
        raise HTTPException(status_code=409, detail="v2 合同缺少 source_identity")
    try:
        key_fields = tuple(json.loads(verification.key_fields_json))
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=409, detail="身份签认 key_fields 无效") from None
    if (
        verification.contract_key != source.contract_key
        or verification.source_system != definition.source_identity.source_system
        or key_fields != definition.source_identity.identity_fields
    ):
        raise HTTPException(status_code=409, detail="身份签认证据与字段合同不匹配")

    candidate_payload = definition.model_dump(mode="json")
    candidate_payload["source_identity"] = {
        **candidate_payload["source_identity"],
        "uniqueness_status": "verified",
        "verification_evidence_hash": verification.probe_hash,
    }
    try:
        candidate_definition = validate_tag_demand_contract(candidate_payload)
    except SemanticTagContractError as exc:
        raise HTTPException(status_code=409, detail=f"绑定后合同无效：{exc}") from None
    latest = db.scalar(
        select(TagDemandContract)
        .where(TagDemandContract.contract_key == source.contract_key)
        .order_by(TagDemandContract.version.desc())
        .limit(1)
    )
    row = TagDemandContract(
        contract_key=source.contract_key,
        version=(latest.version + 1) if latest else 1,
        status="candidate",
        definition_json=canonical_json(candidate_definition.model_dump(mode="json")),
        contract_hash=canonical_contract_hash(candidate_definition),
        created_by=user.username,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        category="tag_demand_contract",
        action="source_identity_bound",
        subject_type="tag_demand_contract",
        subject_id=str(row.id),
        actor=user.username,
        payload={
            "source_contract_id": source.id,
            "verification_id": verification.id,
            "probe_hash": verification.probe_hash,
            "contract_activated": False,
        },
        event_key=f"tag-demand-contract:source-identity-bound:{row.id}",
    )
    db.commit()
    db.refresh(row)
    return _tag_demand_contract_payload(row)


@app.post("/api/tag-demand-contracts/{contract_id}/activate")
def activate_tag_demand_contract(
    contract_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = db.get(TagDemandContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="标签需求合同不存在")
    if contract.status != "candidate":
        raise HTTPException(status_code=409, detail="只有 candidate 合同可以显式激活")
    try:
        definition = validate_tag_demand_contract(json.loads(contract.definition_json))
    except (SemanticTagContractError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=f"合同签名或内容无效：{exc}") from None
    if not definition.category_applicability or not definition.projection_targets:
        raise HTTPException(status_code=409, detail="合同缺少字段适用性或投影目标，不能激活")
    if definition.schema_version == "tag-demand-contract-v2":
        if (
            definition.source_identity is None
            or definition.source_identity.uniqueness_status != "verified"
        ):
            raise HTTPException(status_code=409, detail="源身份唯一性尚未签认")
        approved = db.scalar(
            select(SourceIdentityVerification).where(
                SourceIdentityVerification.contract_key == contract.contract_key,
                SourceIdentityVerification.source_system
                == definition.source_identity.source_system,
                SourceIdentityVerification.status == "approved",
                SourceIdentityVerification.probe_hash
                == definition.source_identity.verification_evidence_hash,
            )
        )
        if approved is None:
            raise HTTPException(
                status_code=409,
                detail="字段合同引用的身份签认证据不存在或已失效",
            )
    current = db.scalars(
        select(TagDemandContract).where(
            TagDemandContract.contract_key == contract.contract_key,
            TagDemandContract.status == "active",
        )
    ).all()
    now = datetime.now(timezone.utc)
    for previous in current:
        previous.status = "retired"
    contract.status = "active"
    contract.approved_by = user.username
    contract.approved_at = now
    append_audit_event(
        db,
        category="tag_demand_contract",
        action="activated",
        subject_type="tag_demand_contract",
        subject_id=str(contract.id),
        actor=user.username,
        payload={
            "contract_key": contract.contract_key,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
            "side_effects": {
                "evaluation_jobs": False,
                "label_releases": False,
                "stock_reruns": False,
                "projection_manifests": False,
                "outbox_events": False,
            },
        },
        event_key=f"tag-demand-contract:activated:{contract.id}",
    )
    db.commit()
    db.refresh(contract)
    return _tag_demand_contract_payload(contract)


@app.get("/api/field-demand-contracts")
def list_field_demand_contracts(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contracts = db.scalars(
        select(FieldDemandContract).order_by(
            FieldDemandContract.contract_key.asc(),
            FieldDemandContract.version.desc(),
            FieldDemandContract.id.desc(),
        )
    ).all()
    return {"items": [field_demand_contract_payload(item) for item in contracts]}


@app.post("/api/field-demand-contracts")
def create_field_demand_contract_api(
    payload: FieldDemandContractCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        contract = create_field_demand_contract(
            db,
            contract_key=payload.contract_key,
            category_key=payload.category_key,
            consumer_key=payload.consumer_key,
            owner=payload.owner,
            fields=payload.fields,
            thresholds=payload.thresholds,
            status=payload.status,
            created_by=user.username,
        )
    except FieldDemandContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    db.refresh(contract)
    return field_demand_contract_payload(contract)


@app.get("/api/upstream-source-contracts")
def list_upstream_source_contracts(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contracts = db.scalars(
        select(UpstreamSourceContract).order_by(
            UpstreamSourceContract.contract_key.asc(),
            UpstreamSourceContract.version.desc(),
            UpstreamSourceContract.id.desc(),
        )
    ).all()
    return {"items": [source_contract_payload(item) for item in contracts]}


@app.post("/api/upstream-source-contracts")
def create_upstream_source_contract_api(
    payload: UpstreamSourceContractCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        contract = create_upstream_source_contract(
            db,
            contract_key=payload.contract_key,
            adapter_key=payload.adapter_key,
            source_system=payload.source_system,
            category_key=payload.category_key,
            connection_locator=payload.connection_locator,
            secret_reference=payload.secret_reference,
            field_mappings=payload.field_mappings,
            cursor_definition=payload.cursor_definition,
            page_size=payload.page_size,
            read_only=payload.read_only,
            schema_fingerprint=payload.schema_fingerprint,
            owner=payload.owner,
            status=payload.status,
            created_by=user.username,
        )
    except ReadOnlySourceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    db.commit()
    db.refresh(contract)
    return source_contract_payload(contract)


@app.post("/api/upstream-source-contracts/{contract_id}/poll")
def poll_upstream_source_api(
    contract_id: int,
    _payload: UpstreamPollRequest,
    _user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(UpstreamSourceContract, contract_id) is None:
        raise HTTPException(status_code=404, detail="只读来源合同不存在")
    raise HTTPException(
        status_code=503,
        detail={
            "code": "SOURCE_ADAPTER_UNAVAILABLE",
            "message": "真实来源适配器与 secret 引用尚未在运行环境激活",
        },
    )


@app.get("/api/upstream-read-runs")
def list_upstream_read_runs(
    limit: int = 100,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(UpstreamReadRun)
        .order_by(UpstreamReadRun.id.desc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return {"items": [source_run_payload(item) for item in runs]}


@app.get("/api/assets/{asset_id}/versions")
def list_asset_versions(
    asset_id: int,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(Asset, asset_id) is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    versions = db.scalars(
        select(AssetVersion)
        .where(AssetVersion.asset_id == asset_id)
        .order_by(AssetVersion.version.desc(), AssetVersion.id.desc())
    ).all()
    return {"items": [asset_version_payload(item) for item in versions]}


@app.get("/api/shadow-projection-targets")
def list_shadow_projection_targets(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    targets = db.scalars(
        select(ShadowProjectionTarget).order_by(
            ShadowProjectionTarget.target_key.asc(),
            ShadowProjectionTarget.version.desc(),
            ShadowProjectionTarget.id.desc(),
        )
    ).all()
    return {"items": [shadow_projection_target_payload(item) for item in targets]}


@app.post("/api/shadow-projection-targets")
def create_shadow_projection_target_api(
    payload: ShadowProjectionTargetCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        target = create_shadow_projection_target(
            db,
            target_key=payload.target_key,
            adapter_key=payload.adapter_key,
            connection_locator=payload.connection_locator,
            secret_reference=payload.secret_reference,
            schema_name=payload.schema_name,
            table_name=payload.table_name,
            environment=payload.environment,
            shadow_only=payload.shadow_only,
            owner=payload.owner,
            schema_fingerprint=payload.schema_fingerprint,
            status=payload.status,
            created_by=user.username,
        )
    except ShadowProjectionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    db.commit()
    db.refresh(target)
    return shadow_projection_target_payload(target)


@app.get("/api/shadow-projection-runs")
def list_shadow_projection_runs(
    limit: int = 100,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(ShadowProjectionRun)
        .order_by(ShadowProjectionRun.id.desc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return {"items": [shadow_projection_run_payload(item) for item in runs]}


@app.get("/api/shadow-projection-runs/{run_id}")
def get_shadow_projection_run(
    run_id: int,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(ShadowProjectionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="影子投影运行不存在")
    return shadow_projection_run_payload(run)


@app.post("/api/shadow-projection-runs")
def create_shadow_projection_run_api(
    payload: ShadowProjectionRunCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = db.get(ProjectionContract, payload.projection_contract_id)
    field_contract = db.get(FieldDemandContract, payload.field_contract_id)
    target = db.get(ShadowProjectionTarget, payload.target_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="影子投影合同不存在")
    if field_contract is None:
        raise HTTPException(status_code=404, detail="字段需求合同不存在")
    if target is None:
        raise HTTPException(status_code=404, detail="影子目标不存在")
    run = enqueue_shadow_projection_run(
        db,
        projection_contract=contract,
        field_contract=field_contract,
        target=target,
        max_rows=payload.max_rows,
        actor=user.username,
    )
    db.commit()
    db.refresh(run)
    return shadow_projection_run_payload(run)


@app.post("/api/shadow-projection-runs/{run_id}/retry")
def retry_shadow_projection_run_api(
    run_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(ShadowProjectionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="影子投影运行不存在")
    try:
        retry_shadow_projection_run(db, run=run, actor=user.username)
    except ShadowProjectionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    db.commit()
    db.refresh(run)
    return shadow_projection_run_payload(run)


@app.post("/api/shadow-projection-runs/{run_id}/rollback")
def rollback_shadow_projection_run_api(
    run_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(ShadowProjectionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="影子投影运行不存在")
    try:
        adapter = resolve_configured_shadow_projection_adapter(run.target)
        rollback_shadow_projection_run(
            db, run=run, adapter=adapter, actor=user.username
        )
    except ShadowProjectionError as exc:
        db.rollback()
        status_code = 503 if exc.code == "SHADOW_ADAPTER_UNAVAILABLE" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    db.commit()
    db.refresh(run)
    return shadow_projection_run_payload(run)


@app.get("/api/projection-contracts")
def list_projection_contracts(
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contracts = db.scalars(
        select(ProjectionContract).order_by(
            ProjectionContract.contract_key.asc(),
            ProjectionContract.version.desc(),
        )
    ).all()
    latest_reconciliation_by_contract: dict[int, ProjectionReconciliation] = {}
    for record in db.scalars(
        select(ProjectionReconciliation).order_by(
            ProjectionReconciliation.contract_id.asc(),
            ProjectionReconciliation.id.desc(),
        )
    ).all():
        latest_reconciliation_by_contract.setdefault(record.contract_id, record)
    items = []
    for contract in contracts:
        payload = projection_contract_payload(contract)
        latest = latest_reconciliation_by_contract.get(contract.id)
        payload["latest_reconciliation"] = (
            projection_reconciliation_payload(latest) if latest else None
        )
        items.append(payload)
    return {"items": items}


@app.post("/api/projection-contracts")
def create_projection_contract(
    payload: ProjectionContractCreateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        contract = create_contract_version(
            db,
            contract_key=payload.contract_key,
            target_role=payload.target_role,
            table_name=payload.table_name,
            environment=payload.environment,
            primary_key=payload.primary_key,
            field_mappings=payload.field_mappings,
            input_versions=payload.input_versions,
            mode=payload.mode,
            idempotency_key_template=payload.idempotency_key_template,
            checkpoint=payload.checkpoint,
            reconciliation=payload.reconciliation,
            rollback=payload.rollback,
            owner=payload.owner,
            status=payload.status,
            created_by=user.username,
            adapter_key=payload.adapter_key,
            target_key=payload.target_key,
            write_policy=payload.write_policy,
            category_key=payload.category_key,
            field_contract_id=payload.field_contract_id,
            max_batch_size=payload.max_batch_size,
        )
    except ProjectionContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    db.refresh(contract)
    return projection_contract_payload(contract)


@app.post("/api/projection-contracts/{contract_id}/manifest")
def create_projection_manifest(
    contract_id: int,
    _user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = db.get(ProjectionContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="投影合同不存在")
    manifest, payload = build_projection_manifest(db, contract=contract)
    db.commit()
    db.refresh(manifest)
    return {**payload, "id": manifest.id, "created_at": manifest.created_at}


@app.post("/api/projection-contracts/{contract_id}/reconcile")
def reconcile_projection_contract(
    contract_id: int,
    _user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = db.get(ProjectionContract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="投影合同不存在")
    manifest, _payload = build_projection_manifest(db, contract=contract)
    adapter = LocalProjectionAdapter()
    adapter.apply(db, contract=contract, manifest=manifest)
    result = adapter.reconcile(db, contract=contract, manifest=manifest)
    record = persist_reconciliation(
        db,
        contract=contract,
        manifest=manifest,
        result=result,
    )
    db.commit()
    db.refresh(record)
    return projection_reconciliation_payload(record)


@app.post(
    "/api/published-labels/export",
    response_class=Response,
    responses={
        200: {
            "description": "正式标签导出文件",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/csv": {},
                "application/json": {},
            },
            "headers": {
                "Content-Disposition": {
                    "description": "附件文件名",
                    "schema": {"type": "string"},
                },
                "X-Export-Row-Count": {
                    "description": "导出的正式标签行数",
                    "schema": {"type": "integer"},
                },
            },
        },
        413: {"description": "导出超过 10,000 条"},
    },
)
def export_published_labels(
    payload: PublishedLabelExportRequest,
    user: User = Depends(require_permission("releases:read")),
    db: Session = Depends(get_db),
) -> Response:
    statement = select(PublishedLabel).order_by(
        PublishedLabel.content_key.asc(),
        PublishedLabel.version.desc(),
        PublishedLabel.id.desc(),
    )
    if payload.scope == "current":
        statement = statement.where(PublishedLabel.status == "published")
    if payload.category_key is not None:
        statement = statement.where(PublishedLabel.category_key == payload.category_key)
    if payload.published_from is not None:
        statement = statement.where(PublishedLabel.published_at >= payload.published_from)
    if payload.published_to is not None:
        statement = statement.where(PublishedLabel.published_at <= payload.published_to)
    labels = db.scalars(statement.limit(10_001)).all()
    if len(labels) > 10_000:
        raise HTTPException(status_code=413, detail="导出超过 10000 条，请按类目或发布时间缩小范围")
    export = build_export(labels, format=payload.format, scope=payload.scope)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    category_fragment = payload.category_key or "all"
    filename = f"published-labels-{payload.scope}-{category_fragment}-{timestamp}.{export.extension}"
    append_audit_event(
        db,
        category="label_export",
        action="downloaded",
        subject_type="published_labels",
        subject_id=f"{payload.scope}:{category_fragment}:{timestamp}",
        actor=user.username,
        payload={
            "format": payload.format,
            "scope": payload.scope,
            "category_key": payload.category_key,
            "published_from": payload.published_from.isoformat() if payload.published_from else None,
            "published_to": payload.published_to.isoformat() if payload.published_to else None,
            "row_count": len(labels),
        },
        event_key=f"label-export:{user.id}:{uuid.uuid4().hex}",
    )
    db.commit()
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Row-Count": str(len(labels)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _consumer_label_payload(label: PublishedLabel) -> dict[str, Any]:
    return {
        "id": label.id,
        "content_key": label.content_key,
        "category_key": label.category_key,
        "version": label.version,
        "label_schema_version": label.label_schema_version,
        "payload_hash": label.payload_hash,
        "label": json.loads(label.label_payload_json),
        "published_at": label.published_at,
    }


@app.get("/api/consumer/v1/labels/{content_key:path}")
def get_published_label_for_consumer(
    content_key: str,
    response: Response,
    _sender: str = Depends(label_consumer_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    label = db.scalar(
        select(PublishedLabel).where(
            PublishedLabel.content_key == content_key,
            PublishedLabel.status == "published",
        ).order_by(PublishedLabel.version.desc())
    )
    if label is None:
        raise HTTPException(status_code=404, detail="当前没有已发布标签")
    response.headers["ETag"] = f'"{label.payload_hash}"'
    return _consumer_label_payload(label)


@app.get("/api/consumer/v1/changes")
def list_label_changes_for_consumer(
    after: int = 0,
    limit: int = 100,
    _sender: str = Depends(label_consumer_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if after < 0:
        raise HTTPException(status_code=422, detail="after 不能小于 0")
    events = db.scalars(
        select(LabelOutboxEvent).where(LabelOutboxEvent.id > after).order_by(LabelOutboxEvent.id.asc()).limit(min(max(limit, 1), 500))
    ).all()
    high_watermark = db.scalar(select(func.max(LabelOutboxEvent.id))) or 0
    return {
        "schema_version": "label-change-event-v1",
        "items": [json.loads(item.payload_json) | {"event_id": item.event_id, "sequence": item.id, "created_at": item.created_at} for item in events],
        "next_cursor": events[-1].id if events else after,
        "high_watermark": high_watermark,
        "has_more": bool(events and events[-1].id < high_watermark),
    }


@app.post("/api/consumer/v1/checkpoints")
def save_consumer_checkpoint(
    payload: ConsumerCheckpointRequest,
    _sender: str = Depends(label_consumer_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    high_watermark = db.scalar(select(func.max(LabelOutboxEvent.id))) or 0
    if payload.cursor > high_watermark:
        raise HTTPException(status_code=409, detail="cursor 尚未存在于发布事件流")
    checkpoint = db.scalar(select(ConsumerSyncCheckpoint).where(ConsumerSyncCheckpoint.consumer_name == payload.consumer_name))
    if checkpoint is None:
        checkpoint = ConsumerSyncCheckpoint(consumer_name=payload.consumer_name, cursor=payload.cursor)
        db.add(checkpoint)
    elif payload.cursor < checkpoint.cursor:
        raise HTTPException(status_code=409, detail="consumer cursor 只能前进，不能回退")
    else:
        checkpoint.cursor = payload.cursor
        checkpoint.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(checkpoint)
    return {"consumer_name": checkpoint.consumer_name, "cursor": checkpoint.cursor, "updated_at": checkpoint.updated_at}


@app.get("/api/consumer/v1/reconciliation")
def consumer_reconciliation(
    _sender: str = Depends(label_consumer_sender),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    high_watermark = db.scalar(select(func.max(LabelOutboxEvent.id))) or 0
    published_count = db.scalar(select(func.count(PublishedLabel.id)).where(PublishedLabel.status == "published")) or 0
    latest_version = db.scalar(select(func.max(PublishedLabel.version))) or 0
    return {
        "schema_version": "label-reconciliation-v1",
        "outbox_high_watermark": high_watermark,
        "current_published_label_count": published_count,
        "max_content_version": latest_version,
        "external_sync_is_eventual": True,
        "external_writes_enabled": False,
    }


def _benchmark_payload(
    experiment: ModelBenchmarkExperiment,
    variants: list[ModelBenchmarkVariant],
) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "experiment_key": experiment.experiment_key,
        "name": experiment.name,
        "status": experiment.status,
        "execution_mode": experiment.execution_mode,
        "cohort_hash": experiment.cohort_hash,
        "snapshot_hash": experiment.snapshot_hash,
        "frozen_snapshot": json.loads(experiment.frozen_snapshot_json),
        "quality_gate": json.loads(experiment.quality_gate_json),
        "max_round_cost_micros": experiment.max_round_cost_micros,
        "actual_cost_micros": experiment.actual_cost_micros,
        "decision": json.loads(experiment.decision_json or "{}"),
        "created_by": experiment.created_by,
        "created_at": experiment.created_at,
        "started_at": experiment.started_at,
        "finished_at": experiment.finished_at,
        "real_model_calls_enabled": experiment.execution_mode == "real",
        "variants": [
            {
                "id": variant.id,
                "model_key": variant.model_key,
                "provider": variant.provider,
                "model_id": variant.model_id,
                "model_config_id": variant.model_config_id,
                "pricing": json.loads(variant.pricing_json),
                "status": variant.status,
                "metrics": json.loads(variant.metrics_json or "{}"),
                "error_message": variant.error_message,
                "input_tokens": variant.input_tokens,
                "output_tokens": variant.output_tokens,
                "total_tokens": variant.total_tokens,
                "actual_cost_micros": variant.actual_cost_micros,
            }
            for variant in variants
        ],
    }


def _benchmark_config_snapshot(config: ModelConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "provider": config.provider,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "structured_output": config.structured_output,
        "input_micros_per_million_tokens":
            config.input_micros_per_million_tokens,
        "output_micros_per_million_tokens":
            config.output_micros_per_million_tokens,
        "max_input_tokens": config.max_input_tokens,
        "benchmark_enabled": config.benchmark_enabled,
        "transport_fingerprint": hashlib.sha256(
            canonical_json({
                "provider": config.provider,
                "base_url": config.base_url,
                "api_path": config.api_path,
            }).encode("utf-8")
        ).hexdigest(),
        "updated_at": config.updated_at.isoformat(),
    }


def _sha256_file(path: Path) -> str:
    # 委托给 nas_storage 的缓存实现：横评冻结与执行两处（9579/9937 附近）会对
    # 同一个 NAS 文件重复校验，而 resolve_asset_path 内部已经算过一次摘要，
    # N 个素材原本要跨 SMB 全量读 2N 遍。
    return sha256_file_cached(path)


def _real_benchmark_truth(
    db: Session, asset: Asset
) -> dict[str, Any]:
    results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.asset_id == asset.id)
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
    ).all()
    for result in results:
        if latest_review_for_result(result) is not None:
            return truth_from_result(result)
    raise ValueError(f"素材 #{asset.id} 缺少最终人工真值")


def _real_benchmark_estimate(
    configs: list[ModelConfig], sample_count: int
) -> int:
    total = 0
    for config in configs:
        if (
            config.max_input_tokens <= 0
            or config.input_micros_per_million_tokens <= 0
            or config.output_micros_per_million_tokens <= 0
        ):
            raise ValueError("真实横评模型缺少输入上限或计价配置")
        total += 2 * sample_count * token_cost_micros(
            config.max_input_tokens,
            config.max_tokens,
            {
                "input_micros_per_million_tokens":
                    config.input_micros_per_million_tokens,
                "output_micros_per_million_tokens":
                    config.output_micros_per_million_tokens,
            },
        )
    return total


@app.post("/api/model-benchmarks")
def create_model_benchmark(
    payload: BenchmarkCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if payload.execution_mode == "real" and not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可创建真实横评")
    existing = db.scalar(
        select(ModelBenchmarkExperiment).where(
            ModelBenchmarkExperiment.experiment_key
            == payload.experiment_key
        )
    )
    assets = db.scalars(
        select(Asset).where(Asset.id.in_(payload.cohort_asset_ids))
    ).all()
    if {asset.id for asset in assets} != set(payload.cohort_asset_ids):
        raise HTTPException(status_code=422, detail="冻结 cohort 含不存在素材")
    bundle = db.get(StrategyBundle, payload.strategy_bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="StrategyBundle 不存在")
    plan = db.scalar(
        select(AgentPlanVersion).where(
            AgentPlanVersion.version == bundle.agent_plan_version
        )
    )
    if plan is None:
        raise HTTPException(status_code=422, detail="AgentPlan 快照不存在")
    configs_by_id = {
        config.id: config
        for config in db.scalars(
            select(ModelConfig).where(
                ModelConfig.id.in_(
                    [
                        item.model_config_id
                        for item in payload.variants
                        if item.model_config_id is not None
                    ]
                )
            )
        ).all()
    }
    if payload.execution_mode == "real":
        try:
            real_configs = [
                configs_by_id[int(item.model_config_id)]
                for item in payload.variants
            ]
        except KeyError:
            raise HTTPException(
                status_code=422, detail="真实横评模型配置不存在"
            ) from None
        if any(
            not config.benchmark_enabled or not config.encrypted_api_key
            for config in real_configs
        ):
            raise HTTPException(
                status_code=409,
                detail="真实横评模型必须由管理员显式启用并配置密钥",
            )
        try:
            predicted_cost = _real_benchmark_estimate(
                real_configs, len(assets)
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if predicted_cost > payload.max_round_cost_micros:
            raise HTTPException(
                status_code=409,
                detail="真实横评预测成本超过单轮上限",
            )
        try:
            _snapshot_json, prompt_a, prompt_b = _strategy_snapshot_for_bundle(
                db, bundle
            )
            sample_snapshots = []
            for asset in sorted(assets, key=lambda item: item.id):
                try:
                    image_path = resolve_asset_path(asset, settings)
                except NasStorageError as exc:
                    raise ValueError(str(exc)) from exc
                if not image_path.is_file() or _sha256_file(image_path) != asset.sha256:
                    raise ValueError(f"素材 #{asset.id} 文件与冻结哈希不匹配")
                sample_snapshots.append(
                    {
                        "asset_id": asset.id,
                        "asset_sha256": asset.sha256,
                        "image": {
                            "mime_type": asset.mime_type,
                            "size_bytes": asset.size_bytes,
                            "width": asset.width,
                            "height": asset.height,
                        },
                        "truth": _real_benchmark_truth(db, asset),
                    }
                )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
    else:
        prompt_a = prompt_b = None
        sample_snapshots = []
        predicted_cost = 0

    frozen_variants = []
    for item in sorted(payload.variants, key=lambda value: value.model_key):
        config = configs_by_id.get(item.model_config_id or -1)
        if payload.execution_mode == "real" and config is not None:
            frozen_variants.append(
                {
                    "model_key": item.model_key,
                    "model_config": _benchmark_config_snapshot(config),
                    "pricing": {
                        "input_micros_per_million_tokens":
                            config.input_micros_per_million_tokens,
                        "output_micros_per_million_tokens":
                            config.output_micros_per_million_tokens,
                        "human_review_cost_micros":
                            item.human_review_cost_micros,
                    },
                }
            )
        else:
            frozen_variants.append(
                {
                    "model_key": item.model_key,
                    "provider": item.provider,
                    "model_id": item.model_id,
                    "pricing": {
                        "input_micros_per_million_tokens":
                            item.input_micros_per_million_tokens,
                        "output_micros_per_million_tokens":
                            item.output_micros_per_million_tokens,
                        "human_review_cost_micros":
                            item.human_review_cost_micros,
                    },
                }
            )
    frozen = {
        "schema_version": "model-benchmark-v1",
        "cohort_asset_ids": sorted(payload.cohort_asset_ids),
        "strategy_bundle": {
            "id": bundle.id,
            "canonical_hash": bundle.canonical_hash,
            "model_id": bundle.model_id,
            "model_config": json.loads(bundle.model_config_snapshot),
            "prompt_a_version": bundle.prompt_a_version,
            "prompt_b_version": bundle.prompt_b_version,
            "rubric_version": bundle.rubric_version,
            "engine_version": bundle.engine_version,
            "risk_review_version": bundle.risk_review_version,
            "agent_plan_version": bundle.agent_plan_version,
        },
        "agent_plan": {
            "version": plan.version,
            "plan": json.loads(plan.plan_json),
        },
        "variants": frozen_variants,
        "samples": sample_snapshots,
        "prompt_a": (
            {
                "version": prompt_a.version,
                "rubric_version": prompt_a.rubric_version,
                "system_prompt": prompt_a.system_prompt,
                "user_prompt": prompt_a.user_prompt,
            }
            if prompt_a is not None else None
        ),
        "prompt_b": (
            {
                "version": prompt_b.version,
                "rubric_version": prompt_b.rubric_version,
                "system_prompt": prompt_b.system_prompt,
                "user_prompt": prompt_b.user_prompt,
            }
            if prompt_b is not None else None
        ),
        "predicted_cost_micros": predicted_cost,
        "max_round_cost_micros": payload.max_round_cost_micros,
    }
    quality_gate = {
        "min_quality_accuracy": payload.min_quality_accuracy,
        "max_p0_p1_errors": payload.max_p0_p1_errors,
        "min_retry_stability": payload.min_retry_stability,
        "low_confidence_threshold": payload.low_confidence_threshold,
        "selection": "quality_gate_then_pareto_composite_cost",
        "approved_for_real_execution": payload.quality_gate_approved,
    }
    frozen_hash = benchmark_snapshot_hash(frozen)
    if existing is not None:
        if (
            existing.snapshot_hash != frozen_hash
            or json.loads(existing.quality_gate_json) != quality_gate
        ):
            raise HTTPException(
                status_code=409,
                detail="同一 experiment_key 的冻结快照不一致",
            )
        variants = db.scalars(
            select(ModelBenchmarkVariant).where(
                ModelBenchmarkVariant.experiment_id == existing.id
            )
        ).all()
        return _benchmark_payload(existing, variants)

    experiment = ModelBenchmarkExperiment(
        experiment_key=payload.experiment_key,
        name=payload.name,
        status="draft",
        execution_mode=payload.execution_mode,
        cohort_hash=hashlib.sha256(
            canonical_json(sorted(payload.cohort_asset_ids)).encode("utf-8")
        ).hexdigest(),
        snapshot_hash=frozen_hash,
        frozen_snapshot_json=canonical_json(frozen),
        quality_gate_json=canonical_json(quality_gate),
        max_round_cost_micros=payload.max_round_cost_micros,
        created_by=user.username,
    )
    db.add(experiment)
    db.flush()
    for item in payload.variants:
        config = configs_by_id.get(item.model_config_id or -1)
        db.add(
            ModelBenchmarkVariant(
                experiment_id=experiment.id,
                model_key=item.model_key,
                provider=config.provider if config else str(item.provider),
                model_id=config.model_id if config else str(item.model_id),
                model_config_id=config.id if config else None,
                pricing_json=canonical_json(
                    {
                        "input_micros_per_million_tokens":
                            config.input_micros_per_million_tokens
                            if config else item.input_micros_per_million_tokens,
                        "output_micros_per_million_tokens":
                            config.output_micros_per_million_tokens
                            if config else item.output_micros_per_million_tokens,
                        "human_review_cost_micros":
                            item.human_review_cost_micros,
                    }
                ),
            )
        )
    db.commit()
    variants = db.scalars(
        select(ModelBenchmarkVariant).where(
            ModelBenchmarkVariant.experiment_id == experiment.id
        )
    ).all()
    return _benchmark_payload(experiment, variants)


@app.get("/api/model-benchmarks")
def list_model_benchmarks(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiments = db.scalars(
        select(ModelBenchmarkExperiment)
        .order_by(
            ModelBenchmarkExperiment.created_at.desc(),
            ModelBenchmarkExperiment.id.desc(),
        )
        .limit(min(max(limit, 1), 500))
    ).all()
    items = []
    for experiment in experiments:
        variants = db.scalars(
            select(ModelBenchmarkVariant).where(
                ModelBenchmarkVariant.experiment_id == experiment.id
            )
        ).all()
        items.append(_benchmark_payload(experiment, variants))
    return {"items": items}


@app.get("/api/model-benchmarks/{experiment_id}")
def get_model_benchmark(
    experiment_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ModelBenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="横评实验不存在")
    variants = db.scalars(
        select(ModelBenchmarkVariant).where(
            ModelBenchmarkVariant.experiment_id == experiment.id
        )
    ).all()
    return _benchmark_payload(experiment, variants)


@app.post("/api/model-benchmarks/{experiment_id}/run-test")
def run_model_benchmark_test(
    experiment_id: int,
    payload: BenchmarkRunRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ModelBenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="横评实验不存在")
    if experiment.execution_mode != "test":
        raise HTTPException(
            status_code=409,
            detail="执行器未启用；只有显式测试模式可运行测试替身",
        )
    try:
        run_benchmark_experiment(
            db,
            experiment=experiment,
            adapter=DeterministicBenchmarkAdapter(
                observations=dict(payload.test_observations)
            ),
            actor=user.username,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    db.commit()
    variants = db.scalars(
        select(ModelBenchmarkVariant).where(
            ModelBenchmarkVariant.experiment_id == experiment.id
        )
    ).all()
    return _benchmark_payload(experiment, variants)


@app.post("/api/model-benchmarks/{experiment_id}/run-real")
def run_model_benchmark_real(
    experiment_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ModelBenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="横评实验不存在")
    if experiment.execution_mode != "real":
        raise HTTPException(status_code=409, detail="横评实验未显式配置真实模式")
    try:
        quality_gate = json.loads(experiment.quality_gate_json)
        snapshot = json.loads(experiment.frozen_snapshot_json)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=409, detail="横评冻结数据无法解析") from None
    if quality_gate.get("approved_for_real_execution") is not True:
        raise HTTPException(status_code=409, detail="横评质量门尚未批准真实执行")
    if benchmark_snapshot_hash(snapshot) != experiment.snapshot_hash:
        raise HTTPException(status_code=409, detail="横评冻结快照哈希不匹配")
    frozen_strategy = snapshot.get("strategy_bundle")
    if not isinstance(frozen_strategy, dict):
        raise HTTPException(status_code=409, detail="横评冻结策略缺失")
    bundle = db.get(StrategyBundle, frozen_strategy.get("id"))
    if (
        bundle is None
        or bundle.canonical_hash != frozen_strategy.get("canonical_hash")
    ):
        raise HTTPException(status_code=409, detail="横评冻结策略版本不匹配")
    try:
        _snapshot_json, prompt_a, prompt_b = _strategy_snapshot_for_bundle(db, bundle)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    for key, prompt in (("prompt_a", prompt_a), ("prompt_b", prompt_b)):
        if snapshot.get(key) != {
            "version": prompt.version,
            "rubric_version": prompt.rubric_version,
            "system_prompt": prompt.system_prompt,
            "user_prompt": prompt.user_prompt,
        }:
            raise HTTPException(status_code=409, detail="横评冻结提示词版本不匹配")
    frozen_plan = snapshot.get("agent_plan")
    plan = db.scalar(
        select(AgentPlanVersion).where(
            AgentPlanVersion.version == bundle.agent_plan_version
        )
    )
    if (
        not isinstance(frozen_plan, dict)
        or plan is None
        or frozen_plan != {"version": plan.version, "plan": json.loads(plan.plan_json)}
    ):
        raise HTTPException(status_code=409, detail="横评冻结 AgentPlan 版本不匹配")
    frozen_cohort = snapshot.get("cohort_asset_ids")
    frozen_samples = snapshot.get("samples")
    try:
        cohort_ids = sorted(int(asset_id) for asset_id in frozen_cohort)
        sample_ids = sorted(
            int(item["asset_id"])
            for item in frozen_samples
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=409, detail="横评冻结 cohort 不匹配") from None
    if (
        not isinstance(frozen_cohort, list)
        or not isinstance(frozen_samples, list)
        or len(sample_ids) != len(frozen_samples)
        or sample_ids != cohort_ids
        or hashlib.sha256(canonical_json(cohort_ids).encode("utf-8")).hexdigest()
        != experiment.cohort_hash
    ):
        raise HTTPException(status_code=409, detail="横评冻结 cohort 不匹配")
    variants = db.scalars(
        select(ModelBenchmarkVariant)
        .where(ModelBenchmarkVariant.experiment_id == experiment.id)
        .order_by(ModelBenchmarkVariant.model_key.asc())
    ).all()
    frozen_variants = {
        item.get("model_key"): item
        for item in snapshot.get("variants", [])
        if isinstance(item, dict)
    }
    if set(frozen_variants) != set(MODEL_KEYS) or {
        variant.model_key for variant in variants
    } != set(MODEL_KEYS):
        raise HTTPException(status_code=409, detail="横评冻结模型组合不匹配")
    configs: dict[str, ModelConfig] = {}
    for variant in variants:
        config = db.get(ModelConfig, variant.model_config_id)
        if (
            config is None
            or not config.benchmark_enabled
            or not config.encrypted_api_key
        ):
            raise HTTPException(status_code=409, detail="横评模型配置不可执行")
        frozen_variant = frozen_variants[variant.model_key]
        if frozen_variant.get("model_config") != _benchmark_config_snapshot(config):
            raise HTTPException(status_code=409, detail="横评模型配置已发生变化")
        configs[variant.model_key] = config
    asset_paths: dict[int, Path] = {}
    for sample in snapshot.get("samples", []):
        asset = db.get(Asset, int(sample["asset_id"]))
        if asset is None or asset.sha256 != sample["asset_sha256"]:
            raise HTTPException(status_code=409, detail="横评冻结素材已发生变化")
        try:
            path = resolve_asset_path(asset, settings)
        except NasStorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not path.is_file() or _sha256_file(path) != sample["asset_sha256"]:
            raise HTTPException(status_code=409, detail="横评冻结图片哈希不匹配")
        asset_paths[asset.id] = path
    try:
        predicted = _real_benchmark_estimate(
            list(configs.values()), len(asset_paths)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if predicted > experiment.max_round_cost_micros:
        raise HTTPException(status_code=409, detail="横评预测成本超过单轮上限")
    try:
        run_benchmark_experiment(
            db,
            experiment=experiment,
            adapter=OpenAICompatibleBenchmarkAdapter(
                configs=configs,
                asset_paths=asset_paths,
                round_cost_limit_micros=experiment.max_round_cost_micros,
            ),
            actor=user.username,
        )
    except Exception:
        db.commit()
        raise HTTPException(status_code=502, detail="真实横评执行失败；请查看脱敏状态") from None
    db.commit()
    return _benchmark_payload(experiment, variants)


@app.get("/api/audit-events")
def list_audit_events(
    category: str | None = None,
    limit: int = 200,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(AuditEvent).order_by(
        AuditEvent.created_at.desc(), AuditEvent.id.desc()
    )
    if category:
        statement = statement.where(AuditEvent.category == category)
    events = db.scalars(statement.limit(min(max(limit, 1), 500))).all()
    return {
        "items": [
            {
                "id": event.id,
                "event_key": event.event_key,
                "category": event.category,
                "action": event.action,
                "subject_type": event.subject_type,
                "subject_id": event.subject_id,
                "actor": event.actor,
                "payload": json.loads(event.payload_json),
                "created_at": event.created_at,
            }
            for event in events
        ]
    }


@app.post(
    "/api/evaluations/{evaluation_id}/review",
    deprecated=True,
    summary="兼容历史样本二审与仲裁",
)
def create_review(
    evaluation_id: int,
    payload: ReviewRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    if evaluation.review_panel is not None:
        raise HTTPException(
            status_code=409,
            detail="该结果由初审组处理，请使用盲审投票或主审裁决接口",
        )
    if payload.expected_stage == "initial":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVIEW_PANEL_REQUIRED",
                "message": (
                    "新图片初审必须在初审组内完成；请先创建初审组并提交盲审投票"
                ),
                "review_stage": evaluation.review_stage,
                "review_revision": evaluation.review_revision,
            },
        )
    if (
        evaluation.review_stage != payload.expected_stage
        or evaluation.review_revision != payload.expected_review_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVIEW_SNAPSHOT",
                "message": "审核阶段或修订号已变化，请刷新后重试",
                "review_stage": evaluation.review_stage,
                "review_revision": evaluation.review_revision,
            },
        )
    correction_data = [item.model_dump() for item in payload.corrections]
    corrected_score = None
    corrected_level = None
    if payload.decision == "corrected":
        try:
            dimension_corrections = [
                item for item in correction_data
                if item.get("target_type") == "dimension"
            ]
            if dimension_corrections:
                recalculated = _calculate_review_dimension_score(
                    evaluation,
                    dimension_corrections,
                )
                corrected_score = recalculated.get("score")
                corrected_level = recalculated.get("level")
            else:
                corrected_score = evaluation.score
                corrected_level = evaluation.level
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if corrected_score is None or corrected_level is None:
            raise HTTPException(status_code=400, detail="当前结果无法自动计算正式等级")
    current_answer = _review_standard_answer(
        decision=payload.decision,
        corrected_level=corrected_level,
        corrected_score=corrected_score,
        corrections=correction_data,
    )
    if payload.expected_stage == "initial":
        next_stage = (
            "secondary"
            if _requires_secondary_review(evaluation, payload.decision)
            else "completed"
        )
    elif payload.expected_stage == "secondary":
        initial_review = next(
            (
                review
                for review in reversed(evaluation.reviews)
                if review.stage == "initial"
            ),
            None,
        )
        if initial_review is None:
            raise HTTPException(status_code=409, detail="缺少初审记录，不能执行二审")
        initial_answer = _review_standard_answer(
            decision=initial_review.decision,
            corrected_level=initial_review.corrected_level,
            corrected_score=initial_review.corrected_score,
            corrections=json.loads(initial_review.corrections_json or "[]"),
        )
        next_stage = (
            "completed" if current_answer == initial_answer else "arbitration"
        )
    else:
        initial_review = next(
            (
                review
                for review in reversed(evaluation.reviews)
                if review.stage == "initial"
            ),
            None,
        )
        secondary_review = next(
            (
                review
                for review in reversed(evaluation.reviews)
                if review.stage == "secondary"
            ),
            None,
        )
        if initial_review is None or secondary_review is None:
            raise HTTPException(
                status_code=409,
                detail="缺少历史初审或二审记录，不能执行兼容仲裁",
            )
        next_stage = "completed"

    next_revision = evaluation.review_revision + 1
    needs_review = next_stage != "completed" or payload.decision == "rejected"
    updated = db.execute(
        update(EvaluationResult)
        .where(
            EvaluationResult.id == evaluation_id,
            EvaluationResult.review_stage == payload.expected_stage,
            EvaluationResult.review_revision == payload.expected_review_revision,
        )
        .values(
            review_stage=next_stage,
            review_revision=next_revision,
            needs_review=needs_review,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVIEW_SNAPSHOT",
                "message": "审核记录已被其他操作更新，请刷新后重试",
            },
        )
    review = HumanReview(
        evaluation_id=evaluation_id,
        stage=payload.expected_stage,
        reviewer_name=user.username,
        decision=payload.decision,
        note=payload.note,
        corrected_level=corrected_level,
        corrected_score=corrected_score,
        corrections_json=json.dumps(correction_data, ensure_ascii=False),
    )
    evaluation.reviews.append(review)
    db.commit()
    db.refresh(review)
    return {
        "id": review.id,
        "stage": review.stage,
        "review_stage": next_stage,
        "review_revision": next_revision,
        "completed": next_stage == "completed",
    }


def _sample_set_summary(sample_set: SampleSet) -> dict[str, Any]:
    truth_complete = sum(1 for item in sample_set.items if bool(json.loads(item.truth_json or "{}")))
    latest_truth_revision = max(
        (item.truth_revision for item in sample_set.items),
        default=0,
    )
    return {
        "id": sample_set.id,
        "name": sample_set.name,
        "description": sample_set.description,
        "kind": sample_set.kind,
        "category_key": sample_set.category_key,
        "status": sample_set.status,
        "item_count": len(sample_set.items),
        "truth_complete_count": truth_complete,
        "latest_truth_revision": latest_truth_revision,
        "created_by": sample_set.created_by,
        "created_at": sample_set.created_at,
    }


def _sample_set_item_payload(item: SampleSetItem) -> dict[str, Any]:
    source = _result_payload(item.source_result)
    truth = json.loads(item.truth_json or "{}")
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "asset_name": item.asset.original_name,
        "image_url": f"/api/assets/{item.asset_id}/file",
        "expected_level": item.expected_level,
        "expected_category": item.expected_category,
        "note": item.note,
        "truth": truth,
        "truth_revision": item.truth_revision,
        "truth_updated_by": item.truth_updated_by,
        "truth_updated_at": item.truth_updated_at,
        "source_model_id": item.source_result.model_id,
        "source_level": source.get("final_level") if source else None,
        "added_by": item.added_by,
        "created_at": item.created_at,
    }


def _assert_sample_set_is_mutable(sample_set: SampleSet) -> None:
    if sample_set.kind == "golden" and sample_set.status == "locked":
        raise HTTPException(
            status_code=409,
            detail=(
                "已锁定黄金集不可直接修改；请先复制形成新草稿版本后再调整"
                f"（POST /api/sample-sets 传 source_sample_set_id={sample_set.id}，"
                "条目与人工纠偏真值会一并复制，新集为 draft 可直接编辑）。"
            ),
        )


def _create_regression_runs(
    db: Session,
    *,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
    created_by: str,
    trigger_prompt_id: int | None = None,
    sample_set_id: int | None = None,
    threshold: float = 0.9,
) -> list[int]:
    if prompt_a.category_key != prompt_b.category_key:
        raise HTTPException(status_code=409, detail="回归提示词 A/B 属于不同评测类目")
    query = select(SampleSet).where(
        SampleSet.kind == "golden",
        SampleSet.status == "locked",
        SampleSet.category_key == prompt_a.category_key,
    )
    if sample_set_id is not None:
        query = query.where(SampleSet.id == sample_set_id)
    sample_sets = db.scalars(query.order_by(SampleSet.created_at.asc())).all()
    run_ids: list[int] = []
    for sample_set in sample_sets:
        eligible = [item for item in sample_set.items if json.loads(item.truth_json or "{}")]
        if not eligible:
            continue
        run = PromptRegressionRun(
            name=f"{prompt_a.version} + {prompt_b.version} · {sample_set.name}",
            sample_set_id=sample_set.id,
            trigger_prompt_id=trigger_prompt_id,
            prompt_a_id=prompt_a.id,
            prompt_b_id=prompt_b.id,
            threshold=threshold,
            total=len(eligible),
            status="queued",
            created_by=created_by,
        )
        db.add(run)
        db.flush()
        for sample_item in eligible:
            regression_item = PromptRegressionItem(
                run_id=run.id,
                sample_item_id=sample_item.id,
                status="queued",
            )
            db.add(regression_item)
            db.flush()
            job = EvaluationJob(
                asset_id=sample_item.asset_id,
                category_key=sample_set.category_key,
                prompt_a_id=prompt_a.id,
                prompt_b_id=prompt_b.id,
                regression_item_id=regression_item.id,
                queue_class="validation",
                origin_queue_class="validation",
                batch_key=f"regression:{run.id}",
            )
            db.add(job)
            db.flush()
            regression_item.job_id = job.id
        run_ids.append(run.id)
    return run_ids


def _frozen_v3_dimension_summary(execution: dict[str, Any]) -> dict[str, Any] | None:
    """Return the human-facing dimension contract frozen with a baseline run.

    ``dimension_schema_ref`` remains in the v3 track contract for routing and
    historical traceability.  Its key count is not the number of dimensions
    that the v3 engine actually scores, so UI labels must use the frozen
    ``subcategory_dimensions`` payload instead.
    """

    bundle = execution.get("v3_authoritative_bundle")
    if not isinstance(bundle, dict):
        return None
    contract = bundle.get("contract")
    dimensions_by_track = bundle.get("subcategory_dimensions")
    if not isinstance(contract, dict) or not isinstance(dimensions_by_track, dict):
        return None
    spec_version = contract.get("spec_version")
    classification = contract.get("track_classification")
    tracks = classification.get("tracks") if isinstance(classification, dict) else None
    if not isinstance(spec_version, str) or not spec_version.strip() or not isinstance(tracks, list):
        return None

    track_summaries: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_key = track.get("key")
        track_config = dimensions_by_track.get(track_key)
        if not isinstance(track_key, str) or not isinstance(track_config, dict):
            continue
        dimension_keys: set[str] = set()
        anonymous_dimensions = 0
        for group_key in ("common_group", "specific_group"):
            group = track_config.get(group_key)
            if not isinstance(group, dict):
                continue
            schema_definition = group.get("schema_definition")
            definitions = (
                schema_definition.get("dimensions")
                if isinstance(schema_definition, dict)
                else group.get("dimensions")
            )
            if not isinstance(definitions, list):
                continue
            for definition in definitions:
                if not isinstance(definition, dict):
                    continue
                dimension_key = definition.get("key")
                if isinstance(dimension_key, str) and dimension_key.strip():
                    dimension_keys.add(dimension_key.strip())
                else:
                    anonymous_dimensions += 1
        label = track.get("label")
        track_summaries.append(
            {
                "key": track_key,
                "label": label if isinstance(label, str) and label.strip() else track_key,
                "dimension_count": len(dimension_keys) + anonymous_dimensions,
            }
        )
    if not track_summaries:
        return None
    summary: dict[str, Any] = {
        "spec_version": spec_version.strip(),
        "tracks": track_summaries,
    }
    config_revision = bundle.get("config_revision")
    if isinstance(config_revision, int):
        summary["revision"] = config_revision
    candidate_revision_id = bundle.get("candidate_revision_id")
    if isinstance(candidate_revision_id, int):
        summary["revision_id"] = candidate_revision_id
        summary["candidate_revision_id"] = candidate_revision_id
    contract_hash = bundle.get("contract_hash")
    if isinstance(contract_hash, str) and contract_hash.strip():
        summary["contract_hash"] = contract_hash
    return summary


def _baseline_run_selection(
    run: BaselineRegressionRun,
) -> dict[str, Any]:
    try:
        strategy = safe_strategy_snapshot_payload(
            run.strategy_snapshot_json
        )
    except ValueError:
        strategy = {}

    def prompt_identity(stage: str) -> dict[str, Any] | None:
        raw = strategy.get(f"prompt_{stage.casefold()}")
        if not isinstance(raw, dict):
            return None
        return {
            "id": raw.get("id"),
            "stage": raw.get("stage") or stage,
            "name": raw.get("name"),
            "version": raw.get("version"),
            "rubric_version": raw.get("rubric_version"),
        }

    try:
        execution = json.loads(run.execution_snapshot_json or "{}")
    except json.JSONDecodeError:
        execution = {}
    frozen_selection = execution.get("dimension_selection")
    frozen_contract = execution.get("dimension_contract")
    frozen_v3_contract = _frozen_v3_dimension_summary(execution)
    if isinstance(frozen_selection, dict):
        return {
            "schema_version": "baseline-run-selection-v2",
            "category_key": execution.get("category_key"),
            "execution_mode": execution.get("execution_mode", "structured"),
            "prompt_mode": (execution.get("pipeline_config") or {}).get("prompt_mode"),
            "prompt_a": prompt_identity("A"),
            "prompt_b": prompt_identity("B"),
            "dimension": {
                **frozen_selection,
                "manual_selection_supported": True,
                "contract": frozen_contract if isinstance(frozen_contract, dict) else None,
                "v3_contract": frozen_v3_contract,
            },
        }
    dimension_set = strategy.get("dimension_schema_set")
    raw_schemas = (
        dimension_set.get("schemas")
        if isinstance(dimension_set, dict)
        else []
    )
    schemas = [
        {
            "schema_key": item.get("schema_key"),
            "version": item.get("version"),
            "schema_type": item.get("schema_type"),
            "family_key": item.get("family_key"),
            "canonical_hash": item.get("canonical_hash"),
        }
        for item in raw_schemas
        if isinstance(item, dict)
    ]
    return {
        "schema_version": "baseline-run-selection-v1",
        "execution_mode": execution.get("execution_mode", "structured"),
        "prompt_a": prompt_identity("A"),
        "prompt_b": prompt_identity("B"),
        "dimension": {
            "mode": "strategy_snapshot",
            "manual_selection_supported": False,
            "v3_contract": frozen_v3_contract,
            "route_policy_id": strategy.get(
                "dimension_route_policy_id"
            ),
            "schemas": schemas,
        },
    }


def _baseline_run_summary(run: BaselineRegressionRun) -> dict[str, Any]:
    try:
        metrics = json.loads(run.metrics_json or "{}")
    except json.JSONDecodeError:
        metrics = {}
    return {
        "id": run.id,
        "baseline_set_id": run.baseline_set_id,
        "category_key": run.category_key,
        "workflow_kind": "stock",
        "sequence_no": run.sequence_no,
        "previous_run_id": run.previous_run_id,
        "strategy_bundle_id": run.strategy_bundle_id,
        "strategy_canonical_id": run.strategy_bundle.canonical_hash,
        "status": run.status,
        "total": run.total,
        "completed": run.completed,
        "valid_predictions": run.valid_predictions,
        "failed": run.failed,
        "metrics": metrics,
        "selection": _baseline_run_selection(run),
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


def _baseline_set_summary(
    baseline_set: BaselineSet,
    *,
    item_count: int | None = None,
) -> dict[str, Any]:
    latest_run = baseline_set.runs[-1] if baseline_set.runs else None
    return {
        "id": baseline_set.id,
        "name": baseline_set.name,
        "category_key": baseline_set.category_key,
        "description": baseline_set.description,
        "default_expected_level": baseline_set.default_expected_level,
        "fingerprint": baseline_set.fingerprint,
        "item_count": len(baseline_set.items) if item_count is None else item_count,
        "run_count": len(baseline_set.runs),
        "latest_run": _baseline_run_summary(latest_run) if latest_run else None,
        "frozen": True,
        "created_by": baseline_set.created_by,
        "created_at": baseline_set.created_at,
    }


@app.get("/api/baseline-sets")
def list_baseline_sets(
    category_key: str | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(BaselineSet)
    if category_key is not None:
        _category_profile(db, category_key)
        statement = statement.where(BaselineSet.category_key == category_key)
    sets = db.scalars(
        statement.order_by(
            BaselineSet.created_at.desc(), BaselineSet.id.desc()
        )
    ).all()
    set_ids = [item.id for item in sets]
    item_counts = {
        baseline_set_id: count
        for baseline_set_id, count in db.execute(
            select(
                BaselineSetItem.baseline_set_id,
                func.count(BaselineSetItem.id),
            )
            .where(BaselineSetItem.baseline_set_id.in_(set_ids))
            .group_by(BaselineSetItem.baseline_set_id)
        ).all()
    } if set_ids else {}
    return {
        "items": [
            _baseline_set_summary(item, item_count=item_counts.get(item.id, 0))
            for item in sets
        ]
    }


@app.post("/api/baseline-sets")
def create_baseline_set(
    payload: BaselineSetCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    name = payload.name.strip()
    _category_profile(db, payload.category_key, require_active=True)
    if db.scalar(select(BaselineSet.id).where(BaselineSet.name == name)):
        raise HTTPException(status_code=409, detail="基准集名称已存在")

    requested_items = list(payload.items)
    expected_sources: dict[int, tuple[str, dict[str, Any]]] = {}
    source_names: dict[tuple[int, int], str] = {}
    if payload.source_package_id is not None:
        source_package = db.get(MaterialPackage, payload.source_package_id)
        if source_package is None:
            raise HTTPException(status_code=404, detail="所选素材包不存在")
        if source_package.category_key != payload.category_key:
            raise HTTPException(status_code=409, detail="素材包与基准集流水线类目不一致")
        seen_asset_ids: set[int] = set()
        requested_items = []
        for package_item in source_package.items:
            if (
                package_item.asset.status == "deleted"
                or package_item.asset_id in seen_asset_ids
            ):
                continue
            seen_asset_ids.add(package_item.asset_id)
            source_names[(source_package.id, package_item.asset_id)] = (
                package_item.original_name
            )
            suggestion = filename_level_suggestion(package_item.original_name)
            override = payload.expected_level_overrides.get(
                package_item.asset_id
            )
            suggested_level = suggestion["suggested_level"]
            expected_level = (
                override
                or suggested_level
                or payload.default_expected_level
            )
            expected_sources[package_item.asset_id] = (
                "manual_override"
                if override
                else "filename"
                if suggested_level
                else "batch_default",
                suggestion,
            )
            requested_items.append(
                BaselineSetItemCreateRequest(
                    asset_id=package_item.asset_id,
                    expected_level=expected_level,
                    source_package_id=source_package.id,
                )
            )
        if not requested_items:
            raise HTTPException(status_code=400, detail="所选素材包没有可用素材")
        if len(requested_items) > 10_000:
            raise HTTPException(
                status_code=400,
                detail="单个基准集最多包含 10000 张唯一素材",
            )
        unknown_override_ids = (
            set(payload.expected_level_overrides) - seen_asset_ids
        )
        if unknown_override_ids:
            raise HTTPException(
                status_code=400,
                detail="逐张等级覆盖包含不属于所选素材包的素材",
            )

    asset_ids = [item.asset_id for item in requested_items]
    assets = db.scalars(
        select(Asset).where(
            Asset.id.in_(asset_ids),
            Asset.status != "deleted",
        )
    ).all()
    assets_by_id = {asset.id: asset for asset in assets}
    if set(assets_by_id) != set(asset_ids):
        raise HTTPException(
            status_code=404,
            detail="部分基准素材不存在或已删除",
        )
    if any(asset.category_key != payload.category_key for asset in assets):
        raise HTTPException(status_code=409, detail="基准集不允许混入其他流水线类目素材")
    manual_source_pairs = {
        (item.source_package_id, item.asset_id)
        for item in requested_items
        if item.source_package_id is not None
    } - set(source_names)
    if manual_source_pairs:
        source_package_ids = {pair[0] for pair in manual_source_pairs}
        source_asset_ids = {pair[1] for pair in manual_source_pairs}
        for package_item in db.scalars(
            select(MaterialPackageItem).where(
                MaterialPackageItem.package_id.in_(source_package_ids),
                MaterialPackageItem.asset_id.in_(source_asset_ids),
            )
        ).all():
            source_names.setdefault(
                (package_item.package_id, package_item.asset_id),
                package_item.original_name,
            )
    frozen_items: list[dict[str, Any]] = []
    for requested in requested_items:
        asset = assets_by_id[requested.asset_id]
        source_name = asset.original_name
        if requested.source_package_id is not None:
            source_name = source_names.get(
                (requested.source_package_id, asset.id),
                "",
            )
            if not source_name:
                raise HTTPException(
                    status_code=400,
                    detail=f"素材 #{asset.id} 不属于所选素材包",
                )
        suggestion = filename_level_suggestion(source_name)
        level_source, frozen_suggestion = expected_sources.get(
            asset.id,
            (
                "manual_override"
                if requested.expected_level
                else "filename"
                if suggestion["suggested_level"]
                else "batch_default",
                suggestion,
            ),
        )
        expected_level = (
            requested.expected_level
            or suggestion["suggested_level"]
            or payload.default_expected_level
        )
        asset_snapshot = {
            "schema_version": "baseline-asset-v1",
            "asset_id": asset.id,
            "category_key": payload.category_key,
            "name": source_name,
            "sha256": asset.sha256,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "width": asset.width,
            "height": asset.height,
            "source_package_id": requested.source_package_id,
            "expected_level_source": level_source,
            "filename_level_suggestion": frozen_suggestion,
            "created_at": asset.created_at.isoformat(),
        }
        frozen_items.append(
            {
                "asset": asset,
                "expected_level": expected_level,
                "source_package_id": requested.source_package_id,
                "asset_snapshot": asset_snapshot,
            }
        )

    fingerprint = baseline_set_fingerprint(
        (
            {
                "asset_id": entry["asset"].id,
                "asset_sha256": entry["asset"].sha256,
                "expected_level": entry["expected_level"],
            }
            for entry in frozen_items
        ),
        category_key=payload.category_key,
    )
    baseline_set = BaselineSet(
        name=name,
        category_key=payload.category_key,
        description=payload.description.strip(),
        default_expected_level=payload.default_expected_level,
        fingerprint=fingerprint,
        created_by=user.username,
    )
    db.add(baseline_set)
    db.flush()
    for entry in frozen_items:
        db.add(
            BaselineSetItem(
                baseline_set_id=baseline_set.id,
                asset_id=entry["asset"].id,
                source_package_id=entry["source_package_id"],
                expected_level=entry["expected_level"],
                asset_snapshot_json=baseline_canonical_json(
                    entry["asset_snapshot"]
                ),
            )
        )
    append_audit_event(
        db,
        category="baseline_regression",
        action="baseline_set_created",
        subject_type="baseline_set",
        subject_id=baseline_set.id,
        actor=user.username,
        payload={
            "fingerprint": fingerprint,
            "item_count": len(frozen_items),
            "default_expected_level": payload.default_expected_level,
            "category_key": payload.category_key,
            "source_package_id": payload.source_package_id,
        },
        event_key=f"baseline-set:{baseline_set.id}:created",
    )
    db.commit()
    db.refresh(baseline_set)
    return _baseline_set_summary(baseline_set, item_count=len(frozen_items))


@app.post("/api/baseline-sets/inspiration-balanced-100")
def create_inspiration_balanced_baseline_set(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from .inspiration_auto_correction import ensure_inspiration_balanced_golden_set

    try:
        baseline_set, report = ensure_inspiration_balanced_golden_set(
            db,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "summary": _baseline_set_summary(
            baseline_set,
            item_count=int(report["item_count"]),
        ),
        **report,
    }


@app.get("/api/baseline-sets/inspiration-balanced-sample/rebuild-survey")
def survey_inspiration_balanced_rebuild(
    include_deleted: bool = False,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read-only: how much rated material a rebuild could draw from now."""

    from .inspiration_auto_correction import (
        survey_inspiration_balanced_candidates,
    )

    return survey_inspiration_balanced_candidates(
        db, include_deleted=include_deleted
    )


@app.post("/api/baseline-sets/inspiration-balanced-sample/rebuild")
def rebuild_inspiration_balanced_sample(
    payload: BalancedSampleRebuildRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Freeze a new balanced sample that can include newly rated material.

    This never rewrites the original balanced set: that set is frozen truth
    with runs attached, so a rebuild lands in a new set named from its own
    parameters.
    """

    request = payload or BalancedSampleRebuildRequest()
    from .inspiration_auto_correction import (
        rebuild_inspiration_balanced_golden_set,
    )

    try:
        baseline_set, report = rebuild_inspiration_balanced_golden_set(
            db,
            created_by=user.username,
            per_level=request.per_level,
            strategy=request.strategy,
            seed=request.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "summary": _baseline_set_summary(
            baseline_set,
            item_count=int(report["item_count"]),
        ),
        **report,
    }


@app.get("/api/baseline-sets/{baseline_set_id}")
def baseline_set_detail(
    baseline_set_id: int,
    include_items: bool = True,
    limit: int = 1000,
    offset: int = 0,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    baseline_set = db.get(BaselineSet, baseline_set_id)
    if baseline_set is None:
        raise HTTPException(status_code=404, detail="基准集不存在")
    item_total = db.scalar(
        select(func.count(BaselineSetItem.id)).where(
            BaselineSetItem.baseline_set_id == baseline_set.id
        )
    ) or 0
    frozen_items = (
        db.scalars(
            select(BaselineSetItem)
            .where(BaselineSetItem.baseline_set_id == baseline_set.id)
            .order_by(BaselineSetItem.id.asc())
            .offset(max(0, offset))
            .limit(min(1000, max(1, limit)))
        ).all()
        if include_items
        else []
    )
    return {
        "summary": _baseline_set_summary(baseline_set, item_count=item_total),
        "items": [
            {
                "id": item.id,
                "asset_id": item.asset_id,
                "source_package_id": item.source_package_id,
                "expected_level": item.expected_level,
                "asset": json.loads(item.asset_snapshot_json),
                "image_url": f"/api/assets/{item.asset_id}/file",
                "frozen": True,
            }
            for item in frozen_items
        ],
        "pagination": {
            "offset": max(0, offset),
            "limit": min(1000, max(1, limit)),
            "total": item_total,
        },
        "runs": [
            _baseline_run_summary(run)
            for run in reversed(baseline_set.runs)
        ],
    }


@app.post("/api/baseline-sets/{baseline_set_id}/runs")
def create_baseline_run(
    baseline_set_id: int,
    payload: BaselineRunCreateRequest | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request = payload or BaselineRunCreateRequest()
    baseline_set = db.get(BaselineSet, baseline_set_id)
    if baseline_set is None:
        raise HTTPException(status_code=404, detail="基准集不存在")
    if not baseline_set.items:
        raise HTTPException(status_code=409, detail="空基准集不能创建 run")
    all_frozen_items = list(baseline_set.items)
    if request.baseline_item_ids is not None:
        item_by_id = {item.id: item for item in all_frozen_items}
        missing_ids = [
            item_id
            for item_id in request.baseline_item_ids
            if item_id not in item_by_id
        ]
        if missing_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "baseline_items_invalid",
                    "message": "所选 item 不属于当前冻结基准集",
                    "item_ids": missing_ids,
                },
            )
        frozen_items = [item_by_id[item_id] for item_id in request.baseline_item_ids]
    else:
        frozen_items = all_frozen_items
    category_mismatch_asset_ids = [
        item.asset_id
        for item in frozen_items
        if item.asset.category_key != baseline_set.category_key
    ]
    if (
        request.category_context is not None
        and request.category_context.category_key != baseline_set.category_key
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "category_context_mismatch",
                "message": "显式类目上下文必须与冻结基准集类目一致",
            },
        )
    if (
        request.category_context is None
        and (
            request.baseline_item_ids is not None
            or category_mismatch_asset_ids
        )
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "category_context_required",
                "message": "冻结子集或素材主类目不一致时必须显式确认基准集类目上下文",
            },
        )
    category_context = {
        "source": "baseline_set",
        "category_key": baseline_set.category_key,
        "selected_baseline_item_ids": [item.id for item in frozen_items],
        "asset_category_mismatches": category_mismatch_asset_ids,
    }
    profile = _category_profile(db, baseline_set.category_key, require_active=True)
    running = db.scalar(
        select(BaselineRegressionRun.id).where(
            BaselineRegressionRun.baseline_set_id == baseline_set.id,
            BaselineRegressionRun.status == "running",
        )
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="该基准集上一轮仍在运行")

    frozen_v3_bundle = _required_baseline_v3_bundle(
        db,
        baseline_set.category_key,
        request.candidate_revision_id,
    )
    if (
        request.dimension_schema_id is not None
        or request.dimension_mode != "category_default"
    ):
        raise HTTPException(
            status_code=410,
            detail={
                "code": "legacy_dimension_write_retired",
                "message": (
                    "旧类目维度写入口已下线，请使用"
                    "“类目评测 v3 合同配置”。"
                ),
            },
        )
    model_config = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.active.is_(True))
        .order_by(ModelConfig.id.asc())
        .limit(1)
    )
    pipeline = _profile_pipeline(profile, db)
    explicit_pair = request.prompt_a_id is not None
    single_prompt_mode = request.prompt_id is not None or (
        not explicit_pair and pipeline["prompt_mode"] == "single"
    )
    if single_prompt_mode:
        selected_prompt_id = request.prompt_id or profile.prompt_a_id
        prompt_a = (
            db.get(PromptVersion, selected_prompt_id)
            if selected_prompt_id is not None
            else None
        )
        if prompt_a is None:
            raise HTTPException(status_code=409, detail="该类目尚未绑定可用的单提示词版本")
        if prompt_a.status == "archived" or prompt_a.pipeline_scope not in {
            "baseline_regression", "shared"
        }:
            raise HTTPException(status_code=422, detail="所选单提示词不允许用于基准回归")
        if prompt_a.stage != "A":
            raise HTTPException(
                status_code=422,
                detail="单提示词模式只能选择阶段为 A 的提示词版本",
            )
        if prompt_a.category_key != baseline_set.category_key:
            raise HTTPException(status_code=422, detail="所选单提示词属于其他流水线类目")
        prompt_b = None
    elif request.prompt_a_id is not None:
        assert request.prompt_b_id is not None
        prompt_a = db.get(PromptVersion, request.prompt_a_id)
        prompt_b = db.get(PromptVersion, request.prompt_b_id)
        missing = [
            label
            for label, prompt in (("A", prompt_a), ("B", prompt_b))
            if prompt is None
        ]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"所选 {'/'.join(missing)} 提示词版本不存在",
            )
        if prompt_a.stage != "A" or prompt_b.stage != "B":
            raise HTTPException(
                status_code=422,
                detail="所选提示词阶段不匹配：A 选择器只能选 A，B 选择器只能选 B",
            )
        if any(
            prompt.status == "archived"
            or prompt.pipeline_scope not in {"baseline_regression", "shared"}
            for prompt in (prompt_a, prompt_b)
        ):
            raise HTTPException(status_code=422, detail="所选提示词不允许用于基准回归")
        if (
            prompt_a.category_key != baseline_set.category_key
            or prompt_b.category_key != baseline_set.category_key
        ):
            raise HTTPException(status_code=422, detail="所选提示词包含其他流水线类目版本")
    else:
        prompt_a = (
            db.get(PromptVersion, profile.prompt_a_id)
            if profile.prompt_a_id is not None
            else None
        )
        prompt_b = (
            db.get(PromptVersion, profile.prompt_b_id)
            if profile.prompt_b_id is not None
            else None
        )
        if prompt_a is None or prompt_b is None:
            prompt_a = db.scalar(
                select(PromptVersion)
                .where(
                    PromptVersion.category_key == baseline_set.category_key,
                    PromptVersion.stage == "A",
                    PromptVersion.status == "published",
                    PromptVersion.pipeline_scope.in_(("baseline_regression", "shared")),
                )
                .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
                .limit(1)
            )
            prompt_b = db.scalar(
                select(PromptVersion)
                .where(
                    PromptVersion.category_key == baseline_set.category_key,
                    PromptVersion.stage == "B",
                    PromptVersion.status == "published",
                    PromptVersion.pipeline_scope.in_(("baseline_regression", "shared")),
                )
                .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
                .limit(1)
            )
        if (
            (prompt_a is not None and prompt_a.category_key != baseline_set.category_key)
            or (prompt_b is not None and prompt_b.category_key != baseline_set.category_key)
        ):
            raise HTTPException(status_code=409, detail="类目当前发布提示词归属不一致")
    if (
        prompt_a is not None
        and (prompt_a.status == "archived" or prompt_a.pipeline_scope not in {"baseline_regression", "shared"})
    ) or (
        prompt_b is not None
        and (prompt_b.status == "archived" or prompt_b.pipeline_scope not in {"baseline_regression", "shared"})
    ):
        raise HTTPException(status_code=409, detail="当前类目绑定的提示词未发布到基准回归流水线")
    if model_config is None or prompt_a is None or (
        prompt_b is None and not single_prompt_mode
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "当前已发布单提示词或启用模型配置不完整"
                if single_prompt_mode
                else "当前已发布 A/B 提示词或启用模型配置不完整"
            ),
        )
    prompt_rubrics = {prompt_a.rubric_version}
    if prompt_b is not None:
        prompt_rubrics.add(prompt_b.rubric_version)
    if request.execution_mode == "structured" and len(prompt_rubrics) != 1:
        raise HTTPException(
            status_code=409,
            detail="基准回归所选提示词的 rubric 版本不一致",
        )
    if request.candidate_revision_id is not None:
        # 候选合同声明的 A/B 只是生成时的出厂建议，不是启动闸门：评测机制实验需要
        # 运营自由组合任意候选合同与任意 A/B 版本。冻结快照因此改为绑定本轮真实
        # 执行的一对，这样快照不会谎报跑了哪一对，worker 的执行期一致性校验也仍然
        # 有效（它比对快照与 StrategyBundle，用于发现快照被篡改或漂移）。
        executed_prompt_b_version = (
            prompt_b.version if prompt_b is not None else None
        )
        declared_bindings = frozen_v3_bundle.get("contract")
        declared_bindings = (
            declared_bindings.get("prompt_bindings")
            if isinstance(declared_bindings, dict)
            else None
        )
        frozen_v3_bundle["contract"] = bind_category_evaluation_prompt_versions(
            frozen_v3_bundle.get("contract") or {},
            prompt_a_version=prompt_a.version,
            prompt_b_version=executed_prompt_b_version,
        )
        executed_bindings = {
            "call_a_version": prompt_a.version,
            "call_b_version": executed_prompt_b_version,
        }
        if isinstance(declared_bindings, dict) and dict(declared_bindings) != executed_bindings:
            frozen_v3_bundle["prompt_binding_override"] = {
                "declared": dict(declared_bindings),
                "executed": executed_bindings,
                "actor": user.username,
            }
        # 绑定后自校验：失败意味着绑定函数与校验器不再自洽，属内部缺陷而非运营输入问题。
        try:
            validate_category_evaluation_prompt_bindings(
                frozen_v3_bundle.get("contract"),
                prompt_a_version=prompt_a.version,
                prompt_b_version=executed_prompt_b_version,
            )
        except CategoryEvaluationPromptBindingError as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "candidate_prompt_binding_rebind_failed",
                    "message": f"冻结候选合同绑定本轮 A/B 后仍不自洽：{exc}",
                    "reason": exc.code,
                },
            ) from exc
    sampling_policy = db.get(SamplingPolicy, 1)
    bundle = get_or_create_bundle(
        db=db,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=(
            prompt_a.rubric_version
            if single_prompt_mode
            else prompt_b.rubric_version
        ),
        engine_version=ENGINE_VERSION,
        risk_review_version=(
            RISK_REVIEW_VERSION
            if model_config.high_risk_review_enabled
            else None
        ),
        sampling_policy=sampling_policy,
    )
    strategy_snapshot = build_strategy_snapshot(
        bundle=bundle,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        sampling_policy=sampling_policy,
    )
    execution_snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id if prompt_b is not None else None,
        model_config=model_config,
        rubric_version_override=(
            prompt_b.rubric_version if prompt_b is not None else prompt_a.rubric_version
        ),
        v3_authoritative_bundle=frozen_v3_bundle,
    )
    human_truth_levels = latest_locked_golden_levels(
        db,
        category_key=baseline_set.category_key,
        asset_ids=[item.asset_id for item in frozen_items],
    )
    effective_expected_levels = {
        item.asset_id: human_truth_levels.get(item.asset_id, item.expected_level)
        for item in frozen_items
    }
    effective_fingerprint = baseline_set_fingerprint(
        (
            {
                "asset_id": item.asset_id,
                "asset_sha256": item.asset.sha256,
                "expected_level": effective_expected_levels[item.asset_id],
            }
            for item in frozen_items
        ),
        category_key=baseline_set.category_key,
    )
    execution_payload = json.loads(execution_snapshot)
    execution_payload["execution_mode"] = request.execution_mode
    execution_payload["selection_explicit"] = request.baseline_item_ids is not None
    execution_payload["category_context"] = category_context
    execution_payload["semantic_truth_snapshot"] = freeze_semantic_truth_snapshot(
        db,
        category_key=baseline_set.category_key,
        asset_ids=[item.asset_id for item in frozen_items],
    )
    execution_payload["human_truth_level_overrides"] = {
        str(asset_id): level
        for asset_id, level in sorted(human_truth_levels.items())
    }
    active_semantic_contract = db.scalar(
        select(TagDemandContract)
        .where(
            TagDemandContract.contract_key == PLATFORM_SEMANTIC_CONTRACT_KEY,
            TagDemandContract.status == "active",
        )
        .order_by(TagDemandContract.version.desc(), TagDemandContract.id.desc())
        .limit(1)
    )
    if active_semantic_contract is not None:
        try:
            semantic_definition = validate_tag_demand_contract(
                json.loads(active_semantic_contract.definition_json)
            )
        except (SemanticTagContractError, json.JSONDecodeError):
            semantic_definition = None
        if semantic_definition is not None:
            site_scopes = {
                variant.site_scope
                for variant in semantic_definition.execution_variants
                if variant.category_key == baseline_set.category_key
            }
            if len(site_scopes) == 1:
                execution_payload["semantic_quality_context"] = {
                    "contract_id": active_semantic_contract.id,
                    "contract_key": active_semantic_contract.contract_key,
                    "contract_version": active_semantic_contract.version,
                    "contract_hash": active_semantic_contract.contract_hash,
                    "site_scope": next(iter(site_scopes)),
                    "asset_scope": "unknown",
                }
    correction_contract = freeze_contract_from_execution_snapshot(
        category_key=baseline_set.category_key,
        execution_snapshot=execution_payload,
    )
    execution_payload["correction_contract"] = correction_contract
    execution_snapshot = baseline_canonical_json(execution_payload)
    previous = db.scalar(
        select(BaselineRegressionRun)
        .where(
            BaselineRegressionRun.baseline_set_id == baseline_set.id,
            BaselineRegressionRun.status.in_(BASELINE_TERMINAL_STATUSES),
        )
        .order_by(BaselineRegressionRun.sequence_no.desc())
        .limit(1)
    )
    sequence_no = (previous.sequence_no + 1) if previous else 1
    initial_metrics = compute_level_metrics(
        {
            "status": "queued",
            "expected_level": effective_expected_levels[item.asset.id],
            "predicted_level": None,
        }
        for item in frozen_items
    )
    run = BaselineRegressionRun(
        baseline_set_id=baseline_set.id,
        sequence_no=sequence_no,
        previous_run_id=previous.id if previous else None,
        strategy_bundle_id=bundle.id,
        category_key=baseline_set.category_key,
        strategy_snapshot_json=strategy_snapshot,
        execution_snapshot_json=execution_snapshot,
        correction_contract_json=baseline_canonical_json(correction_contract),
        correction_contract_hash=correction_contract_hash(correction_contract),
        baseline_set_fingerprint=effective_fingerprint,
        status="running",
        total=len(frozen_items),
        metrics_json=baseline_canonical_json(initial_metrics),
        created_by=user.username,
    )
    db.add(run)
    db.flush()
    batch_key = f"baseline:{run.id}:{uuid.uuid4().hex}"
    run_items = [
        BaselineRegressionItem(
            run_id=run.id,
            baseline_set_item_id=frozen_item.id,
            asset_id=frozen_item.asset_id,
            expected_level=effective_expected_levels[frozen_item.asset_id],
            status="queued",
        )
        for frozen_item in frozen_items
    ]
    db.add_all(run_items)
    db.flush()
    jobs = [
        EvaluationJob(
            asset_id=frozen_item.asset_id,
            category_key=baseline_set.category_key,
            category_profile_snapshot_json=execution_snapshot,
            prompt_a_id=prompt_a.id,
            prompt_b_id=prompt_b.id if prompt_b is not None else None,
            baseline_regression_item_id=run_item.id,
            strategy_bundle_id=bundle.id,
            queue_class="validation",
            origin_queue_class="validation",
            batch_key=batch_key,
        )
        for frozen_item, run_item in zip(frozen_items, run_items, strict=True)
    ]
    db.add_all(jobs)
    db.flush()
    for run_item, job in zip(run_items, jobs, strict=True):
        run_item.job_id = job.id
    job_ids = [job.id for job in jobs]
    append_audit_event(
        db,
        category="baseline_regression",
        action="run_created",
        subject_type="baseline_regression_run",
        subject_id=run.id,
        actor=user.username,
        payload={
            "baseline_set_id": baseline_set.id,
            "sequence_no": sequence_no,
            "strategy_bundle_id": bundle.id,
            "strategy_canonical_id": bundle.canonical_hash,
            "prompt_a_id": prompt_a.id,
            "prompt_b_id": prompt_b.id if prompt_b is not None else None,
            "prompt_mode": "single" if single_prompt_mode else "ab",
            "execution_mode": request.execution_mode,
            "category_key": baseline_set.category_key,
            "category_context": category_context,
            "baseline_item_ids": [item.id for item in frozen_items],
            "dimension_selection_mode": json.loads(execution_snapshot)["dimension_selection"]["mode"],
            "dimension_schema_id": None,
            "v3_contract_only": True,
            "v3_config_revision": frozen_v3_bundle.get("config_revision"),
            "candidate_revision_id": frozen_v3_bundle.get(
                "candidate_revision_id"
            ),
            "total": run.total,
        },
        event_key=f"baseline-run:{run.id}:created",
    )
    db.commit()
    return {**_baseline_run_summary(run), "job_ids": job_ids}


@app.get("/api/baseline-regressions")
def list_baseline_runs(
    baseline_set_id: int | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(BaselineRegressionRun)
    if baseline_set_id is not None:
        statement = statement.where(
            BaselineRegressionRun.baseline_set_id == baseline_set_id
        )
    runs = db.scalars(
        statement.order_by(
            BaselineRegressionRun.created_at.desc(),
            BaselineRegressionRun.id.desc(),
        )
    ).all()
    return {"items": [_baseline_run_summary(run) for run in runs]}


@app.get("/api/baseline-regressions/{run_id}")
def baseline_run_detail(
    run_id: int,
    deviations_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    page_limit = min(1000, max(1, limit))
    page_offset = max(0, offset)
    if deviations_only:
        run_items = run.items
        item_total = None
    else:
        item_total = db.scalar(
            select(func.count(BaselineRegressionItem.id)).where(
                BaselineRegressionItem.run_id == run.id
            )
        ) or 0
        run_items = db.scalars(
            select(BaselineRegressionItem)
            .where(BaselineRegressionItem.run_id == run.id)
            .order_by(BaselineRegressionItem.id.asc())
            .offset(page_offset)
            .limit(page_limit)
        ).all()
    run_item_ids = [item.id for item in run_items]
    queue_cases = {
        case.baseline_regression_item_id: case
        for case in db.scalars(
            select(OptimizationCaseQueue).where(
                OptimizationCaseQueue.baseline_regression_item_id.in_(run_item_ids)
            )
        ).all() if run_item_ids
    }
    item_payloads: list[dict[str, Any]] = []
    for item in run_items:
        snapshot = json.loads(item.result_snapshot_json or "{}")
        actual = snapshot.get("predicted_level")
        deviation = actual in BASELINE_LEVELS and actual != item.expected_level
        if deviations_only and not deviation:
            continue
        frozen_asset = json.loads(
            item.baseline_set_item.asset_snapshot_json
        )
        queue_case = queue_cases.get(item.id)
        frozen_explanation = snapshot.get("level_explanation")
        if not isinstance(frozen_explanation, dict):
            frozen_explanation = {
                "schema_version": "baseline-level-explanation-v1",
                "status": "unavailable_historical",
                "predicted_level": actual,
                "authoritative_score": snapshot.get(
                    "authoritative_score"
                ),
                "scope_status": None,
                "strong_dimensions": [],
                "weak_dimensions": [],
                "all_dimensions": [],
                "image_quality": {
                    "status": "missing",
                    "severity": None,
                    "severity_label": "",
                    "confidence": None,
                    "evidence": [],
                },
                "caps": [],
                "review_reasons": [],
                "message": "历史结果未冻结评测理由",
            }
        else:
            # 兼容已冻结的 v1 explanation：新字段只能补空默认值，不能重算或
            # 改写历史结果，确保回归结果仍然是当时服务端结论的只读快照。
            frozen_explanation = dict(frozen_explanation)
            frozen_explanation.setdefault("all_dimensions", [])
            frozen_explanation.setdefault(
                "image_quality",
                {
                    "status": "missing",
                    "severity": None,
                    "severity_label": "",
                    "confidence": None,
                    "evidence": [],
                },
            )
        item_payloads.append(
            {
                "id": item.id,
                "baseline_set_item_id": item.baseline_set_item_id,
                "asset_id": item.asset_id,
                "asset": frozen_asset,
                "image_url": f"/api/assets/{item.asset_id}/file",
                "expected_level": item.expected_level,
                "predicted_level": actual,
                "authoritative_score": snapshot.get("authoritative_score"),
                "cap_reasons": snapshot.get("cap_reasons") or [],
                "stage_a": snapshot.get("stage_a") or {},
                "level_explanation": frozen_explanation,
                "confidence": snapshot.get("confidence"),
                "needs_review": snapshot.get("needs_review"),
                "versions": snapshot.get("versions") or {},
                "execution_mode": snapshot.get("execution_mode", "structured"),
                "interpretation": snapshot.get("interpretation") or {
                    "status": (
                        "scored"
                        if actual in BASELINE_LEVELS
                        and isinstance(snapshot.get("authoritative_score"), (int, float))
                        and not isinstance(snapshot.get("authoritative_score"), bool)
                        else "manual_required"
                    ),
                    "raw_text_a": None,
                    "raw_text_b": None,
                },
                "status": item.status,
                "deviation": deviation,
                "error_message": item.error_message,
                "evaluation_id": item.evaluation_id,
                "evaluation": (
                    _result_payload(item.evaluation)
                    if item.evaluation is not None
                    else None
                ),
                "job_id": item.job_id,
                "run_id": run.id,
                "optimization_case_id": queue_case.id if queue_case else None,
                "finished_at": item.finished_at,
            }
        )
    previous = (
        db.get(BaselineRegressionRun, run.previous_run_id)
        if run.previous_run_id is not None
        else None
    )
    if deviations_only:
        item_total = len(item_payloads)
        item_payloads = item_payloads[page_offset:page_offset + page_limit]
    baseline_item_count = db.scalar(
        select(func.count(BaselineSetItem.id)).where(
            BaselineSetItem.baseline_set_id == run.baseline_set_id
        )
    ) or 0
    return {
        "summary": _baseline_run_summary(run),
        "baseline_set": _baseline_set_summary(
            run.baseline_set,
            item_count=baseline_item_count,
        ),
        "strategy": safe_strategy_snapshot_payload(
            run.strategy_snapshot_json
        ),
        "comparison": run_comparison(run, previous),
        "filter": {"deviations_only": deviations_only},
        "pagination": {
            "offset": page_offset,
            "limit": page_limit,
            "total": item_total,
        },
        "items": item_payloads,
    }


@app.get("/api/baseline-regressions/{run_id}/metrics")
def baseline_run_metrics(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    return build_baseline_field_metrics(db, run)


@app.get("/api/baseline-regressions/{run_id}/rule-diagnostics")
def baseline_run_rule_diagnostics(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Report which declared scoring rules actually fired during one run.

    Raising deduction points or lowering a redline threshold changes nothing for
    rules the model never reports, so this coverage view is what tells an
    operator whether the mechanism or the prompt is the thing to fix.
    """
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    return diagnostics_from_run(run)


def _semantic_entity_values(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        value = value.get("values")
    if not isinstance(value, list):
        return set()
    result: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            entity_id = item.get("entity_id") or item.get("value")
            if entity_id:
                result.add(str(entity_id))
        elif isinstance(item, str) and item.strip():
            result.add(item.strip())
    return result


@app.get("/api/baseline-regressions/{run_id}/semantic-metrics")
def baseline_run_semantic_metrics(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    report, evidence, context = build_run_semantic_quality(run)
    return {
        "run_id": run.id,
        "category_key": run.category_key,
        **report.to_dict(),
        "evidence": evidence,
        "contract": context or None,
    }


def _baseline_correction_payload(row: BaselineCorrectionRun) -> dict[str, Any]:
    baseline_run = row.baseline_run
    correction_contract = None
    if baseline_run is not None and baseline_run.correction_contract_hash:
        frozen_contract = json.loads(
            baseline_run.correction_contract_json or "{}"
        )
        correction_contract = {
            "contract_version": frozen_contract.get("contract_version"),
            "contract_hash": baseline_run.correction_contract_hash,
            "category_key": baseline_run.category_key,
        }
    orchestration = json.loads(row.orchestration_json or "{}")
    mechanism_refresh = orchestration.get("mechanism_refresh")
    if not isinstance(mechanism_refresh, dict):
        mechanism_refresh = None
    return {
        "id": row.id,
        "baseline_run_id": row.baseline_run_id,
        "category_key": row.category_key,
        "selected_item_ids": json.loads(row.selected_item_ids_json),
        "status": row.status,
        "stage": row.stage,
        "progress": row.progress,
        "report": json.loads(row.report_json or "{}"),
        "blockers": json.loads(row.blockers_json or "[]"),
        "candidate_revision_id": row.candidate_revision_id,
        "regression_run_id": row.regression_run_id,
        "orchestration": orchestration,
        "error": {
            "code": row.error_code,
            "message": row.error_message,
            "retryable": row.status == "failed" and row.attempt_count < 3,
        } if row.error_code else None,
        "attempt_count": row.attempt_count,
        "decision": row.decision,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "decision_note": row.decision_note,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "finished_at": row.finished_at,
        "correction_contract": correction_contract,
        "mechanism_refresh": mechanism_refresh,
    }


def _baseline_correction_item(
    db: Session,
    *,
    run_id: int,
    item_id: int,
) -> tuple[BaselineRegressionRun, BaselineRegressionItem]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "BASELINE_RUN_NOT_FOUND",
                "message": "基准回归 run 不存在",
            },
        )
    item = db.get(BaselineRegressionItem, item_id)
    if item is None or item.run_id != run.id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "BASELINE_ITEM_NOT_FOUND",
                "message": "基准回归条目不存在",
            },
        )
    return run, item


def _previous_baseline_correction_item(
    db: Session,
    *,
    run: BaselineRegressionRun,
    item: BaselineRegressionItem,
) -> BaselineRegressionItem | None:
    if run.previous_run_id is None:
        return None
    return db.scalar(
        select(BaselineRegressionItem)
        .where(
            BaselineRegressionItem.run_id == run.previous_run_id,
            BaselineRegressionItem.asset_id == item.asset_id,
        )
        .order_by(BaselineRegressionItem.id.desc())
        .limit(1)
    )


def _raise_correction_view_http(exc: CorrectionViewError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


@app.get(
    "/api/baseline-regressions/{run_id}/items/{item_id}/correction-view"
)
def get_baseline_item_correction_view(
    run_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _baseline_correction_item(db, run_id=run_id, item_id=item_id)
    return build_correction_view(
        db,
        run=run,
        item=item,
        previous_item=_previous_baseline_correction_item(
            db, run=run, item=item
        ),
    )


@app.post(
    "/api/baseline-regressions/{run_id}/items/{item_id}/corrections"
)
def submit_baseline_item_corrections(
    run_id: int,
    item_id: int,
    payload: BaselineCorrectionSubmitRequest,
    user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _baseline_correction_item(db, run_id=run_id, item_id=item_id)
    try:
        response = submit_correction_nodes(
            db,
            run=run,
            item=item,
            contract_hash=payload.contract_hash,
            nodes=[node.model_dump(mode="json") for node in payload.nodes],
            review_revision=payload.review_revision,
            idempotency_key=payload.idempotency_key,
            actor=user.username,
        )
    except CorrectionViewError as exc:
        _raise_correction_view_http(exc)
    if not response.get("idempotent_replay"):
        append_audit_event(
            db,
            category="baseline_regression",
            action="contract_correction_submitted",
            subject_type="baseline_regression_item",
            subject_id=item.id,
            actor=user.username,
            payload={
                "baseline_run_id": run.id,
                "contract_hash": payload.contract_hash,
                "node_keys": [node.node_key for node in payload.nodes],
                "review_revision": response["review_revision"],
            },
            event_key=(
                f"baseline-item:{item.id}:contract-correction:"
                f"{payload.idempotency_key}"
            ),
        )
    db.commit()
    return response


def _lane_result_snapshot(
    result: EvaluationResult,
    *,
    archived_json: str | None = None,
) -> str:
    archived = json.loads(archived_json or "{}")
    frozen = archived.get("correction_result_snapshot")
    if isinstance(frozen, dict):
        return baseline_canonical_json(frozen)
    precheck = json.loads(result.precheck_json or "{}")
    aesthetic = json.loads(result.aesthetic_json or "{}")
    scoring = json.loads(result.scoring_json or "{}")
    return baseline_canonical_json(
        {
            "schema_version": "lane-correction-result-v1",
            "evaluation_id": result.id,
            "job_id": result.job_id,
            "category_key": (
                result.job.category_key
                if result.job is not None
                else result.asset.category_key
            ),
            "predicted_level": result.level,
            "authoritative_score": result.score,
            "stage_a": precheck if isinstance(precheck, dict) else {},
            "stage_b": aesthetic if isinstance(aesthetic, dict) else {},
            "scoring": scoring if isinstance(scoring, dict) else {},
            "confidence": result.confidence,
            "needs_review": result.needs_review,
        }
    )


def _production_correction_item(
    db: Session,
    *,
    run_id: int,
    evaluation_id: int,
) -> tuple[EvaluationProductionRun, Any]:
    run = db.get(EvaluationProductionRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PRODUCTION_RUN_NOT_FOUND",
                "message": "评测生产记录不存在",
            },
        )
    result = db.get(EvaluationResult, evaluation_id)
    job_ids = {
        int(value)
        for value in json.loads(run.job_ids_json or "[]")
        if isinstance(value, int)
    }
    job = db.get(EvaluationJob, result.job_id) if result is not None else None
    belongs_to_run = bool(
        result is not None
        and job is not None
        and (
            job.id in job_ids
            or job.root_job_id in job_ids
            or job.batch_key == run.batch_key
        )
    )
    if not belongs_to_run or result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PRODUCTION_EVALUATION_NOT_FOUND",
                "message": "该生产运行中不存在此评测结果",
            },
        )
    return run, wrap_evaluation_item(
        run,
        result,
        item_id=result.id,
        result_snapshot_json=_lane_result_snapshot(result),
    )


def _candidate_correction_item(
    db: Session,
    *,
    run_id: int,
    item_id: int,
) -> tuple[PromptRegressionRun, Any]:
    run = db.get(PromptRegressionRun, run_id)
    item = db.scalar(
        select(PromptRegressionItem).where(
            PromptRegressionItem.id == item_id,
            PromptRegressionItem.run_id == run_id,
        )
    )
    if run is None or item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CANDIDATE_REGRESSION_ITEM_NOT_FOUND",
                "message": "候选回归条目不存在",
            },
        )
    result = item.candidate_evaluation or item.evaluation
    if result is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANDIDATE_EVALUATION_UNAVAILABLE",
                "message": "候选回归条目尚无评测结果",
            },
        )
    return run, wrap_evaluation_item(
        run,
        result,
        item_id=item.id,
        result_snapshot_json=_lane_result_snapshot(
            result,
            archived_json=item.candidate_result_json,
        ),
    )


def _submit_lane_corrections(
    db: Session,
    *,
    run: Any,
    item: Any,
    lane: Literal["incremental", "candidate"],
    payload: BaselineCorrectionSubmitRequest,
    user: User,
) -> dict[str, Any]:
    try:
        response = submit_correction_nodes(
            db,
            run=run,
            item=item,
            contract_hash=payload.contract_hash,
            nodes=[node.model_dump(mode="json") for node in payload.nodes],
            review_revision=payload.review_revision,
            idempotency_key=payload.idempotency_key,
            actor=user.username,
        )
    except CorrectionViewError as exc:
        _raise_correction_view_http(exc)
    response["lane"] = lane
    if not response.get("idempotent_replay"):
        append_audit_event(
            db,
            category="adaptive_correction",
            action="contract_correction_submitted",
            subject_type=f"{lane}_evaluation",
            subject_id=item.evaluation_id,
            actor=user.username,
            payload={
                "run_id": run.id,
                "item_id": item.id,
                "contract_hash": payload.contract_hash,
                "node_keys": [node.node_key for node in payload.nodes],
                "review_revision": response["review_revision"],
            },
            event_key=(
                f"{lane}:{run.id}:{item.id}:contract-correction:"
                f"{payload.idempotency_key}"
            ),
        )
    db.commit()
    return response


@app.get(
    "/api/evaluation-production-runs/{run_id}/evaluations/"
    "{evaluation_id}/correction-view"
)
def get_incremental_correction_view(
    run_id: int,
    evaluation_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _production_correction_item(
        db, run_id=run_id, evaluation_id=evaluation_id
    )
    response = build_correction_view(db, run=run, item=item)
    response["lane"] = "incremental"
    return response


@app.post(
    "/api/evaluation-production-runs/{run_id}/evaluations/"
    "{evaluation_id}/corrections"
)
def submit_incremental_corrections(
    run_id: int,
    evaluation_id: int,
    payload: BaselineCorrectionSubmitRequest,
    user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _production_correction_item(
        db, run_id=run_id, evaluation_id=evaluation_id
    )
    return _submit_lane_corrections(
        db,
        run=run,
        item=item,
        lane="incremental",
        payload=payload,
        user=user,
    )


@app.get(
    "/api/prompt-regressions/{run_id}/items/{item_id}/correction-view"
)
def get_candidate_correction_view(
    run_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _candidate_correction_item(db, run_id=run_id, item_id=item_id)
    response = build_correction_view(db, run=run, item=item)
    response["lane"] = "candidate"
    return response


@app.post(
    "/api/prompt-regressions/{run_id}/items/{item_id}/corrections"
)
def submit_candidate_corrections(
    run_id: int,
    item_id: int,
    payload: BaselineCorrectionSubmitRequest,
    user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run, item = _candidate_correction_item(db, run_id=run_id, item_id=item_id)
    return _submit_lane_corrections(
        db,
        run=run,
        item=item,
        lane="candidate",
        payload=payload,
        user=user,
    )


@app.post("/api/baseline-regressions/{run_id}/auto-corrections")
def apply_inspiration_auto_corrections(
    run_id: int,
    payload: InspirationAutoCorrectionRequest | None = None,
    _user: User = Depends(_permission_user("reviews:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    try:
        return apply_auto_correction_to_run(
            db,
            run=run,
            policy=(payload or InspirationAutoCorrectionRequest()).to_policy(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "auto_correction_not_ready", "message": str(exc)},
        ) from exc


@app.get("/api/baseline-regressions/{run_id}/drift-test")
def inspiration_auto_correction_drift_test(
    run_id: int,
    confidence_threshold: float = 0.85,
    minimum_support: int = 30,
    coverage_rate: float = 0.10,
    calibration_fraction: float = 0.70,
    maximum_level_shift: int = 1,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    try:
        policy = AutoCorrectionPolicy(
            confidence_threshold=confidence_threshold,
            minimum_support=minimum_support,
            coverage_rate=coverage_rate,
            calibration_fraction=calibration_fraction,
            maximum_level_shift=maximum_level_shift,
        )
        policy.validate()
        return build_inspiration_drift_report(run, policy=policy)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "drift_policy_invalid", "message": str(exc)},
        ) from exc


def _record_baseline_correction_failure(
    db: Session,
    row: BaselineCorrectionRun,
    exc: BaseException,
) -> None:
    stage_codes = {
        "analysis": "CORRECTION_ANALYSIS_FAILED",
        "candidate_generation": "CORRECTION_CANDIDATE_GENERATION_FAILED",
        "candidate_validation": "CORRECTION_CANDIDATE_VALIDATION_FAILED",
        "regression": "CORRECTION_REGRESSION_SETUP_FAILED",
    }
    fail_correction_run(
        row,
        error_code=(
            exc.code
            if isinstance(exc, CorrectionOrchestrationError)
            else stage_codes.get(row.stage, "CORRECTION_PIPELINE_FAILED")
        ),
        error_message=str(exc),
    )
    generation_trace = getattr(exc, "generation_trace", None)
    if isinstance(generation_trace, list):
        try:
            orchestration = json.loads(row.orchestration_json or "{}")
        except json.JSONDecodeError:
            orchestration = {}
        if not isinstance(orchestration, dict):
            orchestration = {}
        orchestration["generation_trace"] = [
            dict(entry) for entry in generation_trace if isinstance(entry, dict)
        ]
        row.orchestration_json = canonical_json(orchestration)
    row.finished_at = datetime.now(timezone.utc)


def _run_baseline_correction_background(correction_id: int, bind: Any = None) -> None:
    """Run model work outside the request's SQLite write transaction."""
    session_factory = SessionLocal if bind is None else sessionmaker(
        bind=bind,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        with session_factory() as db:
            row = db.get(BaselineCorrectionRun, correction_id)
            if row is None:
                return
            prepared = prepare_correction_generation(db, row)
            generator = configured_correction_generator(db)
            db.commit()

        generated = generate_correction_candidate(prepared, generator)

        with session_factory() as db:
            row = db.get(BaselineCorrectionRun, correction_id)
            if row is None:
                return
            advance_correction_run(db, row, None, generated)
            db.commit()
    except Exception as exc:
        with session_factory() as db:
            row = db.get(BaselineCorrectionRun, correction_id)
            if row is not None:
                _record_baseline_correction_failure(db, row, exc)
                db.commit()


@app.post("/api/baseline-regressions/{run_id}/corrections")
def create_baseline_correction(
    run_id: int,
    payload: BaselineCorrectionCreateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    if run.status not in BASELINE_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="基准回归尚未结束，不能创建纠偏分析")
    request_hash = hashlib.sha256(
        baseline_canonical_json(
            {"run_id": run_id, "item_ids": sorted(payload.item_ids)}
        ).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(BaselineCorrectionRun).where(
            BaselineCorrectionRun.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        frozen = json.loads(existing.input_snapshot_json or "{}")
        if frozen.get("request_hash") != request_hash:
            raise HTTPException(status_code=409, detail="幂等键已用于不同纠偏请求")
        return _baseline_correction_payload(existing)
    selected = db.scalars(
        select(BaselineRegressionItem).where(
            BaselineRegressionItem.run_id == run.id,
            BaselineRegressionItem.id.in_(payload.item_ids),
        )
    ).all()
    if {item.id for item in selected} != set(payload.item_ids):
        raise HTTPException(status_code=400, detail="存在不属于该 run 的样本")
    try:
        frozen_input = correction_input_snapshot(run, selected)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    frozen_input["request_hash"] = request_hash
    frozen_input["strategy_canonical_id"] = run.strategy_bundle.canonical_hash
    row = BaselineCorrectionRun(
        idempotency_key=payload.idempotency_key,
        baseline_run_id=run.id,
        category_key=run.category_key,
        selected_item_ids_json=baseline_canonical_json(sorted(payload.item_ids)),
        input_snapshot_json=baseline_canonical_json(frozen_input),
        created_by=user.username,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        category="baseline_regression",
        action="correction_created",
        subject_type="baseline_correction_run",
        subject_id=row.id,
        actor=user.username,
        payload={"baseline_run_id": run.id, "item_count": len(payload.item_ids)},
        event_key=f"baseline-correction:{row.id}:attempt:1",
    )
    db.commit()
    background_tasks.add_task(_run_baseline_correction_background, row.id, db.get_bind())
    return _baseline_correction_payload(row)


@app.get("/api/baseline-regressions/{run_id}/corrections")
def list_baseline_corrections(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(BaselineRegressionRun, run_id) is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    rows = db.scalars(
        select(BaselineCorrectionRun)
        .where(BaselineCorrectionRun.baseline_run_id == run_id)
        .order_by(BaselineCorrectionRun.created_at.desc(), BaselineCorrectionRun.id.desc())
    ).all()
    for row in rows:
        refresh_correction_run(db, row)
    db.commit()
    return {"items": [_baseline_correction_payload(row) for row in rows]}


@app.get("/api/baseline-corrections/{correction_id}")
def get_baseline_correction(
    correction_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(BaselineCorrectionRun, correction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="基准回归纠偏任务不存在")
    refresh_correction_run(db, row)
    db.commit()
    return _baseline_correction_payload(row)


@app.post("/api/baseline-corrections/{correction_id}/decision")
def decide_baseline_correction(
    correction_id: int,
    payload: BaselineCorrectionDecisionRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(BaselineCorrectionRun, correction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="基准回归纠偏任务不存在")
    refresh_correction_run(db, row)
    normalized_note = payload.note.strip()
    if row.status in {"approved", "rejected"}:
        if row.decision == payload.decision and row.decision_note == normalized_note:
            return _baseline_correction_payload(row)
        raise HTTPException(status_code=409, detail="纠偏任务已有不可变的人工结论")
    if row.status != "awaiting_decision" or row.stage != "decision":
        raise HTTPException(status_code=409, detail="候选回归尚未形成最终结论")

    if payload.decision == "approved":
        report = json.loads(row.report_json or "{}")
        regression = report.get("candidate_regression")
        if (
            not isinstance(regression, dict)
            or regression.get("approval_allowed") is not True
            or regression.get("recommendation") != "approve"
        ):
            raise HTTPException(status_code=409, detail="候选回归未通过，不能启用")
        candidate = db.get(
            CategoryEvaluationV3Revision,
            row.candidate_revision_id,
        )
        projected = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == row.category_key,
                CategoryEvaluationV3Config.status == "active",
            )
        )
        if candidate is None or projected is None:
            raise HTTPException(status_code=409, detail="候选或现役机制不存在")
        orchestration = json.loads(row.orchestration_json or "{}")
        base_projection = orchestration.get("base_projection")
        if (
            not isinstance(base_projection, dict)
            or projected.projected_revision_id != base_projection.get("revision_id")
            or projected.revision != base_projection.get("revision")
            or projected.contract_hash != base_projection.get("contract_hash")
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "projected_revision_conflict",
                    "message": "现役机制已变化，请重新执行纠偏分析",
                },
            )
        prompt_state = orchestration.get("candidate_prompt")
        if not isinstance(prompt_state, dict):
            raise HTTPException(status_code=409, detail="候选提示词绑定缺失")
        candidate_prompt = db.get(PromptVersion, prompt_state.get("id"))
        base_prompt_id = prompt_state.get("base_prompt_id")
        stage = prompt_state.get("stage")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == row.category_key
            )
        )
        if (
            candidate_prompt is None
            or profile is None
            or stage not in {"A", "B"}
            or candidate_prompt.stage != stage
        ):
            raise HTTPException(status_code=409, detail="候选提示词或类目绑定无效")
        current_prompt_id = profile.prompt_a_id if stage == "A" else profile.prompt_b_id
        if current_prompt_id not in {None, base_prompt_id}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "prompt_projection_conflict",
                    "message": "类目现役提示词已变化，请重新执行纠偏分析",
                },
            )
        try:
            activate_candidate_revision(
                db,
                projected,
                candidate,
                actor=user.username,
            )
        except CategoryEvaluationV3RevisionError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        candidate_prompt.status = "published"
        if stage == "A":
            profile.prompt_a_id = candidate_prompt.id
        else:
            profile.prompt_b_id = candidate_prompt.id
        candidate_run = (
            db.get(BaselineRegressionRun, row.regression_run_id)
            if row.regression_run_id is not None
            else None
        )
        candidate_contract = json.loads(candidate.contract_json or "{}")
        candidate_correction_contract = candidate_contract.get("correction_contract")
        if not isinstance(candidate_correction_contract, dict) and isinstance(
            candidate_contract.get("nodes"), list
        ):
            candidate_correction_contract = candidate_contract
        refresh_contract_hash = (
            candidate_run.correction_contract_hash
            if candidate_run is not None and candidate_run.correction_contract_hash
            else (
                correction_contract_hash(candidate_correction_contract)
                if isinstance(candidate_correction_contract, dict)
                else candidate.contract_hash
            )
        )
        mechanism_refresh = {
            "category_key": row.category_key,
            "prompt_version_ids": [
                value
                for value in (profile.prompt_a_id, profile.prompt_b_id)
                if isinstance(value, int)
            ],
            "v3_revision_id": candidate.id,
            "contract_hash": refresh_contract_hash,
        }
        orchestration["mechanism_refresh"] = mechanism_refresh
        row.orchestration_json = baseline_canonical_json(orchestration)

    row.status = payload.decision
    row.decision = payload.decision
    row.decided_by = user.username
    row.decided_at = datetime.now(timezone.utc)
    row.decision_note = normalized_note
    row.finished_at = row.decided_at
    append_audit_event(
        db,
        category="baseline_regression",
        action=f"correction_{payload.decision}",
        subject_type="baseline_correction_run",
        subject_id=row.id,
        actor=user.username,
        payload={
            "decision": payload.decision,
            "candidate_revision_id": row.candidate_revision_id,
            "regression_run_id": row.regression_run_id,
            "note": normalized_note,
        },
        event_key=f"baseline-correction:{row.id}:decision:{payload.decision}",
    )
    db.commit()
    return _baseline_correction_payload(row)


@app.post("/api/baseline-corrections/{correction_id}/retry")
def retry_baseline_correction(
    correction_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(BaselineCorrectionRun, correction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="基准回归纠偏任务不存在")
    if row.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的纠偏任务可重试")
    if row.attempt_count >= 3:
        raise HTTPException(status_code=409, detail="纠偏任务已达到最大重试次数")
    row.status = "processing"
    row.stage = "analysis"
    row.progress = 10
    row.error_code = ""
    row.error_message = ""
    row.finished_at = None
    row.attempt_count += 1
    append_audit_event(
        db,
        category="baseline_regression",
        action="correction_retried",
        subject_type="baseline_correction_run",
        subject_id=row.id,
        actor=user.username,
        payload={"attempt_count": row.attempt_count},
        event_key=f"baseline-correction:{row.id}:attempt:{row.attempt_count}",
    )
    db.commit()
    if background_tasks is not None:
        background_tasks.add_task(_run_baseline_correction_background, row.id, db.get_bind())
    return _baseline_correction_payload(row)


@app.post("/api/baseline-regressions/{run_id}/optimization-cases")
def enqueue_baseline_optimization_cases(
    run_id: int,
    payload: BaselineOptimizationQueueRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(BaselineRegressionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="基准回归 run 不存在")
    selected = db.scalars(
        select(BaselineRegressionItem).where(
            BaselineRegressionItem.run_id == run.id,
            BaselineRegressionItem.id.in_(payload.item_ids),
        )
    ).all()
    if {item.id for item in selected} != set(payload.item_ids):
        raise HTTPException(status_code=400, detail="存在不属于该 run 的条目")

    case_ids: list[int] = []
    created_count = 0
    for item in selected:
        snapshot = json.loads(item.result_snapshot_json or "{}")
        actual = snapshot.get("predicted_level")
        if (
            item.status != "completed"
            or actual not in BASELINE_LEVELS
            or actual == item.expected_level
            or item.evaluation_id is None
        ):
            raise HTTPException(
                status_code=409,
                detail=f"条目 #{item.id} 不是可入队的已完成偏差样本",
            )
        distance = abs(
            BASELINE_LEVELS.index(item.expected_level)
            - BASELINE_LEVELS.index(actual)
        )
        prompt_version = (
            (snapshot.get("versions") or {}).get("prompt_b")
            or (snapshot.get("versions") or {}).get("prompt_a")
            or run.strategy_bundle.prompt_b_version
            or run.strategy_bundle.prompt_a_version
        )
        case_payload = {
            "schema_version": "optimization-case-v1",
            "source": "baseline_regression",
            "purpose": BASELINE_OPTIMIZATION_CASE_PURPOSE,
            "expected_level": item.expected_level,
            "actual_level": actual,
            "baseline_set_id": run.baseline_set_id,
            "baseline_run_id": run.id,
            "baseline_item_id": item.id,
            "baseline_set_item_id": item.baseline_set_item_id,
            "asset_id": item.asset_id,
            "evaluation_id": item.evaluation_id,
            "job_id": item.job_id,
            "diagnostic": {
                "level_distance": distance,
                "authoritative_score": snapshot.get(
                    "authoritative_score"
                ),
                "cap_reasons": snapshot.get("cap_reasons") or [],
                "stage_a": snapshot.get("stage_a") or {},
            },
        }
        statement = (
            sqlite_insert(OptimizationCaseQueue)
            .values(
                idempotency_key=f"baseline-regression-item:{item.id}",
                evaluation_id=item.evaluation_id,
                final_review_id=None,
                source_type="baseline_regression",
                source_event_id=None,
                baseline_regression_item_id=item.id,
                prompt_version=str(prompt_version),
                severity="P1" if distance >= 2 else "P2",
                case_json=baseline_canonical_json(case_payload),
                status="pending",
            )
            # 无冲突目标：idempotency_key 与 baseline_regression_item_id 是 1:1 的
            # 两个唯一索引，重复入队会同时命中。指定单一 index_elements 时 SQLite 仍会
            # 对另一个唯一索引 ABORT，命中顺序不可控，因此对全部唯一约束一律 DO NOTHING。
            .on_conflict_do_nothing()
        )
        result = db.execute(statement)
        created_count += int(result.rowcount or 0)
        case = db.scalar(
            select(OptimizationCaseQueue).where(
                OptimizationCaseQueue.baseline_regression_item_id == item.id
            )
        )
        if case is None:
            raise HTTPException(status_code=409, detail="偏差样本并发入队失败")
        case_ids.append(case.id)
        append_audit_event(
            db,
            category="baseline_regression",
            action="deviation_enqueued",
            subject_type="baseline_regression_item",
            subject_id=item.id,
            actor=user.username,
            payload={"optimization_case_id": case.id, **case_payload},
            event_key=f"baseline-item:{item.id}:optimization-case",
        )
    db.commit()
    return {
        "run_id": run.id,
        "case_ids": case_ids,
        "created": created_count,
        "idempotent": created_count == 0,
        "purpose": BASELINE_OPTIMIZATION_CASE_PURPOSE,
    }


@app.get("/api/sample-sets")
def list_sample_sets(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    sample_sets = db.scalars(select(SampleSet).order_by(SampleSet.created_at.desc())).all()
    return {"items": [_sample_set_summary(sample_set) for sample_set in sample_sets]}


@app.get("/api/quality-assets/summary")
def quality_assets_summary(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    sample_sets = db.scalars(select(SampleSet)).all()
    grouped: dict[str, dict[str, dict[str, int]]] = {
        "by_kind": {},
        "by_category": {},
        "by_status": {},
    }
    item_count = 0
    truth_complete_count = 0
    for sample_set in sample_sets:
        summary = _sample_set_summary(sample_set)
        set_items = int(summary["item_count"])
        complete_items = int(summary["truth_complete_count"])
        item_count += set_items
        truth_complete_count += complete_items
        for group_key, value in (
            ("by_kind", sample_set.kind),
            ("by_category", sample_set.category_key),
            ("by_status", sample_set.status),
        ):
            bucket = grouped[group_key].setdefault(
                value,
                {"sample_sets": 0, "items": 0, "truth_complete": 0},
            )
            bucket["sample_sets"] += 1
            bucket["items"] += set_items
            bucket["truth_complete"] += complete_items
    return {
        "sample_set_count": len(sample_sets),
        "item_count": item_count,
        "truth_complete_count": truth_complete_count,
        "by_truth_complete": {
            "true": truth_complete_count,
            "false": item_count - truth_complete_count,
        },
        **grouped,
    }


@app.post("/api/sample-sets")
def create_sample_set(
    payload: SampleSetCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写样本集名称")
    if db.scalar(select(SampleSet).where(SampleSet.name == name)):
        raise HTTPException(status_code=400, detail="样本集名称已存在")

    source: SampleSet | None = None
    if payload.source_sample_set_id is not None:
        source = db.get(SampleSet, payload.source_sample_set_id)
        if not source:
            raise HTTPException(status_code=404, detail="源样本集不存在")

    sample_set = SampleSet(
        name=name,
        description=payload.description.strip(),
        # 复制出来的一律是 draft：locked 守卫的建议是"复制形成新草稿版本后再调整"，
        # 若复制体也进 locked 就又改不了，等于没给出路。
        kind=source.kind if source is not None else payload.kind,
        category_key=source.category_key if source is not None else payload.category_key,
        created_by=user.username,
    )
    db.add(sample_set)
    db.flush()

    copied = 0
    if source is not None:
        for item in source.items:
            db.add(
                SampleSetItem(
                    sample_set_id=sample_set.id,
                    asset_id=item.asset_id,
                    source_result_id=item.source_result_id,
                    expected_level=item.expected_level,
                    expected_category=item.expected_category,
                    # 人工纠偏真值连同修订号与署名一起带过去 —— 复制黄金集的意义
                    # 就在于保留这份人工结论，重置它等于把纠偏工作丢掉。
                    truth_json=item.truth_json,
                    truth_revision=item.truth_revision,
                    truth_updated_by=item.truth_updated_by,
                    truth_updated_at=item.truth_updated_at,
                    note=item.note,
                    added_by=user.username,
                )
            )
            copied += 1

    db.commit()
    db.refresh(sample_set)
    result: dict[str, int] = {"id": sample_set.id}
    if source is not None:
        result["copied_items"] = copied
        result["source_sample_set_id"] = source.id
    return result


@app.get("/api/sample-sets/{sample_set_id}")
def sample_set_detail(
    sample_set_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    return {
        "summary": _sample_set_summary(sample_set),
        "items": [_sample_set_item_payload(item) for item in sample_set.items],
    }


@app.post("/api/sample-sets/{sample_set_id}/export")
def export_sample_set(
    sample_set_id: int,
    payload: SampleSetExportRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    export = build_quality_asset_export(sample_set, format=payload.format)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", sample_set.name).strip("-")
    filename = f"{safe_name or f'sample-set-{sample_set.id}'}.{export.extension}"
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Row-Count": str(export.row_count),
            "X-Dataset-Version": export.dataset_version,
            "X-Manifest-Hash": export.manifest_hash,
        },
    )


@app.post("/api/sample-sets/{sample_set_id}/items")
def add_sample_set_items(
    sample_set_id: int,
    payload: SampleSetAddItemsRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    _assert_sample_set_is_mutable(sample_set)
    requested_ids = list(dict.fromkeys(payload.asset_ids))
    assets = db.scalars(select(Asset).where(Asset.id.in_(requested_ids))).all()
    assets_by_id = {asset.id: asset for asset in assets}
    missing = [asset_id for asset_id in requested_ids if asset_id not in assets_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"有 {len(missing)} 张素材不存在")
    if any(asset.category_key != sample_set.category_key for asset in assets):
        raise HTTPException(status_code=422, detail="样本集类目与素材所属通道不一致")
    existing_ids = {item.asset_id for item in sample_set.items}
    added = 0
    skipped: list[int] = []
    for asset_id in requested_ids:
        if asset_id in existing_ids:
            skipped.append(asset_id)
            continue
        result = db.scalar(
            select(EvaluationResult)
            .where(EvaluationResult.asset_id == asset_id)
            .order_by(EvaluationResult.created_at.desc())
            .limit(1)
        )
        if not result:
            skipped.append(asset_id)
            continue
        result_payload = _result_payload(result) or {}
        human_review = result_payload.get("human_review") or {}
        if human_review.get("decision") not in {"approved", "corrected"}:
            skipped.append(asset_id)
            continue
        precheck = result_payload.get("precheck") or {}
        category = ((precheck.get("classification") or {}).get("primary_category") or "无法判断")
        item = SampleSetItem(
                sample_set_id=sample_set.id,
                asset_id=asset_id,
                source_result_id=result.id,
                expected_level=payload.expected_level or result_payload.get("final_level"),
                expected_category=category,
                truth_json=json.dumps(
                    truth_from_result(result, payload.expected_level), ensure_ascii=False
                ),
                truth_updated_by=user.username,
                truth_updated_at=datetime.now(timezone.utc),
                added_by=user.username,
            )
        db.add(item)
        db.flush()
        db.add(
            SampleTruthRevision(
                sample_item_id=item.id,
                revision=1,
                truth_json=item.truth_json,
                reason="收录样本时建立首版标准答案",
                reviewer_name=user.username,
            )
        )
        added += 1
    if not added:
        raise HTTPException(status_code=400, detail="所选素材未评测或已经在样本集中")
    db.commit()
    return {"added": added, "skipped": skipped}


@app.patch("/api/sample-sets/{sample_set_id}/items/{item_id}")
def update_sample_set_item(
    sample_set_id: int,
    item_id: int,
    payload: SampleSetItemUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    _assert_sample_set_is_mutable(item.sample_set)
    item.expected_level = payload.expected_level
    item.note = payload.note.strip()
    if payload.truth is not None:
        truth = dict(payload.truth)
        if payload.expected_level:
            truth["level"] = payload.expected_level
        item.truth_revision += 1
        item.truth_json = json.dumps(truth, ensure_ascii=False)
        item.truth_updated_by = user.username
        item.truth_updated_at = datetime.now(timezone.utc)
        db.add(
            SampleTruthRevision(
                sample_item_id=item.id,
                revision=item.truth_revision,
                truth_json=item.truth_json,
                reason=payload.revision_reason.strip() or "更新标准答案",
                reviewer_name=user.username,
            )
        )
    db.commit()
    return {"ok": True}


@app.patch("/api/sample-sets/{sample_set_id}/status")
def update_sample_set_status(
    sample_set_id: int,
    payload: SampleSetStatusRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    if sample_set.status == "locked" and payload.status != "locked":
        _assert_sample_set_is_mutable(sample_set)
    if payload.status == "locked":
        if sample_set.kind != "golden":
            raise HTTPException(status_code=400, detail="只有黄金样本集需要锁定")
        incomplete = [item for item in sample_set.items if not json.loads(item.truth_json or "{}")]
        if not sample_set.items or incomplete:
            raise HTTPException(status_code=400, detail="请先补全所有黄金样本的标准答案")
    sample_set.status = payload.status
    db.commit()
    return {"ok": True}


@app.get("/api/sample-sets/{sample_set_id}/items/{item_id}/history")
def sample_item_history(
    sample_set_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.asset_id == item.asset_id)
        .order_by(EvaluationResult.created_at.desc())
    ).all()
    evaluations = []
    for result in results:
        payload = _result_payload(result) or {}
        payload["reviews"] = [
            {
                "id": review.id,
                "reviewer_name": review.reviewer_name,
                "decision": review.decision,
                "corrected_level": review.corrected_level,
                "note": review.note,
                "corrections": json.loads(review.corrections_json or "[]"),
                "created_at": review.created_at,
            }
            for review in result.reviews
        ]
        evaluations.append(payload)
    revisions = db.scalars(
        select(SampleTruthRevision)
        .where(SampleTruthRevision.sample_item_id == item.id)
        .order_by(SampleTruthRevision.revision.desc())
    ).all()
    regression_items = db.scalars(
        select(PromptRegressionItem)
        .where(PromptRegressionItem.sample_item_id == item.id)
        .order_by(PromptRegressionItem.created_at.desc())
    ).all()
    return {
        "item": _sample_set_item_payload(item),
        "evaluations": evaluations,
        "truth_revisions": [
            {
                "id": revision.id,
                "revision": revision.revision,
                "truth": json.loads(revision.truth_json or "{}"),
                "reason": revision.reason,
                "reviewer_name": revision.reviewer_name,
                "created_at": revision.created_at,
            }
            for revision in revisions
        ],
        "regressions": [
            {
                "id": regression.id,
                "run_id": regression.run_id,
                "run_name": regression.run.name,
                "status": regression.status,
                "passed": regression.passed,
                "comparison": json.loads(regression.comparison_json or "{}"),
                "created_at": regression.created_at,
                "finished_at": regression.finished_at,
            }
            for regression in regression_items
        ],
    }


@app.delete("/api/sample-sets/{sample_set_id}/items/{item_id}")
def remove_sample_set_item(
    sample_set_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    _assert_sample_set_is_mutable(item.sample_set)
    db.delete(item)
    db.commit()
    return {"ok": True}


def _regression_summary(run: PromptRegressionRun) -> dict[str, Any]:
    metrics = json.loads(run.metrics_json or "{}")
    candidate_strategy = (
        json.loads(run.candidate_strategy_snapshot_json or "{}")
        if run.regression_mode == "paired"
        else {}
    )
    candidate_prompt_a = candidate_strategy.get("prompt_a") or {}
    candidate_prompt_b = candidate_strategy.get("prompt_b") or {}
    payload = {
        "id": run.id,
        "name": run.name,
        "regression_mode": run.regression_mode,
        "sample_set_id": run.sample_set_id,
        "sample_set_name": run.sample_set.name,
        "prompt_a_id": (
            candidate_prompt_a.get("id")
            if candidate_prompt_a
            else run.prompt_a_id
        ),
        "prompt_a_version": (
            candidate_prompt_a.get("version")
            if candidate_prompt_a
            else run.prompt_a.version
        ),
        "prompt_b_id": (
            candidate_prompt_b.get("id")
            if candidate_prompt_b
            else run.prompt_b_id
        ),
        "prompt_b_version": (
            candidate_prompt_b.get("version")
            if candidate_prompt_b
            else run.prompt_b.version
        ),
        "status": run.status,
        "threshold": run.threshold,
        "total": run.total,
        "completed": run.completed,
        "passed": run.passed,
        "failed": run.failed,
        "pass_rate": metrics.get("pass_rate", 0),
        "release_gate_passed": metrics.get("release_gate_passed", False),
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }
    if run.regression_mode == "paired":
        payload.update(
            {
                "baseline_strategy_bundle_id": run.baseline_strategy_bundle_id,
                "candidate_strategy_bundle_id": run.candidate_strategy_bundle_id,
                "trigger_prompt_id": run.trigger_prompt_id,
                "sample_set_version": run.sample_set_version,
                "metric_rules_version": run.metric_rules_version,
                "metric_rules": json.loads(run.metric_rules_json or "{}"),
                "recommendation": run.recommendation,
                "approval_status": run.approval_status,
                "approved_by": run.approved_by,
                "approval_note": run.approval_note,
                "approved_at": run.approved_at,
                "summary": json.loads(run.summary_json or "{}"),
            }
        )
    return payload


@app.get("/api/prompt-regressions")
def list_prompt_regressions(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(PromptRegressionRun)
        .order_by(PromptRegressionRun.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    return {"items": [_regression_summary(run) for run in runs]}


@app.get("/api/strategy-bundles")
def list_strategy_bundles(
    prompt_b_id: int | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(StrategyBundle).order_by(
        StrategyBundle.created_at.desc(), StrategyBundle.id.desc()
    )
    if prompt_b_id is not None:
        prompt = db.get(PromptVersion, prompt_b_id)
        if not prompt or prompt.stage != "B":
            raise HTTPException(status_code=404, detail="B 阶段提示词不存在")
        statement = statement.where(
            StrategyBundle.prompt_b_version == prompt.version
        )
    bundles = db.scalars(statement.limit(200)).all()
    return {
        "items": [
            {
                "id": bundle.id,
                "model_id": bundle.model_id,
                "prompt_a_version": bundle.prompt_a_version,
                "prompt_b_version": bundle.prompt_b_version,
                "rubric_version": bundle.rubric_version,
                "engine_version": bundle.engine_version,
                "risk_review_version": bundle.risk_review_version,
                "agent_plan_version": bundle.agent_plan_version,
                "sampling_policy_revision": bundle.sampling_policy_revision,
                "created_at": bundle.created_at,
            }
            for bundle in bundles
        ]
    }


def _dimension_schema_payload(
    schema: DimensionSchema,
    *,
    include_definition: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": schema.id,
        "schema_key": schema.schema_key,
        "version": schema.version,
        "schema_type": schema.schema_type,
        "family_key": schema.family_key,
        "display_name": schema.display_name,
        "status": schema.status,
        "parent_schema_id": schema.parent_schema_id,
        "core_schema_id": schema.core_schema_id,
        "canonical_hash": schema.canonical_hash,
        "source_optimization_run_id": schema.source_optimization_run_id,
        "created_by": schema.created_by,
        "created_at": schema.created_at,
        "published_by": schema.published_by,
        "published_at": schema.published_at,
        "retired_at": schema.retired_at,
    }
    if include_definition:
        payload["definition"] = json.loads(schema.definition_json)
    return payload


def _validated_dimension_definition(definition: dict[str, Any]) -> tuple[str, str]:
    if definition.get("format_version") != "dimension-schema-definition-v1":
        raise HTTPException(status_code=422, detail="维度方案格式版本不受支持")
    dimensions = definition.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise HTTPException(status_code=422, detail="维度方案至少需要一个维度")
    seen_keys: set[str] = set()
    for index, dimension in enumerate(dimensions, start=1):
        if not isinstance(dimension, dict):
            raise HTTPException(status_code=422, detail=f"第 {index} 个维度必须是对象")
        key = dimension.get("key")
        if not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_]{2,79}", key) is None:
            raise HTTPException(
                status_code=422,
                detail=f"第 {index} 个维度 key 必须是 3-80 位小写英文、数字或下划线",
            )
        if key in seen_keys:
            raise HTTPException(status_code=422, detail=f"维度 key 重复：{key}")
        seen_keys.add(key)
        label = dimension.get("label")
        if (
            not isinstance(label, str)
            or not label.strip()
            or re.search(r"[\u4e00-\u9fff]", label) is None
        ):
            raise HTTPException(status_code=422, detail=f"维度 {key} 必须填写中文名")
        description = dimension.get("description")
        if not isinstance(description, str) or not description.strip():
            raise HTTPException(status_code=422, detail=f"维度 {key} 必须填写评审说明/关注点")
        anchors = dimension.get("anchors")
        if not isinstance(anchors, dict) or any(
            not isinstance(anchors.get(str(level)), str)
            or not anchors[str(level)].strip()
            for level in range(1, 6)
        ):
            raise HTTPException(status_code=422, detail=f"维度 {key} 必须填写 1-5 级锚点")
    try:
        validate_dimension_scoring_contract(definition)
    except (DimensionScoringContractError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"维度方案不可执行：{exc}") from exc
    serialized = canonical_json(definition)
    return serialized, canonical_hash(definition)


def _ensure_dimension_schema_references(
    db: Session,
    *,
    parent_schema_id: int | None,
    core_schema_id: int | None,
) -> None:
    for label, schema_id in (("父版本", parent_schema_id), ("核心版本", core_schema_id)):
        if schema_id is not None and db.get(DimensionSchema, schema_id) is None:
            raise HTTPException(status_code=422, detail=f"维度方案{label}不存在")


def _legacy_dimension_write_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "legacy_dimension_write_retired",
            "message": "旧类目维度写入口已下线，请使用“类目评测 v3 合同配置”。",
        },
    )


@app.get("/api/dimension-schemas")
def list_dimension_schemas(
    schema_key: str | None = None,
    schema_type: str | None = None,
    family_key: str | None = None,
    status: str | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(DimensionSchema).order_by(
        DimensionSchema.schema_key,
        DimensionSchema.created_at.desc(),
        DimensionSchema.id.desc(),
    )
    for column, value in (
        (DimensionSchema.schema_key, schema_key),
        (DimensionSchema.schema_type, schema_type),
        (DimensionSchema.family_key, family_key),
        (DimensionSchema.status, status),
    ):
        if value is not None:
            statement = statement.where(column == value)
    schemas = db.scalars(statement.limit(200)).all()
    return {
        "items": [
            _dimension_schema_payload(schema, include_definition=False)
            for schema in schemas
        ]
    }


@app.get("/api/dimension-schemas/{schema_key}/versions/{version}")
def get_dimension_schema_version(
    schema_key: str,
    version: str,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    schema = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == schema_key,
            DimensionSchema.version == version,
        )
    )
    if schema is None:
        raise HTTPException(status_code=404, detail="维度 Schema 版本不存在")
    return _dimension_schema_payload(schema, include_definition=True)


@app.post("/api/dimension-schemas", status_code=201)
def create_dimension_schema(
    payload: DimensionSchemaWriteRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _legacy_dimension_write_retired()
    definition_json, definition_hash = _validated_dimension_definition(payload.definition)
    _ensure_dimension_schema_references(
        db,
        parent_schema_id=payload.parent_schema_id,
        core_schema_id=payload.core_schema_id,
    )
    schema = DimensionSchema(
        schema_key=payload.schema_key.strip(),
        version=payload.version.strip(),
        schema_type=payload.schema_type,
        family_key=payload.family_key,
        display_name=payload.display_name.strip(),
        status="draft",
        parent_schema_id=payload.parent_schema_id,
        core_schema_id=payload.core_schema_id,
        definition_json=definition_json,
        canonical_hash=definition_hash,
        created_by=user.username,
    )
    db.add(schema)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="维度方案版本或内容已经存在") from exc
    db.refresh(schema)
    append_audit_event(
        db,
        category="dimension_schema",
        action="created",
        subject_type="dimension_schema",
        subject_id=schema.id,
        actor=user.username,
        payload={"schema_key": schema.schema_key, "version": schema.version},
    )
    db.commit()
    return _dimension_schema_payload(schema, include_definition=True)


@app.put("/api/dimension-schemas/{schema_id}")
def update_dimension_schema(
    schema_id: int,
    payload: DimensionSchemaUpdateRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _legacy_dimension_write_retired()
    schema = db.get(DimensionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="维度方案不存在")
    if schema.status not in {"draft", "candidate"}:
        raise HTTPException(status_code=409, detail="已发布或停用的维度方案不能原地修改")
    definition_json, definition_hash = _validated_dimension_definition(payload.definition)
    _ensure_dimension_schema_references(
        db,
        parent_schema_id=payload.parent_schema_id,
        core_schema_id=payload.core_schema_id,
    )
    schema.display_name = payload.display_name.strip()
    schema.parent_schema_id = payload.parent_schema_id
    schema.core_schema_id = payload.core_schema_id
    schema.definition_json = definition_json
    schema.canonical_hash = definition_hash
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="相同维度方案内容已经存在") from exc
    db.refresh(schema)
    append_audit_event(
        db,
        category="dimension_schema",
        action="updated",
        subject_type="dimension_schema",
        subject_id=schema.id,
        actor=user.username,
        payload={"schema_key": schema.schema_key, "version": schema.version},
    )
    db.commit()
    return _dimension_schema_payload(schema, include_definition=True)


@app.delete("/api/dimension-schemas/{schema_id}")
def delete_dimension_schema(
    schema_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _legacy_dimension_write_retired()
    schema = db.get(DimensionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="维度方案不存在")
    if schema.status not in {"draft", "candidate"}:
        raise HTTPException(status_code=409, detail="已发布或停用的维度方案不能删除")
    if db.scalar(select(EvaluationCategoryProfile.id).where(
        EvaluationCategoryProfile.dimension_schema_key == schema.schema_key,
        EvaluationCategoryProfile.dimension_schema_version == schema.version,
    )) is not None:
        raise HTTPException(status_code=409, detail="维度方案仍被类目引用，不能删除")
    db.delete(schema)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="维度方案仍被其他版本或运行记录引用") from exc
    append_audit_event(
        db,
        category="dimension_schema",
        action="deleted",
        subject_type="dimension_schema",
        subject_id=schema_id,
        actor=user.username,
        payload={"schema_key": schema.schema_key, "version": schema.version},
    )
    db.commit()
    return {"ok": True}


@app.post("/api/dimension-schemas/{schema_id}/publish")
def publish_dimension_schema(
    schema_id: int,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _legacy_dimension_write_retired()
    schema = db.get(DimensionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="维度方案不存在")
    if schema.status not in {"draft", "candidate"}:
        raise HTTPException(status_code=409, detail="当前维度方案不能重复发布")
    definition = json.loads(schema.definition_json)
    _validated_dimension_definition(definition)
    release_gate = definition.get("release_gate")
    if (
        isinstance(release_gate, dict)
        and release_gate.get("publishing_blocked") is True
    ):
        reasons = release_gate.get("blocked_reasons")
        message = "；".join(
            str(item) for item in reasons or [] if str(item).strip()
        ) or "维度方案尚未满足发布门禁"
        raise HTTPException(status_code=409, detail=message)
    prompt_contract = definition.get("prompt_contract")
    if (
        isinstance(prompt_contract, dict)
        and prompt_contract.get("publishing_blocked") is True
    ):
        raise HTTPException(status_code=409, detail="维度方案提示词合同尚未满足发布门禁")
    now = datetime.now(timezone.utc)
    schema.status = "published"
    schema.published_by = user.username
    schema.published_at = now
    schema.retired_at = None
    db.commit()
    db.refresh(schema)
    append_audit_event(
        db,
        category="dimension_schema",
        action="published",
        subject_type="dimension_schema",
        subject_id=schema.id,
        actor=user.username,
        payload={"schema_key": schema.schema_key, "version": schema.version},
    )
    db.commit()
    return _dimension_schema_payload(schema, include_definition=True)


def _dimension_route_policy_payload(
    policy: DimensionRoutePolicy,
    *,
    include_definition: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": policy.id,
        "policy_key": policy.policy_key,
        "version": policy.version,
        "display_name": policy.display_name,
        "status": policy.status,
        "canonical_hash": policy.canonical_hash,
        "created_by": policy.created_by,
        "created_at": policy.created_at,
        "published_by": policy.published_by,
        "published_at": policy.published_at,
        "retired_at": policy.retired_at,
    }
    if include_definition:
        payload["definition"] = json.loads(policy.definition_json)
    return payload


@app.get("/api/dimension-route-policies")
def list_dimension_route_policies(
    policy_key: str | None = None,
    status: str | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(DimensionRoutePolicy).order_by(
        DimensionRoutePolicy.policy_key,
        DimensionRoutePolicy.created_at.desc(),
        DimensionRoutePolicy.id.desc(),
    )
    if policy_key is not None:
        statement = statement.where(
            DimensionRoutePolicy.policy_key == policy_key
        )
    if status is not None:
        statement = statement.where(
            DimensionRoutePolicy.status == status
        )
    policies = db.scalars(statement.limit(200)).all()
    return {
        "items": [
            _dimension_route_policy_payload(
                policy,
                include_definition=False,
            )
            for policy in policies
        ]
    }


@app.get(
    "/api/dimension-route-policies/{policy_key}/versions/{version}"
)
def get_dimension_route_policy_version(
    policy_key: str,
    version: str,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.scalar(
        select(DimensionRoutePolicy).where(
            DimensionRoutePolicy.policy_key == policy_key,
            DimensionRoutePolicy.version == version,
        )
    )
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail="维度路由策略版本不存在",
        )
    return _dimension_route_policy_payload(
        policy,
        include_definition=True,
    )


def _paired_metric_rules(payload: PairedRegressionCreateRequest) -> dict[str, Any]:
    return {
        "schema_version": "paired-metric-rules-v1",
        "version": payload.metric_rules_version.strip(),
        "thresholds": {
            "aesthetic_accuracy_max_drop": payload.aesthetic_accuracy_max_drop,
            "whole_image_accuracy_max_drop": payload.whole_image_accuracy_max_drop,
            "level_consistency_max_drop": payload.level_consistency_max_drop,
        },
        **paired_gate_policy(),
    }


def _latest_bundle_result(
    db: Session, *, asset_id: int, strategy_bundle_id: int
) -> EvaluationResult | None:
    return db.scalar(
        select(EvaluationResult)
        .where(
            EvaluationResult.asset_id == asset_id,
            EvaluationResult.strategy_bundle_id == strategy_bundle_id,
        )
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .limit(1)
    )


def _ensure_paired_validation_job(
    db: Session,
    *,
    item: PromptRegressionItem,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
    category_key: str | None = None,
    category_profile_snapshot_json: str | None = None,
) -> EvaluationJob:
    existing = db.scalar(
        select(EvaluationJob).where(
            EvaluationJob.regression_item_id == item.id,
            EvaluationJob.strategy_bundle_id == bundle.id,
        )
    )
    if existing is not None:
        return existing
    if category_key is None or category_profile_snapshot_json is None:
        raise ValueError("新建配对回归评测任务必须冻结类目合同")
    job = EvaluationJob(
        asset_id=item.sample_item.asset_id,
        category_key=category_key,
        category_profile_snapshot_json=category_profile_snapshot_json,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        regression_item_id=item.id,
        strategy_bundle_id=bundle.id,
        queue_class="validation",
        origin_queue_class="validation",
        technical_attempt=0,
        batch_key=f"paired-regression:{item.run_id}",
        status="queued",
        stage="waiting",
    )
    db.add(job)
    db.flush()
    return job


def _strategy_snapshot_for_bundle(
    db: Session, bundle: StrategyBundle
) -> tuple[str, PromptVersion, PromptVersion]:
    """Resolve the exact immutable bundle definition without result history."""
    if bundle.prompt_b_version is None:
        raise ValueError("配对回归的 StrategyBundle 必须包含 B 阶段提示词")
    prompt_a_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "A",
            PromptVersion.version == bundle.prompt_a_version,
        )
    ).all()
    prompt_b_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "B",
            PromptVersion.version == bundle.prompt_b_version,
        )
    ).all()
    if bundle.sampling_policy_revision is None:
        policy_matches: list[SamplingPolicy | None] = [None]
    else:
        policy_matches = list(
            db.scalars(
                select(SamplingPolicy).where(
                    SamplingPolicy.revision
                    == bundle.sampling_policy_revision
                )
            ).all()
        )

    matches: list[tuple[str, PromptVersion, PromptVersion]] = []
    for prompt_a in prompt_a_matches:
        for prompt_b in prompt_b_matches:
            for policy in policy_matches:
                try:
                    snapshot = safe_strategy_snapshot_payload(
                        build_strategy_snapshot(
                            bundle, prompt_a, prompt_b, policy
                        )
                    )
                    snapshot_json = json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except ValueError:
                    continue
                matches.append((snapshot_json, prompt_a, prompt_b))
    if len(matches) != 1:
        raise ValueError(
            "StrategyBundle 缺少唯一可验证的 Prompt/采样策略定义；"
            "不能依赖历史评测结果补全"
        )
    return matches[0]


def _model_config_for_bundle(
    db: Session, bundle: StrategyBundle
) -> ModelConfig:
    try:
        frozen = json.loads(bundle.model_config_snapshot)
    except json.JSONDecodeError as exc:
        raise ValueError("StrategyBundle 模型配置快照损坏") from exc
    matches = [
        model
        for model in db.scalars(
            select(ModelConfig).where(
                ModelConfig.model_id == bundle.model_id,
                ModelConfig.active.is_(True),
            )
        ).all()
        if build_model_config_snapshot(model) == frozen
    ]
    if len(matches) != 1:
        raise ValueError("StrategyBundle 缺少唯一可执行的模型配置")
    return matches[0]


def _create_paired_regression(
    payload: PairedRegressionCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    *,
    commit: bool,
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, payload.sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    if sample_set.kind != "golden" or sample_set.status != "locked":
        raise HTTPException(
            status_code=400, detail="配对回归只能使用已锁定的黄金样本集"
        )
    baseline_bundle = db.get(
        StrategyBundle, payload.baseline_strategy_bundle_id
    )
    candidate_bundle = db.get(
        StrategyBundle, payload.candidate_strategy_bundle_id
    )
    if not baseline_bundle or not candidate_bundle:
        raise HTTPException(
            status_code=400, detail="基线或候选 StrategyBundle 不存在"
        )
    if baseline_bundle.id == candidate_bundle.id:
        raise HTTPException(
            status_code=400, detail="基线与候选 StrategyBundle 必须不同"
        )

    try:
        (
            baseline_strategy_snapshot_json,
            baseline_prompt_a,
            baseline_prompt_b,
        ) = _strategy_snapshot_for_bundle(db, baseline_bundle)
        (
            candidate_strategy_snapshot_json,
            candidate_prompt_a,
            candidate_prompt_b,
        ) = _strategy_snapshot_for_bundle(db, candidate_bundle)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    category_profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == sample_set.category_key
        )
    )
    category_automation = (
        json.loads(category_profile.automation_config_json or "{}")
        if category_profile is not None
        else {}
    )
    if (
        category_automation.get("baseline_strategy_bundle_id") is not None
        or candidate_prompt_a.source_automation_run_id is not None
        or candidate_prompt_b.source_automation_run_id is not None
    ):
        try:
            assert_bundle_pair_category_contract(
                baseline_bundle, candidate_bundle
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    baseline_contract_errors = category_bundle_contract_errors(
        db,
        profile=category_profile,
        bundle=baseline_bundle,
        require_complete=False,
        require_prompt_b=True,
        enforce_baseline_id=True,
    )
    candidate_contract_errors = category_bundle_contract_errors(
        db,
        profile=category_profile,
        bundle=candidate_bundle,
        require_complete=False,
        require_prompt_b=False,
        enforce_baseline_id=False,
    )
    if baseline_contract_errors or candidate_contract_errors:
        raise HTTPException(
            status_code=409,
            detail="StrategyBundle 与黄金集类目合同不一致",
        )
    if payload.trigger_prompt_id is not None and payload.trigger_prompt_id not in {
        candidate_prompt_a.id,
        candidate_prompt_b.id,
    }:
        raise HTTPException(
            status_code=400,
            detail="发布门禁提示词不属于候选 StrategyBundle",
        )

    requested_ids = [sample.sample_item_id for sample in payload.samples]
    sample_items = db.scalars(
        select(SampleSetItem).where(
            SampleSetItem.sample_set_id == sample_set.id,
            SampleSetItem.id.in_(requested_ids),
        )
    ).all()
    items_by_id = {item.id: item for item in sample_items}
    if set(items_by_id) != set(requested_ids):
        raise HTTPException(
            status_code=400, detail="存在不属于该样本集的样本"
        )
    if any(item.asset.category_key != sample_set.category_key for item in items_by_id.values()):
        raise HTTPException(status_code=422, detail="黄金集条目包含其他评测类目素材")
    if any(
        item.source_result.job.category_key != sample_set.category_key
        for item in items_by_id.values()
    ):
        raise HTTPException(
            status_code=422,
            detail="黄金集来源评测包含其他类目合同",
        )

    try:
        baseline_model = _model_config_for_bundle(db, baseline_bundle)
        candidate_model = _model_config_for_bundle(db, candidate_bundle)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if category_profile is None:
        raise HTTPException(status_code=409, detail="黄金集类目配置不存在")
    try:
        paired_dimension_contract = resolve_published_dimension_contract(
            db,
            schema_key=category_profile.dimension_schema_key,
            version=category_profile.dimension_schema_version,
            require_configured=False,
        )
    except ProductionDimensionContractError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    baseline_category_snapshot = _category_execution_snapshot(
        category_profile,
        prompt_a_id=baseline_prompt_a.id,
        prompt_b_id=baseline_prompt_b.id,
        model_config=baseline_model,
        dimension_contract=paired_dimension_contract,
    )
    candidate_category_snapshot = _category_execution_snapshot(
        category_profile,
        prompt_a_id=candidate_prompt_a.id,
        prompt_b_id=candidate_prompt_b.id,
        model_config=candidate_model,
        dimension_contract=paired_dimension_contract,
    )
    candidate_correction_contract = freeze_contract_from_execution_snapshot(
        category_key=sample_set.category_key,
        execution_snapshot=json.loads(candidate_category_snapshot),
    )

    frozen: list[dict[str, Any]] = []
    for requested in payload.samples:
        sample_item = items_by_id[requested.sample_item_id]
        try:
            truth_snapshot, review = reviewed_truth_snapshot(
                sample_item.source_result, requested.role
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        frozen.append(
            {
                "sample_item": sample_item,
                "role": requested.role,
                "truth_snapshot": truth_snapshot,
                "source_review_id": review.id,
            }
        )

    manifest = {
        "schema_version": "paired-sample-set-v1",
        "sample_set_id": sample_set.id,
        "items": [
            {
                "sample_item_id": entry["sample_item"].id,
                "asset_id": entry["sample_item"].asset_id,
                "role": entry["role"],
                "truth_revision": entry["sample_item"].truth_revision,
                "truth_snapshot": entry["truth_snapshot"],
            }
            for entry in frozen
        ],
    }
    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sample_set_version = hashlib.sha256(
        manifest_json.encode("utf-8")
    ).hexdigest()
    metric_rules = _paired_metric_rules(payload)
    metric_rules_json = json.dumps(
        metric_rules,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prior_rule_run = db.scalar(
        select(PromptRegressionRun)
        .where(
            PromptRegressionRun.regression_mode == "paired",
            PromptRegressionRun.metric_rules_version
            == payload.metric_rules_version.strip(),
        )
        .order_by(PromptRegressionRun.id.asc())
        .limit(1)
    )
    if (
        prior_rule_run
        and prior_rule_run.metric_rules_json != metric_rules_json
    ):
        raise HTTPException(
            status_code=400,
            detail="同一指标规则版本不能对应不同阈值",
        )

    run = PromptRegressionRun(
        name=payload.name.strip(),
        sample_set_id=sample_set.id,
        trigger_prompt_id=payload.trigger_prompt_id,
        prompt_a_id=candidate_prompt_a.id,
        prompt_b_id=candidate_prompt_b.id,
        regression_mode="paired",
        baseline_strategy_bundle_id=baseline_bundle.id,
        candidate_strategy_bundle_id=candidate_bundle.id,
        baseline_strategy_snapshot_json=baseline_strategy_snapshot_json,
        candidate_strategy_snapshot_json=candidate_strategy_snapshot_json,
        correction_contract_json=baseline_canonical_json(candidate_correction_contract),
        correction_contract_hash=correction_contract_hash(candidate_correction_contract),
        sample_set_version=sample_set_version,
        sample_manifest_json=manifest_json,
        metric_rules_version=payload.metric_rules_version.strip(),
        metric_rules_json=metric_rules_json,
        summary_json="{}",
        recommendation="pending",
        approval_status="pending",
        threshold=1.0,
        total=len(frozen),
        status="waiting_results",
        created_by=user.username,
    )
    db.add(run)
    db.flush()

    created_items: list[PromptRegressionItem] = []
    for entry in frozen:
        sample_item = entry["sample_item"]
        item = PromptRegressionItem(
            run_id=run.id,
            sample_item_id=sample_item.id,
            sample_role=entry["role"],
            source_evaluation_id=sample_item.source_result_id,
            source_review_id=entry["source_review_id"],
            truth_snapshot_json=json.dumps(
                entry["truth_snapshot"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            status="waiting_results",
        )
        db.add(item)
        db.flush()
        created_items.append(item)

    pending_item_ids: list[int] = []
    pending_job_ids: list[int] = []
    for item in created_items:
        sample_item = item.sample_item
        baseline = _latest_bundle_result(
            db,
            asset_id=sample_item.asset_id,
            strategy_bundle_id=baseline_bundle.id,
        )
        candidate = _latest_bundle_result(
            db,
            asset_id=sample_item.asset_id,
            strategy_bundle_id=candidate_bundle.id,
        )
        if baseline and candidate:
            complete_paired_regression_item(
                db,
                item=item,
                baseline=baseline,
                candidate=candidate,
            )
        else:
            pending_item_ids.append(item.id)
            if baseline is None:
                pending_job_ids.append(
                    _ensure_paired_validation_job(
                        db,
                        item=item,
                        bundle=baseline_bundle,
                        prompt_a=baseline_prompt_a,
                        prompt_b=baseline_prompt_b,
                        category_key=sample_set.category_key,
                        category_profile_snapshot_json=baseline_category_snapshot,
                    ).id
                )
            if candidate is None:
                candidate_job = _ensure_paired_validation_job(
                    db,
                    item=item,
                    bundle=candidate_bundle,
                    prompt_a=candidate_prompt_a,
                    prompt_b=candidate_prompt_b,
                    category_key=sample_set.category_key,
                    category_profile_snapshot_json=candidate_category_snapshot,
                )
                pending_job_ids.append(candidate_job.id)
                item.job_id = candidate_job.id
    refresh_paired_regression_run(db, run)
    if commit:
        db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "recommendation": run.recommendation,
        "approval_status": run.approval_status,
        "sample_set_version": run.sample_set_version,
        "pending_item_ids": pending_item_ids,
        "pending_job_ids": pending_job_ids,
    }


@app.post("/api/paired-regressions")
def create_paired_regression(
    payload: PairedRegressionCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _create_paired_regression(payload, user, db, commit=True)


@app.post("/api/paired-regressions/{run_id}/items/{item_id}/results")
def attach_paired_regression_results(
    run_id: int,
    item_id: int,
    payload: PairedRegressionResultsRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        select(PromptRegressionItem).where(
            PromptRegressionItem.id == item_id,
            PromptRegressionItem.run_id == run_id,
        )
    )
    if not item or item.run.regression_mode != "paired":
        raise HTTPException(status_code=404, detail="配对回归项不存在")
    baseline = db.get(EvaluationResult, payload.baseline_evaluation_id)
    candidate = db.get(EvaluationResult, payload.candidate_evaluation_id)
    if not baseline or not candidate:
        raise HTTPException(status_code=400, detail="基线或候选评测结果不存在")
    try:
        comparison = complete_paired_regression_item(
            db,
            item=item,
            baseline=baseline,
            candidate=candidate,
        )
    except ValueError as exc:
        status_code = 409 if item.status == "completed" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    db.commit()
    return {
        "ok": True,
        "status": item.status,
        "passed": item.passed,
        "comparison": comparison,
        "recommendation": item.run.recommendation,
    }


@app.post("/api/paired-regressions/{run_id}/approval")
def approve_paired_regression(
    run_id: int,
    payload: PairedRegressionApprovalRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptRegressionRun, run_id)
    if not run or run.regression_mode != "paired":
        raise HTTPException(status_code=404, detail="配对回归任务不存在")
    if run.recommendation == "pending":
        raise HTTPException(status_code=409, detail="配对回归尚未完成")
    if payload.status == "approved" and run.recommendation != "pass":
        raise HTTPException(
            status_code=409, detail="系统建议未通过，不能标记为人工批准"
        )
    if run.approval_status != "pending":
        if (
            run.approval_status == payload.status
            and run.approved_by == user.username
            and run.approval_note == payload.note
        ):
            return {
                "ok": True,
                "approval_status": run.approval_status,
                "published": False,
            }
        raise HTTPException(status_code=409, detail="人工批准结论已经冻结")
    run.approval_status = payload.status
    run.approved_by = user.username
    run.approval_note = payload.note
    run.approved_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "ok": True,
        "approval_status": run.approval_status,
        "published": False,
    }


@app.get("/api/prompt-regressions/{run_id}")
def prompt_regression_detail(
    run_id: int,
    outcome: Literal["passed", "failed", "pending", "sealed"] | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptRegressionRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="回归任务不存在")
    paired = run.regression_mode == "paired"
    if not paired:
        return {
            "summary": _regression_summary(run),
            "items": [
                {
                    "id": item.id,
                    "sample_item_id": item.sample_item_id,
                    "asset_id": item.sample_item.asset_id,
                    "asset_name": item.sample_item.asset.original_name,
                    "image_url": (
                        f"/api/assets/{item.sample_item.asset_id}/file"
                    ),
                    "sample_role": item.sample_role,
                    "source_evaluation_id": item.source_evaluation_id,
                    "source_review_id": item.source_review_id,
                    "truth_snapshot": None,
                    "expected": json.loads(
                        item.sample_item.truth_json or "{}"
                    ),
                    "status": item.status,
                    "passed": item.passed,
                    "comparison": json.loads(
                        item.comparison_json or "{}"
                    ),
                    "baseline_evaluation": None,
                    "candidate_evaluation": None,
                    "evaluation": _result_payload(item.evaluation),
                }
                for item in run.items
            ],
        }

    answer_visible = (
        run.status in {"passed", "regressed"}
        and run.recommendation in {"pass", "fail"}
        and run.finished_at is not None
    )
    item_payloads: list[dict[str, Any]] = []
    for item in run.items:
        truth_snapshot = json.loads(item.truth_snapshot_json or "{}")
        comparison = json.loads(item.comparison_json or "{}")
        baseline_result = json.loads(item.baseline_result_json or "{}")
        candidate_result = json.loads(item.candidate_result_json or "{}")
        blind_withheld = (
            item.sample_role == "blind_holdout" and not answer_visible
        )
        if blind_withheld:
            truth_snapshot_payload: dict[str, Any] | None = None
            expected_payload: dict[str, Any] | None = None
            comparison_payload: dict[str, Any] = {
                "withheld": True,
                "reason": "blind_holdout_pending",
            }
            baseline_result_payload = {
                key: baseline_result[key]
                for key in ("evaluation_id", "strategy_bundle_id", "fields")
                if key in baseline_result
            }
            candidate_result_payload = {
                key: candidate_result[key]
                for key in ("evaluation_id", "strategy_bundle_id", "fields")
                if key in candidate_result
            }
            diffs: list[dict[str, Any]] = []
            failure_reasons: list[dict[str, Any]] = []
            critical_regressions: list[str] = []
            new_severe_errors: list[dict[str, Any]] = []
            passed: bool | None = None
            failed: bool | None = None
            baseline_evaluation = None
            candidate_evaluation = None
        else:
            truth_snapshot_payload = truth_snapshot
            expected_payload = truth_snapshot.get("truth") or {}
            comparison_payload = comparison
            baseline_result_payload = baseline_result
            candidate_result_payload = candidate_result
            diffs = comparison.get("diffs") or []
            failure_reasons = comparison.get("failure_reasons") or []
            if item.status == "error" and not failure_reasons:
                failure_reasons = [
                    {
                        "code": "comparison_error",
                        "severity": "P0",
                        "message": str(
                            comparison.get("error")
                            or "样本配对比较失败"
                        ),
                    }
                ]
            critical_regressions = (
                comparison.get("critical_regressions") or []
            )
            new_severe_errors = (
                comparison.get("new_severe_errors") or []
            )
            passed = item.passed
            failed = (
                item.status == "error" or item.passed is False
                if item.status in {"completed", "error"}
                else None
            )
            baseline_evaluation = _result_payload(
                item.baseline_evaluation
            )
            candidate_evaluation = _result_payload(
                item.candidate_evaluation
            )

        item_outcome = (
            "sealed"
            if blind_withheld
            else "failed"
            if item.status == "error" or item.passed is False
            else "passed"
            if item.status == "completed" and item.passed is True
            else "pending"
        )
        item_payloads.append(
            {
                "id": item.id,
                "sample_item_id": item.sample_item_id,
                "asset_id": item.sample_item.asset_id,
                "asset_name": item.sample_item.asset.original_name,
                "image_url": (
                    f"/api/assets/{item.sample_item.asset_id}/file"
                ),
                "sample_role": item.sample_role,
                "source_evaluation_id": (
                    None
                    if blind_withheld
                    else item.source_evaluation_id
                ),
                "source_review_id": (
                    None if blind_withheld else item.source_review_id
                ),
                "truth_snapshot": truth_snapshot_payload,
                "expected": expected_payload,
                "answer_withheld": blind_withheld,
                "truth_revealed": not blind_withheld,
                "status": item.status,
                "outcome": item_outcome,
                "passed": passed,
                "failed": failed,
                "baseline_evaluation_id": item.baseline_evaluation_id,
                "candidate_evaluation_id": item.candidate_evaluation_id,
                "baseline_result": baseline_result_payload,
                "candidate_result": candidate_result_payload,
                "diffs": diffs,
                "failure_reasons": failure_reasons,
                "critical_regressions": critical_regressions,
                "new_severe_errors": new_severe_errors,
                "comparison": comparison_payload,
                "baseline_evaluation": baseline_evaluation,
                "candidate_evaluation": candidate_evaluation,
                "evaluation": candidate_evaluation,
            }
        )

    error_items = [
        {
            "item_id": item["id"],
            "sample_item_id": item["sample_item_id"],
            "asset_id": item["asset_id"],
            "asset_name": item["asset_name"],
            "image_url": item["image_url"],
            "sample_role": item["sample_role"],
            "status": item["status"],
            "passed": item["passed"],
            "baseline_evaluation_id": item["baseline_evaluation_id"],
            "candidate_evaluation_id": item["candidate_evaluation_id"],
            "failure_reasons": item["failure_reasons"],
            "critical_regressions": item["critical_regressions"],
            "new_severe_errors": item["new_severe_errors"],
        }
        for item in item_payloads
        if item["answer_withheld"] is False
        and (item["status"] == "error" or item["passed"] is False)
    ]
    filtered_items = (
        [
            item
            for item in item_payloads
            if item["outcome"] == outcome
        ]
        if outcome is not None
        else item_payloads
    )
    return {
        "summary": _regression_summary(run),
        "baseline_strategy": safe_strategy_snapshot_payload(
            run.baseline_strategy_snapshot_json
        ),
        "candidate_strategy": safe_strategy_snapshot_payload(
            run.candidate_strategy_snapshot_json
        ),
        "error_items": error_items,
        "item_filter": {"outcome": outcome},
        "items": filtered_items,
    }


@app.post("/api/prompt-regressions")
def create_prompt_regression(
    payload: RegressionCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = (
        db.get(SampleSet, payload.sample_set_id)
        if payload.sample_set_id is not None
        else None
    )
    if payload.sample_set_id is not None and sample_set is None:
        raise HTTPException(status_code=404, detail="黄金样本集不存在")
    category_key = sample_set.category_key if sample_set is not None else "space_image"
    prompt_a = db.get(PromptVersion, payload.prompt_a_id) if payload.prompt_a_id else db.scalar(
        select(PromptVersion)
        .where(
            PromptVersion.category_key == category_key,
            PromptVersion.stage == "A",
            PromptVersion.status == "published",
        )
        .order_by(PromptVersion.created_at.desc())
    )
    prompt_b = db.get(PromptVersion, payload.prompt_b_id) if payload.prompt_b_id else db.scalar(
        select(PromptVersion)
        .where(
            PromptVersion.category_key == category_key,
            PromptVersion.stage == "B",
            PromptVersion.status == "published",
        )
        .order_by(PromptVersion.created_at.desc())
    )
    if not prompt_a or prompt_a.stage != "A" or not prompt_b or prompt_b.stage != "B":
        raise HTTPException(status_code=400, detail="请选择有效的 A、B 提示词版本")
    if prompt_a.category_key != category_key or prompt_b.category_key != category_key:
        raise HTTPException(status_code=409, detail="提示词与黄金样本集属于不同评测类目")
    run_ids = _create_regression_runs(
        db,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        created_by=user.username,
        sample_set_id=payload.sample_set_id,
        threshold=payload.threshold,
    )
    if not run_ids:
        raise HTTPException(status_code=400, detail="没有可回归的已锁定黄金样本")
    db.commit()
    return {"ids": run_ids}


def _migration_summary(run: MigrationRun, items: list[MigrationItem]) -> dict[str, Any]:
    completed = sum(1 for item in items if item.candidate_result_id is not None)
    review_required = sum(1 for item in items if item.requires_review)
    reviewed = sum(1 for item in items if item.human_verdict is not None)
    verdicts = {
        verdict: sum(1 for item in items if item.human_verdict == verdict)
        for verdict in ("candidate_better", "same", "baseline_better")
    }
    auto_pass = sum(
        1 for item in items if item.candidate_result_id is not None and not item.requires_review
    )
    exact_rate = round(auto_pass / completed, 4) if completed else 0.0
    return {
        "id": run.id,
        "name": run.name,
        "baseline_model_id": run.baseline_model_id,
        "candidate_model_id": run.candidate_model_id,
        "sample_size": len(items),
        "status": run.status,
        "completed": completed,
        "pending": len(items) - completed,
        "review_required": review_required,
        "reviewed": reviewed,
        "auto_exact_rate": exact_rate,
        "verdicts": verdicts,
        "created_by": run.created_by,
        "created_at": run.created_at,
    }


def _refresh_migration(db: Session, run: MigrationRun) -> list[MigrationItem]:
    items = db.scalars(
        select(MigrationItem)
        .where(MigrationItem.run_id == run.id)
        .order_by(MigrationItem.id.asc())
    ).all()
    for item in items:
        if item.candidate_result_id is not None:
            continue
        candidate = db.scalar(
            select(EvaluationResult)
            .where(
                EvaluationResult.asset_id == item.asset_id,
                EvaluationResult.model_id == run.candidate_model_id,
                EvaluationResult.created_at >= run.created_at,
            )
            .order_by(EvaluationResult.created_at.desc())
            .limit(1)
        )
        if not candidate:
            continue
        baseline_payload = _result_payload(item.baseline_result) or {}
        if item.sample_expected_level:
            baseline_payload["final_level"] = item.sample_expected_level
        candidate_payload = _result_payload(candidate) or {}
        base_comparison = compare_results(baseline_payload, candidate_payload)
        audit_sample = not base_comparison["requires_review"] and item.asset_id % 20 == run.id % 20
        comparison = compare_results(
            baseline_payload,
            candidate_payload,
            audit_sample=audit_sample,
        )
        item.candidate_result_id = candidate.id
        item.requires_review = comparison["requires_review"]
        item.status = "review_required" if item.requires_review else "auto_pass"
        item.comparison_reason = json.dumps(comparison, ensure_ascii=False)

    pending = any(item.candidate_result_id is None for item in items)
    unreviewed = any(item.requires_review and not item.human_verdict for item in items)
    regression = any(item.human_verdict == "baseline_better" for item in items)
    if pending:
        run.status = "running"
    elif unreviewed:
        run.status = "review"
    elif regression:
        run.status = "regressed"
    else:
        run.status = "accepted"
    db.commit()
    return items


@app.get("/api/migrations/context")
def migration_context(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    rows = db.execute(
        select(EvaluationResult.model_id, func.count(func.distinct(EvaluationResult.asset_id)))
        .group_by(EvaluationResult.model_id)
        .order_by(func.count(func.distinct(EvaluationResult.asset_id)).desc())
    ).all()
    sample_sets = db.scalars(select(SampleSet).order_by(SampleSet.created_at.desc())).all()
    return {
        "candidate": {
            "model_id": model.model_id if model else "",
            "name": model.name if model else "未配置",
            "has_api_key": bool(model and model.encrypted_api_key),
        },
        "baselines": [
            {"model_id": model_id, "asset_count": count} for model_id, count in rows
        ],
        "sample_sets": [_sample_set_summary(sample_set) for sample_set in sample_sets],
    }


@app.post("/api/migrations")
def create_migration(
    payload: MigrationCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=400, detail="请先配置候选模型")
    if model.model_id == payload.baseline_model_id:
        raise HTTPException(status_code=400, detail="旧模型与候选模型不能相同")

    all_results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.model_id == payload.baseline_model_id)
        .order_by(EvaluationResult.created_at.desc())
    ).all()
    latest_by_asset: dict[int, EvaluationResult] = {}
    for result in all_results:
        latest_by_asset.setdefault(result.asset_id, result)
    if not latest_by_asset:
        raise HTTPException(status_code=400, detail="没有找到旧模型的历史评测结果")

    selected: list[EvaluationResult] = []
    selected_sample_set: SampleSet | None = None
    sample_expected_levels: dict[int, str | None] = {}
    if payload.sample_set_id:
        selected_sample_set = db.get(SampleSet, payload.sample_set_id)
        if not selected_sample_set:
            raise HTTPException(status_code=404, detail="样本集不存在")
        selected = [
            latest_by_asset[item.asset_id]
            for item in selected_sample_set.items
            if item.asset_id in latest_by_asset
        ]
        sample_expected_levels = {
            item.asset_id: item.expected_level for item in selected_sample_set.items
        }
        if not selected:
            raise HTTPException(status_code=400, detail="该样本集没有对应旧模型历史结果的素材")
    else:
        buckets: dict[tuple[str, str], list[EvaluationResult]] = {}
        for result in latest_by_asset.values():
            try:
                precheck = json.loads(result.precheck_json)
            except json.JSONDecodeError:
                precheck = {}
            category = ((precheck.get("classification") or {}).get("primary_category") or "无法判断")
            key = (result.level or "无等级", category)
            buckets.setdefault(key, []).append(result)

        ordered_buckets = [buckets[key] for key in sorted(buckets)]
        while len(selected) < min(payload.sample_size, len(latest_by_asset)):
            progressed = False
            for bucket in ordered_buckets:
                if bucket and len(selected) < payload.sample_size:
                    selected.append(bucket.pop(0))
                    progressed = True
            if not progressed:
                break

    run = MigrationRun(
        name=payload.name or (
            f"{model.model_id} · {selected_sample_set.name}"
            if selected_sample_set
            else f"{payload.baseline_model_id} → {model.model_id}"
        ),
        baseline_model_id=payload.baseline_model_id,
        candidate_model_id=model.model_id,
        sample_size=len(selected),
        created_by=user.username,
    )
    db.add(run)
    db.flush()
    for baseline in selected:
        db.add(
            MigrationItem(
                run_id=run.id,
                asset_id=baseline.asset_id,
                baseline_result_id=baseline.id,
                sample_expected_level=sample_expected_levels.get(baseline.asset_id),
            )
        )
        db.add(EvaluationJob(asset_id=baseline.asset_id))
        asset = db.get(Asset, baseline.asset_id)
        if asset:
            asset.status = "queued"
    db.commit()
    return {"id": run.id}


@app.get("/api/migrations")
def list_migrations(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    runs = db.scalars(select(MigrationRun).order_by(MigrationRun.created_at.desc())).all()
    summaries = []
    for run in runs:
        items = _refresh_migration(db, run)
        summaries.append(_migration_summary(run, items))
    return {"items": summaries}


@app.get("/api/migrations/{run_id}")
def migration_detail(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(MigrationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="迁移评测不存在")
    items = _refresh_migration(db, run)
    return {
        "summary": _migration_summary(run, items),
        "items": [
            {
                "id": item.id,
                "asset_id": item.asset_id,
                "asset_name": item.asset.original_name,
                "image_url": f"/api/assets/{item.asset_id}/file",
                "status": item.status,
                "requires_review": item.requires_review,
                "comparison": (
                    json.loads(item.comparison_reason) if item.comparison_reason else None
                ),
                "human_verdict": item.human_verdict,
                "reviewer_name": item.reviewer_name,
                "review_note": item.review_note,
                "baseline": _result_payload(item.baseline_result),
                "candidate": _result_payload(item.candidate_result),
            }
            for item in items
        ],
    }


@app.post("/api/migrations/{run_id}/items/{item_id}/review")
def review_migration_item(
    run_id: int,
    item_id: int,
    payload: MigrationReviewRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(MigrationItem).where(MigrationItem.id == item_id, MigrationItem.run_id == run_id)
    )
    if not item or item.candidate_result_id is None:
        raise HTTPException(status_code=404, detail="迁移样本尚未生成候选结果")
    item.human_verdict = payload.verdict
    item.reviewer_name = user.username
    item.review_note = payload.note
    item.reviewed_at = datetime.now(timezone.utc)
    item.status = "reviewed"
    db.commit()
    run = db.get(MigrationRun, run_id)
    if run:
        _refresh_migration(db, run)
    return {"ok": True}


@app.get("/{full_path:path}")
def frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 不存在")
    requested = settings.frontend_dist / full_path
    if full_path and requested.exists() and requested.is_file():
        return FileResponse(requested)
    index = settings.frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="前端尚未构建，请先运行前端开发服务")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
