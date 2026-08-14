"""Seed the initial 3D/SU model-aesthetic category contract.

The builders are pure JSON assembly helpers.  ``seed_model_3d_su`` is the
small persistence adapter used by startup seeding; it only owns the new
``model_3d_su`` rows and never touches the parallel ``three_d`` profile.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    COMMON_MODIFIERS_FORMAT_VERSION,
    canonical_contract_hash,
    validate_category_evaluation_contract,
)
from .category_pipeline import validate_pipeline_config
from .category_evaluation_v3_revisions import (
    ensure_projected_revision,
    sync_projected_revision,
)
from .dimension_composition import (
    SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
    validate_subcategory_dimensions,
)
from .dimension_deduction_bridge import extract_dimension_deduction_rules
from .dimension_schema_registry import canonical_json
from .models import CategoryEvaluationV3Config, EvaluationCategoryProfile, ModelConfig, PromptVersion
from .redline_policy import REDLINE_POLICY_FORMAT_VERSION
from .subcategory_resolver import CLASSIFICATION_MAP_FORMAT_VERSION, validate_classification_map


MODEL_3D_SU_CATEGORY_KEY = "model_3d_su"
MODEL_3D_SU_SPEC_VERSION = "model-3d-su-v1-dingtalk-20260814"
MODEL_3D_SU_RUBRIC_VERSION = "model-3d-su-rubric-v1"
MODEL_3D_SU_CALL_A_VERSION = "model-3d-su-a-v1-20260814"
MODEL_3D_SU_CALL_B_VERSION = "model-3d-su-b-v1-20260814"
MODEL_3D_SU_CREATED_BY = "system:model-3d-su-v1"
MODEL_3D_SU_SCHEMA_KEY = "model_3d_su_aesthetic"
MODEL_3D_SU_SCHEMA_VERSION = "v1"

TRACK_SPACE_BUILDING = "space_building"
TRACK_SOFT_FURNISHING = "soft_furnishing"
TRACK_FUNCTIONAL_MODEL = "functional_model"

_ASSET_DIR = Path(__file__).with_name("model_3d_su_assets")


def _rule(rule_id: str, description: str, deduction: int) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "description": description,
        "deduction": deduction,
        "tags": ["模型美感"],
    }


def _dimension(
    key: str,
    label: str,
    weight: float,
    *,
    minor: str,
    obvious: str,
    severe: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "weight": weight,
        "deduction_rules": [
            _rule("minor_defect", f"微瑕：{minor}", 20),
            _rule("obvious_defect", f"明显缺陷：{obvious}", 50),
            _rule("severe_defect", f"严重硬伤：{severe}", 80),
        ],
    }


def _track_dimensions(track_key: str, weights: list[float]) -> dict[str, Any]:
    details = _dimension(
        "model_detail",
        "模型细节",
        weights[0],
        minor="少量边缘倒角、接缝、螺丝或褶皱实体化不足",
        obvious="核心棱角无倒角，缝线/卡扣/沟槽主要依赖平面贴图",
        severe="主体比例畸形，大量结构细节缺失或软质材料呈硬质平板",
    )
    material = _dimension(
        "material_rendering",
        "质感渲染",
        weights[1],
        minor="粗糙度或微观印痕略有偏差",
        obvious="材质属性混淆、纹理重复或高光过曝",
        severe="贴图模糊/UV 拉伸，反光参数混乱且材质与现实属性脱节",
    )
    lighting = _dimension(
        "lighting",
        "光感表现",
        weights[2],
        minor="暗部细节略欠缺或局部补光略突兀",
        obvious="阴影单薄、边缘生硬、无来源亮光或接触阴影缺失",
        severe="光线违背物理衰减，出现大面积过曝/死黑或多光源冲突",
    )
    design = _dimension(
        "design_trend",
        "设计感及流行度",
        weights[3],
        minor="风格混搭生硬、设计平庸，或功能比例略别扭",
        obvious="整体过时、造型同质化，或功能布局明显不合理",
        severe="造型严重违背审美/制造/使用逻辑，形成虚假功能设计",
    )
    composition = _dimension(
        "visual_composition",
        "视觉构图",
        weights[4],
        minor="主体重心略偏或边缘有少量无关物件",
        obvious="构图过满/过空，主体裁切或被遮挡超过约 30%",
        severe="无视觉重心、元素堆砌，主体被杂乱背景淹没",
    )
    dimensions = [details, material, lighting, design, composition]
    if abs(sum(item["weight"] for item in dimensions) - 1.0) > 1e-9:
        raise ValueError(f"{track_key} 维度权重必须合计为 1.0")
    return {
        "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
        "sub_category_key": track_key,
        "dimension_max": 100,
        "common_group": {
            "group_weight": 1.0,
            "schema_definition": {
                "format_version": "dimension-schema-definition-v1",
                "schema_key": MODEL_3D_SU_SCHEMA_KEY,
                "version": MODEL_3D_SU_SCHEMA_VERSION,
                "dimensions": dimensions,
            },
        },
        "specific_group": {
            "schema_definition": {
                "format_version": "dimension-schema-definition-v1",
                "schema_key": f"{MODEL_3D_SU_SCHEMA_KEY}_specific",
                "version": MODEL_3D_SU_SCHEMA_VERSION,
                "dimensions": [],
            }
        },
    }


def _normalize_weight_points(points: list[int]) -> list[float]:
    """Convert document weight points into the strict contract fraction form."""
    total = sum(points)
    if total <= 0:
        raise ValueError("维度权重点数之和必须大于 0")
    return [point / total for point in points]


def build_model_3d_su_contract() -> dict[str, Any]:
    """Build and validate the frozen first-pass v3 contract."""
    contract = {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "spec_version": MODEL_3D_SU_SPEC_VERSION,
        "category_key": MODEL_3D_SU_CATEGORY_KEY,
        "level_semantics_version": "doc-l5-worst-v1",
        "level_scale": {
            "version": "category-level-scale-v1",
            "levels": [
                {"level": "L1", "enabled": True, "min_score": 80, "display_name": "好"},
                {"level": "L2", "enabled": True, "min_score": 61, "display_name": "中等"},
                {"level": "L3", "enabled": True, "min_score": 41, "display_name": "中差"},
                {"level": "L4", "enabled": True, "min_score": 0, "display_name": "极差"},
                {"level": "L5", "enabled": False, "display_name": "过滤"},
            ],
        },
        "prompt_bindings": {
            "call_a_version": MODEL_3D_SU_CALL_A_VERSION,
            "call_b_version": MODEL_3D_SU_CALL_B_VERSION,
        },
        "output_contract": {
            "format_version": "model-3d-su-output-v1",
            "class_specific_fields": {
                "watermark": {"type": "string", "required": True, "empty_value": ""},
                "unrendered": {"type": "string", "required": True, "empty_value": ""},
                "brand_trademark": {"type": "string", "required": True, "empty_value": ""},
                "brand_name": {"type": "string", "required": True, "empty_value": ""},
                "chinese": {"type": "string", "required": True, "empty_value": ""},
                "film_ip": {"type": "string", "required": True, "empty_value": ""},
                "celebrities": {"type": "string", "required": True, "empty_value": ""},
                "game_ip": {"type": "string", "required": True, "empty_value": ""},
                "other_ip": {"type": "string", "required": True, "empty_value": ""},
                "qr_code": {"type": "string", "required": True, "empty_value": ""},
                "landmark": {"type": "string", "required": True, "empty_value": ""},
                "religion": {"type": "string", "required": True, "empty_value": ""},
                "high_risk": {"type": "array", "required": True, "items": "string"},
            },
        },
        "redline_policy": {
            "format_version": REDLINE_POLICY_FORMAT_VERSION,
            "enabled": False,
            "hit_level": "L4",
            "hit_score_cap": 40,
            "rules": [],
        },
        "track_classification": {
            "format_version": "track-classification-v1",
            "default_track": TRACK_SPACE_BUILDING,
            "tracks": [
                {
                    "key": TRACK_SPACE_BUILDING,
                    "label": "空间/建筑类",
                    "base_score": 0,
                    "dimension_max": 100,
                    "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": MODEL_3D_SU_SCHEMA_KEY, "version": MODEL_3D_SU_SCHEMA_VERSION},
                },
                {
                    "key": TRACK_SOFT_FURNISHING,
                    "label": "软装家具类",
                    "base_score": 0,
                    "dimension_max": 100,
                    "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": MODEL_3D_SU_SCHEMA_KEY, "version": MODEL_3D_SU_SCHEMA_VERSION},
                },
                {
                    "key": TRACK_FUNCTIONAL_MODEL,
                    "label": "功能性模型",
                    "base_score": 0,
                    "dimension_max": 100,
                    "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": MODEL_3D_SU_SCHEMA_KEY, "version": MODEL_3D_SU_SCHEMA_VERSION},
                },
            ],
        },
        "common_modifiers": {
            "format_version": COMMON_MODIFIERS_FORMAT_VERSION,
            "media_type_penalty": {
                "enabled": False,
                "baseline": "render_3d",
                "penalties": {"real_photo": 0, "render_3d": 0, "ai_image": 0, "other": 0},
            },
            "high_score_veto": {
                "enabled": False,
                "threshold": 80,
                "cap_to": 79,
            },
        },
    }
    validate_category_evaluation_contract(contract)
    return contract


def build_model_3d_su_classification_map() -> dict[str, Any]:
    mapping = {
        "家装": TRACK_SPACE_BUILDING,
        "工装": TRACK_SPACE_BUILDING,
        "景观": TRACK_SPACE_BUILDING,
        "建筑": TRACK_SPACE_BUILDING,
        "空间": TRACK_SPACE_BUILDING,
        "软装": TRACK_SOFT_FURNISHING,
        "家具": TRACK_SOFT_FURNISHING,
        "家居": TRACK_SOFT_FURNISHING,
        "摆件": TRACK_SOFT_FURNISHING,
        "电子电器": TRACK_FUNCTIONAL_MODEL,
        "五金配件": TRACK_FUNCTIONAL_MODEL,
        "交通工具": TRACK_FUNCTIONAL_MODEL,
        "厨卫日常用品": TRACK_FUNCTIONAL_MODEL,
        "工业机械与零部件": TRACK_FUNCTIONAL_MODEL,
        "办公实用物品": TRACK_FUNCTIONAL_MODEL,
        "设施器械": TRACK_FUNCTIONAL_MODEL,
        "功能性模型": TRACK_FUNCTIONAL_MODEL,
    }
    result = {
        "format_version": CLASSIFICATION_MAP_FORMAT_VERSION,
        "min_confidence": 0.7,
        "category_to_subcategory": mapping,
        "out_of_scope_subcategory": TRACK_SPACE_BUILDING,
    }
    validate_classification_map(
        result,
        valid_track_keys={
            TRACK_SPACE_BUILDING,
            TRACK_SOFT_FURNISHING,
            TRACK_FUNCTIONAL_MODEL,
        },
    )
    return result


def build_model_3d_su_subcategory_dimensions() -> dict[str, dict[str, Any]]:
    configs = {
        TRACK_SPACE_BUILDING: _track_dimensions(
            TRACK_SPACE_BUILDING, [0.20, 0.25, 0.20, 0.20, 0.15]
        ),
        TRACK_SOFT_FURNISHING: _track_dimensions(
            TRACK_SOFT_FURNISHING, [0.25, 0.25, 0.20, 0.20, 0.10]
        ),
        TRACK_FUNCTIONAL_MODEL: _track_dimensions(
            # The source document expresses this track as 35:25:20:15:10
            # points (105 total).  Preserve that relative priority while
            # serializing the v3 contract's required sum-to-one fractions.
            TRACK_FUNCTIONAL_MODEL,
            _normalize_weight_points([35, 25, 20, 15, 10]),
        ),
    }
    for config in configs.values():
        validate_subcategory_dimensions(config)
    return configs


def model_3d_su_pipeline() -> dict[str, Any]:
    return validate_pipeline_config(
        {
            "schema_version": "category-pipeline-v1",
            "input_kind": "image",
            "allowed_suffixes": [".jpg", ".jpeg", ".png", ".webp", ".gif"],
            "processors": [
                {"module": "image.prepare", "enabled": True, "config": {}},
                {"module": "image.animated_contact_sheet", "enabled": True, "config": {}},
            ],
            "prompt_mode": "ab",
            "prompt_context": {
                "instruction": "按 3D/SU 模型合同区分空间建筑、软装家具和功能性模型；SU 白模/线框仅标记未渲染，不因白底或二维码触发红线。"
            },
            "dimensions": {"enabled": True, "mode": "all", "enabled_keys": []},
            "model_nodes": {
                "evaluation_main": True,
                "pdf_summary": False,
                "optimization": True,
                "benchmark": True,
                "diagnostic": True,
            },
        }
    )


def _read_prompt(settings: Any, filename: str) -> str:
    project_root = Path(settings.project_root)
    candidates = (
        project_root / "backend" / "prompts" / filename,
        project_root / "prompts" / filename,
        _ASSET_DIR / filename,
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"找不到 3D/SU 提示词 {filename}，已检查：{searched}")


def _seed_prompt(
    db: Session,
    *,
    stage: str,
    version: str,
    name: str,
    system_prompt: str,
    change_note: str,
) -> PromptVersion:
    existing = db.scalar(select(PromptVersion).where(PromptVersion.version == version))
    if existing is not None:
        if (
            existing.category_key != MODEL_3D_SU_CATEGORY_KEY
            or existing.stage != stage
            or existing.system_prompt != system_prompt
            or existing.rubric_version != MODEL_3D_SU_RUBRIC_VERSION
        ):
            raise RuntimeError(f"冻结提示词 {version} 已存在但内容或身份不匹配")
        return existing
    row = PromptVersion(
        stage=stage,
        category_key=MODEL_3D_SU_CATEGORY_KEY,
        pipeline_scope="shared",
        name=name,
        version=version,
        system_prompt=system_prompt,
        user_prompt="",
        rubric_version=MODEL_3D_SU_RUBRIC_VERSION,
        status="published",
        source="imported",
        change_note=change_note,
        created_by=MODEL_3D_SU_CREATED_BY,
    )
    db.add(row)
    db.flush()
    return row


def seed_model_3d_su(db: Session, settings: Any) -> None:
    """Idempotently seed the active profile and v3 contract for 3D/SU."""
    primary = db.scalar(
        select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc())
    )
    if primary is None:
        raise RuntimeError("缺少 active 评测模型，无法启用 3D/SU 模型类目")

    prompt_a = _seed_prompt(
        db,
        stage="A",
        version=MODEL_3D_SU_CALL_A_VERSION,
        name="3D/SU 模型分类与字段预检",
        system_prompt=_read_prompt(settings, "model_3d_su_call_a_v1.txt"),
        change_note="2026-08-14 钉钉 3D/SU 美感机制首版调用 A；输出平台通用字段与类目标记。",
    )
    prompt_b = _seed_prompt(
        db,
        stage="B",
        version=MODEL_3D_SU_CALL_B_VERSION,
        name="3D/SU 模型五维美感评审",
        system_prompt=_read_prompt(settings, "model_3d_su_call_b_v1.txt"),
        change_note="2026-08-14 钉钉 3D/SU 美感机制首版调用 B；输出五维等级与证据，不输出最终等级。",
    )

    pipeline = model_3d_su_pipeline()
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == MODEL_3D_SU_CATEGORY_KEY
        )
    )
    profile_values = {
        "display_name": "3D & SU 模型",
        "description": "model-3d-su-v1：三赛道五维美感评测，SU 未渲染和风险字段只做标记。",
        "status": "active",
        "allowed_mime_types_json": canonical_json(
            ["image/jpeg", "image/png", "image/webp", "image/gif"]
        ),
        "preprocess_config_json": canonical_json({"preprocess": "image", "su_unrendered_marker": True}),
        "pipeline_config_json": canonical_json(pipeline),
        "prompt_a_id": prompt_a.id,
        "prompt_b_id": prompt_b.id,
        "model_config_id": primary.id,
        "rubric_version": MODEL_3D_SU_RUBRIC_VERSION,
        "dimension_schema_key": None,
        "dimension_schema_version": None,
        "created_by": MODEL_3D_SU_CREATED_BY,
    }
    if profile is None:
        db.add(EvaluationCategoryProfile(category_key=MODEL_3D_SU_CATEGORY_KEY, pipeline_revision=1, **profile_values))
    elif profile.rubric_version not in {MODEL_3D_SU_RUBRIC_VERSION, ""}:
        raise RuntimeError("model_3d_su 已存在非本版本类目配置，拒绝覆盖")
    else:
        profile.prompt_a_id = profile.prompt_a_id or prompt_a.id
        profile.prompt_b_id = profile.prompt_b_id or prompt_b.id
        profile.model_config_id = profile.model_config_id or primary.id
        profile.created_by = profile.created_by or MODEL_3D_SU_CREATED_BY
        if profile.pipeline_config_json in {"", "{}"}:
            profile.pipeline_config_json = profile_values["pipeline_config_json"]
            profile.pipeline_revision = (profile.pipeline_revision or 0) + 1
        if profile.rubric_version == "":
            profile.rubric_version = MODEL_3D_SU_RUBRIC_VERSION

    contract = build_model_3d_su_contract()
    classification_map = build_model_3d_su_classification_map()
    subcategory_dimensions = build_model_3d_su_subcategory_dimensions()
    contract_json = canonical_json(contract)
    config_values = {
        "display_name": "3D & SU 模型",
        "status": "active",
        "contract_json": contract_json,
        "classification_map_json": canonical_json(classification_map),
        "subcategory_dimensions_json": canonical_json(subcategory_dimensions),
        "dimension_deduction_rules_json": canonical_json(
            extract_dimension_deduction_rules(subcategory_dimensions)
        ),
        "media_penalty_enabled": False,
        "contract_hash": canonical_contract_hash(contract),
        "created_by": MODEL_3D_SU_CREATED_BY,
    }
    row = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == MODEL_3D_SU_CATEGORY_KEY
        )
    )
    if row is None:
        row = CategoryEvaluationV3Config(
            category_key=MODEL_3D_SU_CATEGORY_KEY,
            revision=1,
            **config_values,
        )
        db.add(row)
        db.flush()
        ensure_projected_revision(db, row)
        return
    try:
        existing_contract = json.loads(row.contract_json or "{}")
    except (json.JSONDecodeError, TypeError):
        existing_contract = {}
    if existing_contract.get("spec_version") == MODEL_3D_SU_SPEC_VERSION:
        ensure_projected_revision(db, row)
        return
    if row.created_by != MODEL_3D_SU_CREATED_BY:
        raise RuntimeError("model_3d_su v3 合同已存在运营/外部版本，拒绝覆盖")
    sync_projected_revision(
        db,
        row,
        display_name=config_values["display_name"],
        status=config_values["status"],
        contract_json=config_values["contract_json"],
        classification_map_json=config_values["classification_map_json"],
        subcategory_dimensions_json=config_values["subcategory_dimensions_json"],
        dimension_deduction_rules_json=config_values[
            "dimension_deduction_rules_json"
        ],
        media_penalty_enabled=config_values["media_penalty_enabled"],
        contract_hash=config_values["contract_hash"],
        actor=MODEL_3D_SU_CREATED_BY,
    )
