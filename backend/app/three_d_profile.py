from __future__ import annotations

import json
import re
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .models import FieldDemandContract


THREE_D_PROFILE = "3d-asset-quality-v1"
THREE_D_CATEGORY_KEY = "model_3d_su"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ThreeDProfileError(ValueError):
    def __init__(self, code: str, message: str, *, target: str = "contract") -> None:
        super().__init__(message)
        self.code = code
        self.target = target


def _declared_paths(field_contract: FieldDemandContract) -> set[str]:
    try:
        fields = json.loads(field_contract.fields_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ThreeDProfileError(
            "three_d_field_contract_invalid",
            "3D 字段需求合同无法解析",
            target="contract.field_demand_contract_id",
        ) from exc
    if not isinstance(fields, list):
        raise ThreeDProfileError(
            "three_d_field_contract_invalid",
            "3D 字段需求合同 fields 必须是数组",
            target="contract.field_demand_contract_id",
        )
    return {
        str(item.get("source_path"))
        for item in fields
        if isinstance(item, Mapping) and isinstance(item.get("source_path"), str)
    }


def validate_three_d_profile_contract(
    db: Session | None,
    contract: Mapping[str, Any],
    *,
    require_database: bool = True,
) -> FieldDemandContract | None:
    if contract.get("schema_version") != "evaluation-category-profile-v3":
        raise ThreeDProfileError(
            "three_d_contract_schema_invalid",
            "3D 机制必须使用 evaluation-category-profile-v3",
        )
    if contract.get("profile_type") != THREE_D_PROFILE:
        raise ThreeDProfileError(
            "three_d_profile_type_invalid",
            f"3D 机制 profile_type 必须是 {THREE_D_PROFILE}",
        )
    category_key = contract.get("category_key")
    if not isinstance(category_key, str) or not category_key.strip():
        raise ThreeDProfileError(
            "three_d_category_missing",
            "3D 机制必须声明 category_key",
        )
    if category_key != THREE_D_CATEGORY_KEY:
        raise ThreeDProfileError(
            "three_d_category_key_invalid",
            f"3D/SU 组合类目必须使用 {THREE_D_CATEGORY_KEY}",
            target="contract.category_key",
        )
    field_contract_id = contract.get("field_demand_contract_id")
    if isinstance(field_contract_id, bool) or not isinstance(field_contract_id, int) or field_contract_id < 1:
        raise ThreeDProfileError(
            "three_d_field_contract_inactive",
            "3D 机制必须绑定正整数的现役字段需求合同 ID",
            target="contract.field_demand_contract_id",
        )
    field_contract: FieldDemandContract | None = None
    if db is None:
        if require_database:
            raise ThreeDProfileError(
                "three_d_field_contract_inactive",
                "3D 机制校验需要读取现役字段需求合同",
                target="contract.field_demand_contract_id",
            )
    else:
        field_contract = db.get(FieldDemandContract, field_contract_id)
        if field_contract is None or field_contract.status != "active":
            raise ThreeDProfileError(
                "three_d_field_contract_inactive",
                "3D 机制绑定的字段需求合同不存在或未启用",
                target="contract.field_demand_contract_id",
            )
        if field_contract.category_key != category_key:
            raise ThreeDProfileError(
                "three_d_field_contract_mismatch",
                "3D 机制与字段需求合同类目不一致",
                target="contract.field_demand_contract_id",
            )
    fingerprint = contract.get("source_schema_fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint.lower()):
        raise ThreeDProfileError(
            "three_d_source_fingerprint_missing",
            "3D 机制必须绑定 64 位来源 Schema 指纹",
            target="contract.source_schema_fingerprint",
        )
    stage_fields = contract.get("stage_fields")
    a_fields = stage_fields.get("a_quality_fields") if isinstance(stage_fields, Mapping) else None
    b_fields = stage_fields.get("b_aesthetic_fields") if isinstance(stage_fields, Mapping) else None
    if (
        not isinstance(a_fields, list)
        or not a_fields
        or not all(isinstance(item, str) and item.startswith(("quality.", "governance.")) for item in a_fields)
        or not isinstance(b_fields, list)
        or not b_fields
        or not all(isinstance(item, str) and item.startswith(("semantic.", "quality.")) for item in b_fields)
    ):
        raise ThreeDProfileError(
            "three_d_stage_fields_missing",
            "3D 机制必须声明非空的 A 阶段质量字段和 B 阶段美感字段",
            target="contract.stage_fields",
        )
    if field_contract is not None:
        contract_paths = _declared_paths(field_contract)
        undeclared = sorted((set(a_fields) | set(b_fields)) - contract_paths)
        if undeclared:
            raise ThreeDProfileError(
                "three_d_stage_field_not_in_contract",
                f"3D 阶段字段未出现在字段需求合同：{', '.join(undeclared)}",
                target="contract.stage_fields",
            )
    return field_contract
