from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .dimension_schema_registry import canonical_hash
from .models import DimensionSchema, StrategyBundle
from .scoring import (
    DimensionScoringContractError,
    validate_dimension_scoring_contract,
)


class ProductionDimensionContractError(ValueError):
    """A category dimension contract is unsafe for production execution."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.technical_error_type = code
        self.retryable = False


@dataclass(frozen=True)
class ProductionDimensionContract:
    schema_id: int
    schema_key: str
    version: str
    canonical_hash: str
    definition: dict[str, Any]


def resolve_published_dimension_contract(
    db: Session,
    *,
    schema_key: str | None,
    version: str | None,
    bundle: StrategyBundle | None = None,
    require_configured: bool = False,
) -> ProductionDimensionContract | None:
    """Resolve one immutable, published schema and optionally verify its bundle copy."""
    if schema_key is None and version is None:
        if not require_configured:
            return None
        raise ProductionDimensionContractError(
            "dimension_contract_incomplete",
            "类目尚未配置生产维度方案，请选择已发布版本。",
        )
    if not schema_key or not version:
        raise ProductionDimensionContractError(
            "dimension_contract_incomplete",
            "类目维度合同不完整，请同时配置维度方案和版本。",
        )

    matches = db.scalars(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == schema_key,
            DimensionSchema.version == version,
        )
    ).all()
    if not matches:
        raise ProductionDimensionContractError(
            "dimension_contract_missing",
            "类目绑定的维度方案不存在，请重新选择已发布版本。",
        )
    if len(matches) != 1:
        raise ProductionDimensionContractError(
            "dimension_contract_ambiguous",
            "类目绑定的维度方案存在重复版本，已停止生产执行。",
        )
    schema = matches[0]
    if schema.status != "published":
        raise ProductionDimensionContractError(
            "dimension_contract_not_published",
            "类目绑定的维度方案尚未发布或已停用，请选择现役发布版本。",
        )
    try:
        definition = json.loads(schema.definition_json)
    except json.JSONDecodeError as exc:
        raise ProductionDimensionContractError(
            "dimension_contract_invalid",
            "类目绑定的维度方案内容损坏，已停止生产执行。",
        ) from exc
    if not isinstance(definition, dict) or canonical_hash(definition) != schema.canonical_hash:
        raise ProductionDimensionContractError(
            "dimension_contract_invalid",
            "类目绑定的维度方案校验失败，已停止生产执行。",
        )

    try:
        validate_dimension_scoring_contract(definition)
    except (DimensionScoringContractError, TypeError, ValueError) as exc:
        raise ProductionDimensionContractError(
            "dimension_contract_not_executable",
            "类目绑定的维度方案与当前评分引擎不兼容，已停止生产执行。",
        ) from exc

    if bundle is not None:
        try:
            frozen_set = json.loads(bundle.dimension_schema_set_snapshot or "")
        except json.JSONDecodeError as exc:
            raise ProductionDimensionContractError(
                "dimension_contract_not_executable",
                "StrategyBundle 冻结维度合同损坏，已停止生产执行。",
            ) from exc
        entries = frozen_set.get("schemas") if isinstance(frozen_set, dict) else None
        frozen_matches = [
            entry
            for entry in entries or []
            if isinstance(entry, dict)
            and entry.get("schema_key") == schema_key
            and entry.get("version") == version
        ]
        if len(frozen_matches) != 1:
            raise ProductionDimensionContractError(
                "dimension_contract_not_executable",
                "StrategyBundle 未唯一冻结类目现役维度方案，已停止生产执行。",
            )
        frozen = frozen_matches[0]
        if (
            frozen.get("canonical_hash") != schema.canonical_hash
            or frozen.get("definition") != definition
        ):
            raise ProductionDimensionContractError(
                "dimension_contract_not_executable",
                "StrategyBundle 冻结维度方案与注册表不一致，已停止生产执行。",
            )
    return ProductionDimensionContract(
        schema_id=schema.id,
        schema_key=schema.schema_key,
        version=schema.version,
        canonical_hash=schema.canonical_hash,
        definition=definition,
    )


def resolve_frozen_dimension_contract(
    snapshot: dict[str, Any],
    *,
    bundle: StrategyBundle | None = None,
) -> ProductionDimensionContract | None:
    """Validate a dimension contract frozen when a job was accepted."""
    frozen = snapshot.get("dimension_contract")
    if frozen is None:
        return None
    if not isinstance(frozen, dict):
        raise ProductionDimensionContractError(
            "dimension_contract_invalid",
            "任务冻结维度合同损坏，已停止生产执行。",
        )
    definition = frozen.get("definition")
    schema_key = frozen.get("schema_key")
    version = frozen.get("version")
    schema_hash = frozen.get("canonical_hash")
    schema_id = frozen.get("schema_id")
    if (
        not isinstance(schema_id, int)
        or not isinstance(schema_key, str)
        or not isinstance(version, str)
        or not isinstance(schema_hash, str)
        or not isinstance(definition, dict)
        or canonical_hash(definition) != schema_hash
    ):
        raise ProductionDimensionContractError(
            "dimension_contract_invalid",
            "任务冻结维度合同校验失败，已停止生产执行。",
        )
    try:
        validate_dimension_scoring_contract(definition)
    except (DimensionScoringContractError, TypeError, ValueError) as exc:
        raise ProductionDimensionContractError(
            "dimension_contract_not_executable",
            "任务冻结维度合同与当前评分引擎不兼容，已停止生产执行。",
        ) from exc
    if bundle is not None:
        try:
            frozen_set = json.loads(bundle.dimension_schema_set_snapshot or "")
        except json.JSONDecodeError as exc:
            raise ProductionDimensionContractError(
                "dimension_contract_not_executable",
                "StrategyBundle 冻结维度合同损坏，已停止生产执行。",
            ) from exc
        entries = frozen_set.get("schemas") if isinstance(frozen_set, dict) else None
        matches = [
            entry for entry in entries or []
            if isinstance(entry, dict)
            and entry.get("schema_key") == schema_key
            and entry.get("version") == version
            and entry.get("canonical_hash") == schema_hash
            and entry.get("definition") == definition
        ]
        if len(matches) != 1:
            raise ProductionDimensionContractError(
                "dimension_contract_not_executable",
                "StrategyBundle 与任务冻结维度合同不一致，已停止生产执行。",
            )
    return ProductionDimensionContract(
        schema_id=schema_id,
        schema_key=schema_key,
        version=version,
        canonical_hash=schema_hash,
        definition=definition,
    )
