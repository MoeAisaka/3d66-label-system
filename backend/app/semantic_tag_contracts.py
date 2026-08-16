"""Platform-wide semantic tag demand contract.

This module contains immutable, validated contract definitions only.  It does
not perform persistence, model calls, external projections, or other IO.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .dimension_schema_registry import canonical_json


PLATFORM_SEMANTIC_FIELD_KEYS = (
    "space",
    "object",
    "style",
    "material",
    "structural_features",
    "architectural_element",
    "soft_decoration",
    "hard_decoration",
    "color",
)
PLATFORM_SEMANTIC_CONTRACT_KEY = "semantic-platform"

_CATEGORY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class _FrozenDict(dict):
    """A dict-compatible mapping that rejects all ordinary mutations."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("contract mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze_mapping(value: Mapping[str, Any], *, nested: bool = False) -> _FrozenDict:
    items = value.items()
    if nested:
        items = ((key, _FrozenDict(item)) for key, item in items)
    return _FrozenDict(items)


class SemanticTagContractError(ValueError):
    """Raised when a semantic tag demand contract or field result is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SemanticTagValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(min_length=1, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    locale: Literal["zh", "en"]
    rank: int = Field(ge=1, le=100)
    weight: float | None = Field(default=None, ge=0, le=1)
    source: Literal["model", "rule", "human", "mixed"]
    evidence_ref: str = Field(min_length=1, max_length=320)
    model_version: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    normalization_version: str = Field(min_length=1, max_length=80)
    mapping_version: str = Field(min_length=1, max_length=80)
    review_status: Literal["candidate", "needs_review", "approved", "rejected"]


class SemanticFieldResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["required", "optional", "not_applicable", "not_detected", "needs_review"]
    values: tuple[SemanticTagValue, ...] = ()

    @model_validator(mode="after")
    def _validate_null_semantics(self) -> "SemanticFieldResult":
        if self.status in {"not_applicable", "not_detected"} and self.values:
            raise ValueError(f"status={self.status} 时 values 必须为空")
        if self.status == "required" and not self.values:
            raise ValueError("required 字段 values 不能为空")
        ranks = [item.rank for item in self.values]
        if len(ranks) != len(set(ranks)):
            raise ValueError("values 的 rank 不能重复")
        weights = [item.weight for item in self.values if item.weight is not None]
        if math.fsum(weights) > 1.0 + 1e-9:
            raise ValueError("values 的 weight 总和不能超过 1.0")
        object.__setattr__(self, "values", tuple(self.values))
        return self


SemanticApplicability = Literal["required", "optional", "not_applicable"]


class SemanticFieldDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_key: str
    cardinality: Literal["single", "multi"]
    localized: bool = True
    vocabulary_owner: str = Field(min_length=1, max_length=120)
    max_values: int = Field(ge=1, le=100)
    default_value: tuple[SemanticTagValue, ...] = ()

    @field_validator("default_value", mode="before")
    @classmethod
    def _require_structured_defaults(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError("default_value 必须是结构化数组，不能使用逗号拼接值")
        return value

    @model_validator(mode="after")
    def _validate_default_values(self) -> "SemanticFieldDefinition":
        if self.cardinality == "single" and len(self.default_value) > 1:
            raise ValueError("single 字段 default_value 最多只能有一个值")
        if len(self.default_value) > self.max_values:
            raise ValueError("default_value 数量不能超过 max_values")
        ranks = [item.rank for item in self.default_value]
        if len(ranks) != len(set(ranks)):
            raise ValueError("default_value 的 rank 不能重复")
        weights = [item.weight for item in self.default_value if item.weight is not None]
        if math.fsum(weights) > 1.0 + 1e-9:
            raise ValueError("default_value 的 weight 总和不能超过 1.0")
        object.__setattr__(self, "default_value", tuple(self.default_value))
        return self


class FieldQualityGate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_precision: float = Field(ge=0, le=1)
    min_recall: float = Field(ge=0, le=1)
    min_mapping_coverage: float = Field(ge=0, le=1)
    max_conflict_rate: float = Field(ge=0, le=1)


class ProjectionTargetDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_key: Literal["domestic_material_tags", "overseas_material_tags", "knowledge_graph"]
    mode: Literal["dry_run"]
    locale: Literal["zh", "en"]


class SourceIdentityContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_system: str = Field(min_length=1, max_length=120)
    object_grain: Literal["asset"]
    identity_fields: tuple[Literal["res_type", "ll_id"], ...]
    optional_disambiguator: Literal["res_id"] | None = None
    version_field: str = Field(min_length=1, max_length=80)
    deletion_field: str = Field(min_length=1, max_length=80)
    uniqueness_status: Literal["unverified", "verified", "conflict"]
    verification_evidence_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _freeze_identity_fields(self) -> "SourceIdentityContract":
        object.__setattr__(self, "identity_fields", tuple(self.identity_fields))
        return self


class FieldSupplyDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_key: str
    fact_namespace: Literal["semantic", "quality", "governance"]
    object_grain: Literal["asset", "image", "text_fragment"]
    production_method: Literal["source_direct", "rule", "model", "human", "hybrid"]
    source_authority: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    freshness_sla_hours: int = Field(ge=1, le=8760)
    null_semantics: tuple[
        Literal["not_applicable", "not_detected", "unknown", "empty_valid"], ...
    ]
    rollback_strategy: Literal["previous_release", "compensation_release"]

    @model_validator(mode="after")
    def _freeze_null_semantics(self) -> "FieldSupplyDefinition":
        object.__setattr__(self, "null_semantics", tuple(self.null_semantics))
        return self


class ExecutionVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site_scope: Literal["domestic", "overseas"]
    asset_scope: Literal["whole", "single", "other", "unknown"]
    locale: Literal["zh", "en"]
    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    prompt_variant: Literal["whole", "single"]
    prompt_version: str
    model_version: str
    field_applicability_overrides: dict[str, SemanticApplicability] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def _freeze_field_applicability_overrides(self) -> "ExecutionVariant":
        object.__setattr__(
            self,
            "field_applicability_overrides",
            _freeze_mapping(self.field_applicability_overrides),
        )
        return self


class SemanticTagSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["semantic-tag-schema-v1"]
    fields: dict[str, SemanticFieldDefinition]

    @model_validator(mode="after")
    def _freeze_fields(self) -> "SemanticTagSchema":
        object.__setattr__(self, "fields", _freeze_mapping(self.fields))
        return self


class TagDemandContractDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["tag-demand-contract-v1", "tag-demand-contract-v2"]
    semantic_schema: SemanticTagSchema
    category_applicability: dict[str, dict[str, SemanticApplicability]]
    execution_variants: tuple[ExecutionVariant, ...]
    quality_gates: dict[str, FieldQualityGate]
    projection_targets: tuple[ProjectionTargetDefinition, ...]
    source_identity: SourceIdentityContract | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    field_supply: dict[str, FieldSupplyDefinition] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def _validate_platform_contract(self) -> "TagDemandContractDefinition":
        missing_platform_fields = [
            key for key in PLATFORM_SEMANTIC_FIELD_KEYS if key not in self.semantic_schema.fields
        ]
        if missing_platform_fields:
            raise ValueError(
                "semantic_schema.fields 缺少平台语义字段：" + ", ".join(missing_platform_fields)
            )

        declared_fields = set(self.semantic_schema.fields)
        if not self.category_applicability:
            raise ValueError("category_applicability 不能为空")
        for category_key, matrix in self.category_applicability.items():
            if not _CATEGORY_KEY_PATTERN.fullmatch(category_key):
                raise ValueError(f"category_key 无效：{category_key}")
            missing = sorted(declared_fields - set(matrix))
            if missing:
                raise ValueError(
                    f"字段 {missing[0]} 在 {category_key} 适用性矩阵中缺失"
                )
            unknown = sorted(set(matrix) - declared_fields)
            if unknown:
                raise ValueError(
                    f"字段 {unknown[0]} 未在 semantic_schema.fields 中声明"
                )

        declared_categories = set(self.category_applicability)
        for variant in self.execution_variants:
            if variant.category_key not in declared_categories:
                raise ValueError(
                    f"{variant.category_key} 执行变体缺少适用性矩阵"
                )
            if variant.site_scope == "domestic" and variant.locale != "zh":
                raise ValueError("domestic 执行变体 locale 必须为 zh")
            if variant.site_scope == "overseas" and variant.locale != "en":
                raise ValueError("overseas 执行变体 locale 必须为 en")
            if variant.asset_scope == "whole" and variant.prompt_variant != "whole":
                raise ValueError("whole 执行变体必须满足 prompt_variant=whole")
            if variant.asset_scope == "single" and variant.prompt_variant != "single":
                raise ValueError("single 执行变体必须满足 prompt_variant=single")
            unknown_overrides = sorted(
                set(variant.field_applicability_overrides) - declared_fields
            )
            if unknown_overrides:
                raise ValueError(
                    f"执行变体覆盖字段 {unknown_overrides[0]} 未在 semantic_schema.fields 中声明"
                )

        if self.schema_version == "tag-demand-contract-v1":
            if self.source_identity is not None or self.field_supply:
                raise ValueError("v2 身份与字段供给配置不能写入 tag-demand-contract-v1")
            if any(variant.field_applicability_overrides for variant in self.execution_variants):
                raise ValueError("执行变体字段覆盖需要 tag-demand-contract-v2")
        else:
            if self.source_identity is None:
                raise ValueError("tag-demand-contract-v2 必须声明 source_identity")

            missing_supply = sorted(declared_fields - set(self.field_supply))
            if missing_supply:
                raise ValueError(f"字段 {missing_supply[0]} 缺少供给路径")
            unknown_supply = sorted(set(self.field_supply) - declared_fields)
            if unknown_supply:
                raise ValueError(
                    f"供给路径字段 {unknown_supply[0]} 未在 semantic_schema.fields 中声明"
                )
            for field_key, supply in self.field_supply.items():
                if supply.field_key != field_key:
                    raise ValueError(
                        f"字段 {field_key} 的供给路径 field_key 必须与映射键一致"
                    )

            if "model_3d_su" in declared_categories and self.source_identity.identity_fields != (
                "res_type",
                "ll_id",
            ):
                raise ValueError(
                    "model_3d_su 身份字段必须严格为 res_type + ll_id"
                )
            if (
                self.source_identity.uniqueness_status == "verified"
                and self.source_identity.verification_evidence_hash is None
            ):
                raise ValueError(
                    "verified 源身份必须提供 verification_evidence_hash"
                )
            if (
                self.source_identity.uniqueness_status == "conflict"
                and self.source_identity.verification_evidence_hash is not None
            ):
                raise ValueError(
                    "conflict 源身份不能提供 verification_evidence_hash"
                )
        object.__setattr__(
            self,
            "category_applicability",
            _freeze_mapping(self.category_applicability, nested=True),
        )
        object.__setattr__(self, "execution_variants", tuple(self.execution_variants))
        object.__setattr__(
            self,
            "quality_gates",
            _freeze_mapping(self.quality_gates),
        )
        object.__setattr__(self, "projection_targets", tuple(self.projection_targets))
        object.__setattr__(self, "field_supply", _freeze_mapping(self.field_supply))
        return self


def validate_semantic_field_result(payload: Mapping[str, Any]) -> SemanticFieldResult:
    """Validate one field result while exposing a stable contract error."""

    if not isinstance(payload, Mapping):
        raise SemanticTagContractError("field_result_not_object", "字段结果必须是对象")
    try:
        return SemanticFieldResult.model_validate(payload)
    except ValidationError as exc:
        raise SemanticTagContractError("field_result_invalid", str(exc)) from exc


def validate_tag_demand_contract(
    payload: Mapping[str, Any],
) -> TagDemandContractDefinition:
    """Validate and parse a platform semantic tag demand contract."""

    if not isinstance(payload, Mapping):
        raise SemanticTagContractError("contract_not_object", "标签需求合同必须是对象")
    try:
        return TagDemandContractDefinition.model_validate(payload)
    except ValidationError as exc:
        raise SemanticTagContractError("contract_invalid", str(exc)) from exc


def canonical_contract_hash(definition: TagDemandContractDefinition) -> str:
    payload = definition.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
