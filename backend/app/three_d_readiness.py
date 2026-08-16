"""Local, machine-checkable readiness contract for the first 3D/SU slice."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.source_identity_probe import build_three_d_su_identity_probe


SOURCE_TABLE = "aliyun_3d66_dw.dim_res_info_union"
PLATFORM_FIELDS = (
    "space",
    "object",
    "style",
    "material",
    "structural_features",
    "architectural_element",
    "soft_decoration",
    "hard_decoration",
    "color",
    "title",
)


class ThreeDReadinessError(ValueError):
    """Raised when a readiness manifest violates the frozen contract."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IdentityReadiness(FrozenModel):
    table_name: str = SOURCE_TABLE
    key_fields: tuple[str, ...] = ("res_type", "ll_id")
    accepted_res_types: tuple[int, ...] = (1, 6)
    window_required: bool = True
    approval_state: Literal["pending", "signed"] = "pending"
    probe_hash: str
    duplicate_policy: Literal["fail_closed"] = "fail_closed"

    @model_validator(mode="after")
    def validate_frozen_identity(self) -> "IdentityReadiness":
        if self.table_name != SOURCE_TABLE:
            raise ValueError("身份源表必须使用已冻结的候选表")
        if self.key_fields != ("res_type", "ll_id"):
            raise ValueError("身份键必须为 res_type + ll_id")
        if self.accepted_res_types != (1, 6):
            raise ValueError("res_type 只允许 1/6")
        if len(self.probe_hash) != 64:
            raise ValueError("probe_hash 必须是 SHA-256")
        return self


class FieldReadiness(FrozenModel):
    platform_field_keys: tuple[str, ...] = PLATFORM_FIELDS
    category_extensions: tuple[str, ...] = (
        "category.model_3d_su.asset_variant",
        "category.model_3d_su.evaluation_track",
    )
    variants: tuple[str, ...] = ("whole", "single")
    min_precision: float = 0.80
    min_recall: float = 0.70
    owner_signoff_required: bool = True
    owner_signoff_evidence: str | None = None

    @model_validator(mode="after")
    def validate_frozen_fields(self) -> "FieldReadiness":
        if set(self.platform_field_keys) != set(PLATFORM_FIELDS):
            raise ValueError("平台字段必须完整且不得混入类目专有字段")
        if not self.category_extensions or any(
            not key.startswith("category.model_3d_su.")
            for key in self.category_extensions
        ):
            raise ValueError("3D/SU 扩展字段必须使用 category.model_3d_su 命名空间")
        if self.variants != ("whole", "single"):
            raise ValueError("素材形态必须同时覆盖 whole/single")
        if self.min_precision < 0.80:
            raise ValueError("Precision 门槛不得低于 0.80")
        if self.min_recall < 0.70:
            raise ValueError("Recall 门槛不得低于 0.70")
        return self


class GoldenSetReadiness(FrozenModel):
    minimum_count: int = 100
    required_strata: tuple[str, ...] = (
        "3D",
        "SU",
        "whole",
        "single",
        "space_architecture",
        "soft_decoration_furniture",
        "functional_model",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    )
    locked_revision_required: bool = True
    truth_change_policy: Literal["new_revision_only"] = "new_revision_only"
    owner_signoff_evidence: str | None = None

    @model_validator(mode="after")
    def validate_golden_set(self) -> "GoldenSetReadiness":
        if self.minimum_count < 100:
            raise ValueError("黄金集不得少于 100 条")
        required = {"3D", "SU", "whole", "single", "L1", "L2", "L3", "L4", "L5"}
        if not required.issubset(self.required_strata):
            raise ValueError("黄金集分层覆盖不完整")
        return self


class PermissionReadiness(FrozenModel):
    allowed: tuple[str, ...] = ("SELECT", "DESCRIBE")
    denied: tuple[str, ...] = (
        "DOWNLOAD",
        "UPDATE",
        "ALTER",
        "DROP",
        "INSERT",
        "DELETE",
    )
    approval_state: Literal["not_requested", "approved"] = "not_requested"
    approval_evidence: str | None = None

    @model_validator(mode="after")
    def validate_least_privilege(self) -> "PermissionReadiness":
        if self.allowed != ("SELECT", "DESCRIBE"):
            raise ValueError("只允许 SELECT/DESCRIBE 最小权限")
        required_denied = {"DOWNLOAD", "UPDATE", "ALTER", "DROP", "INSERT", "DELETE"}
        if not required_denied.issubset(self.denied):
            raise ValueError("权限拒绝清单不完整")
        return self


class RaciReadiness(FrozenModel):
    required_roles: tuple[str, ...] = (
        "product_owner",
        "data_owner",
        "algorithm_owner",
        "platform_owner",
        "review_owner",
        "consumer_owner",
    )
    assignments: dict[str, str] = Field(default_factory=dict)


class ExternalEffects(FrozenModel):
    connect_real_source: bool = False
    execute_sql: bool = False
    request_permissions: bool = False
    write_database: bool = False
    call_model: bool = False
    publish_labels: bool = False
    deploy: bool = False

    @model_validator(mode="after")
    def reject_external_effects(self) -> "ExternalEffects":
        if any(self.model_dump().values()):
            raise ValueError("前置冻结阶段禁止任何外部效果")
        return self


class ThreeDReadinessManifest(FrozenModel):
    schema_version: Literal["3d-su-readiness-v1"] = "3d-su-readiness-v1"
    category_key: Literal["model_3d_su"] = "model_3d_su"
    status: Literal["pending_external_signoff", "ready_for_real_ingress"] = (
        "pending_external_signoff"
    )
    identity: IdentityReadiness
    fields: FieldReadiness = FieldReadiness()
    golden_set: GoldenSetReadiness = GoldenSetReadiness()
    permissions: PermissionReadiness = PermissionReadiness()
    raci: RaciReadiness = RaciReadiness()
    external_effects: ExternalEffects = ExternalEffects()
    stop_conditions: tuple[str, ...] = (
        "real_source_contact",
        "sql_execution",
        "credential_request",
        "model_call",
        "external_database_write",
        "label_publish",
        "deployment",
    )

    @model_validator(mode="after")
    def require_signed_evidence_for_ready(self) -> "ThreeDReadinessManifest":
        if self.status != "ready_for_real_ingress":
            return self
        evidence_complete = (
            self.identity.approval_state == "signed"
            and bool(self.fields.owner_signoff_evidence)
            and bool(self.golden_set.owner_signoff_evidence)
            and self.permissions.approval_state == "approved"
            and bool(self.permissions.approval_evidence)
            and set(self.raci.required_roles).issubset(self.raci.assignments)
            and all(self.raci.assignments.values())
        )
        if not evidence_complete:
            raise ValueError("进入真实接入前必须完成身份、字段、黄金集、权限和 RACI 签认证据")
        return self


def build_three_d_readiness_manifest() -> ThreeDReadinessManifest:
    """Build the default pending manifest without contacting external systems."""

    probe = build_three_d_su_identity_probe(SOURCE_TABLE)
    return ThreeDReadinessManifest(
        identity=IdentityReadiness(probe_hash=probe.probe_hash)
    )


def validate_three_d_readiness_manifest(
    manifest: ThreeDReadinessManifest | dict[str, Any],
) -> ThreeDReadinessManifest:
    """Validate a manifest and expose a domain-specific failure type."""

    try:
        return ThreeDReadinessManifest.model_validate(manifest)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise ThreeDReadinessError(messages) from exc


def readiness_manifest_hash(manifest: ThreeDReadinessManifest) -> str:
    """Return a stable SHA-256 over the validated canonical manifest."""

    validated = validate_three_d_readiness_manifest(manifest)
    canonical = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
