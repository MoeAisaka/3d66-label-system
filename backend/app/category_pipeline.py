from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    space_schema_definition_for_version,
)


CATEGORY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
PDF_MIME_TYPES = ("application/pdf",)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
PDF_SUFFIXES = (".pdf",)

MODEL_NODE_CATALOG: dict[str, dict[str, Any]] = {
    "evaluation_main": {"label": "主评测", "required": True},
    "pdf_summary": {"label": "文档多模态总结", "required": False},
    "optimization": {"label": "提示词优化", "required": False},
    "benchmark": {"label": "模型横评", "required": False},
    "diagnostic": {"label": "诊断", "required": False},
}
MODEL_NODE_KEYS = tuple(MODEL_NODE_CATALOG)
DIMENSION_OPTIONS = tuple(
    {
        "key": str(item["key"]),
        "label": str(item["label"]),
    }
    for item in space_schema_definition_for_version(ACTIVE_V13_VERSION)["dimensions"]
)
DIMENSION_KEYS = frozenset(item["key"] for item in DIMENSION_OPTIONS)
DIMENSION_MODE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "all",
        "label": "全部维度",
        "enabled": True,
        "prompt_only": False,
        "description": "使用绑定维度方案中的全部维度。",
    },
    {
        "key": "selected",
        "label": "指定维度",
        "enabled": True,
        "prompt_only": False,
        "description": "只启用管理员明确选择的维度。",
    },
    {
        "key": "none",
        "label": "关闭维度（仅提示词）",
        "enabled": False,
        "prompt_only": True,
        "description": "不启用维度方案，用于仅提示词质量实验。",
    },
)
DIMENSION_MODES = frozenset(item["key"] for item in DIMENSION_MODE_CATALOG)

PROCESSOR_CATALOG: dict[str, dict[str, Any]] = {
    "image.prepare": {
        "label": "图片标准化",
        "input_kinds": ["image"],
        "output_kind": "image",
        "config_schema": {},
    },
    "image.animated_contact_sheet": {
        "label": "动图关键帧接触表",
        "input_kinds": ["image"],
        "output_kind": "image",
        "config_schema": {"max_frames": {"label": "最多抽取帧数", "type": "integer", "min": 2, "max": 24, "default": 8}},
    },
    "document.pdf_extract": {
        "label": "PDF 文本提取",
        "input_kinds": ["pdf"],
        "output_kind": "document",
        "config_schema": {
            "max_pages": {"label": "最多处理页数", "type": "integer", "min": 1, "max": 20, "default": 4},
            "max_text_chars": {"label": "文本上限字符数", "type": "integer", "min": 1000, "max": 100000, "default": 24000},
        },
    },
    "document.ocr_if_needed": {
        "label": "必要时 OCR",
        "input_kinds": ["document"],
        "output_kind": "document",
        "config_schema": {"min_text_chars": {"label": "触发 OCR 的最少文本字符", "type": "integer", "min": 0, "max": 10000, "default": 80}},
    },
    "document.page_contact_sheet": {
        "label": "PDF 页图接触表",
        "input_kinds": ["document"],
        "output_kind": "document_image",
        "config_schema": {},
    },
    "document.multimodal_summary": {
        "label": "文档多模态总结",
        "input_kinds": ["document_image"],
        "output_kind": "document_image",
        "requires_model_node": "pdf_summary",
        "config_schema": {},
    },
    "context.material_focus": {
        "label": "材质专项关注",
        "input_kinds": ["image"],
        "output_kind": "image",
        "config_schema": {"enabled": {"label": "应用材质专项提示", "type": "boolean", "default": True}},
    },
}


def _processor(module: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"module": module, "enabled": True, "config": dict(config or {})}


DEFAULT_PIPELINES: dict[str, dict[str, Any]] = {
    "space_image": {
        "schema_version": "category-pipeline-v1",
        "input_kind": "image",
        "allowed_suffixes": list(IMAGE_SUFFIXES),
        "processors": [_processor("image.prepare"), _processor("image.animated_contact_sheet")],
        "prompt_mode": "follow",
        "prompt_context": {"instruction": ""},
        "dimensions": {"enabled": True, "mode": "all", "enabled_keys": []},
        "model_nodes": {"evaluation_main": True, "pdf_summary": False, "optimization": True, "benchmark": True, "diagnostic": True},
    },
    "pdf_text": {
        "schema_version": "category-pipeline-v1",
        "input_kind": "pdf",
        "allowed_suffixes": list(PDF_SUFFIXES),
        "processors": [
            _processor("document.pdf_extract", {"max_pages": 4, "max_text_chars": 24000}),
            _processor("document.ocr_if_needed", {"min_text_chars": 80}),
            _processor("document.page_contact_sheet"),
            _processor("document.multimodal_summary"),
        ],
        "prompt_mode": "single",
        "prompt_context": {"instruction": "基于文档正文、页图与总结评测，不要把页眉页脚当成主体。"},
        "dimensions": {"enabled": True, "mode": "all", "enabled_keys": []},
        "model_nodes": {"evaluation_main": True, "pdf_summary": True, "optimization": True, "benchmark": True, "diagnostic": True},
    },
    "material_image": {
        "schema_version": "category-pipeline-v1",
        "input_kind": "image",
        "allowed_suffixes": list(IMAGE_SUFFIXES),
        "processors": [_processor("image.prepare"), _processor("image.animated_contact_sheet"), _processor("context.material_focus")],
        "prompt_mode": "single",
        "prompt_context": {"instruction": ""},
        "dimensions": {"enabled": True, "mode": "all", "enabled_keys": []},
        "model_nodes": {"evaluation_main": True, "pdf_summary": False, "optimization": True, "benchmark": True, "diagnostic": True},
    },
}


def default_pipeline(category_key: str) -> dict[str, Any]:
    return deepcopy(DEFAULT_PIPELINES.get(category_key, DEFAULT_PIPELINES["space_image"]))


def legacy_preprocess_to_pipeline(category_key: str, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    config = dict(raw or {})
    pipeline = default_pipeline(category_key)
    if config.get("preprocess") == "pdf":
        pipeline = default_pipeline("pdf_text")
        pipeline["processors"][0]["config"] = {
            "max_pages": config.get("max_pages", 4),
            "max_text_chars": config.get("max_text_chars", 24000),
        }
    if config.get("material_focus") is True:
        pipeline = default_pipeline("material_image")
    elif category_key == "material_image" and config.get("material_focus") is False:
        pipeline = default_pipeline("material_image")
        pipeline["processors"][-1]["config"]["enabled"] = False
    return pipeline


def _validate_config(module: str, config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError(f"处理模块 {module} 的参数必须是对象")
    schema = PROCESSOR_CATALOG[module].get("config_schema", {})
    unknown = set(config) - set(schema)
    if unknown:
        raise ValueError(f"处理模块 {module} 包含未知参数：{', '.join(sorted(unknown))}")
    normalized: dict[str, Any] = {}
    for key, rule in schema.items():
        value = config.get(key, rule.get("default"))
        if value is None:
            continue
        if rule["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"处理模块 {module}.{key} 必须是整数")
        if rule["type"] == "boolean" and not isinstance(value, bool):
            raise ValueError(f"处理模块 {module}.{key} 必须是布尔值")
        if rule["type"] == "integer" and not rule["min"] <= value <= rule["max"]:
            raise ValueError(f"处理模块 {module}.{key} 超出允许范围")
        normalized[key] = value
    return normalized


def dimension_options_from_definition(
    definition: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return stable frontend metadata without mutating a schema definition."""

    if not isinstance(definition, Mapping):
        return []
    dimensions = definition.get("dimensions")
    if not isinstance(dimensions, list):
        return []
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(dimensions, start=1):
        if not isinstance(item, Mapping):
            return []
        key = item.get("key")
        label = item.get("label")
        if (
            not isinstance(key, str)
            or not key
            or key in seen
            or not isinstance(label, str)
            or not label
        ):
            return []
        seen.add(key)
        option: dict[str, Any] = {
            "key": key,
            "label": label,
            "display_order": item.get("display_order", index),
        }
        weight = item.get("weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            option["weight"] = float(weight)
        options.append(option)
    return options


def _normalize_dimension_config(
    value: Mapping[str, Any],
    *,
    allowed_dimension_keys: Iterable[str] = DIMENSION_KEYS,
) -> dict[str, Any]:
    unknown_fields = set(value) - {
        "enabled",
        "mode",
        "selected_keys",
        # category-pipeline-v1 originally exposed this name.  Keep accepting
        # and returning it while the frontend migrates to selected_keys.
        "enabled_keys",
    }
    if unknown_fields:
        raise ValueError(
            "维度配置包含未知字段：" + "、".join(sorted(unknown_fields))
        )

    raw_mode = value.get("mode")
    raw_enabled = value.get("enabled")
    if raw_enabled is not None and not isinstance(raw_enabled, bool):
        raise ValueError("维度 enabled 必须是布尔值")
    if raw_enabled is False and raw_mode in (None, "all", "none"):
        # category-pipeline-v1 clients historically only knew all/selected.
        # Treat their canonical disabled payload as the new explicit none mode.
        mode = "none"
    elif raw_mode is None:
        mode = "none" if raw_enabled is False else "all"
    elif isinstance(raw_mode, str) and raw_mode in DIMENSION_MODES:
        mode = raw_mode
    else:
        raise ValueError("维度模式必须是 all、selected 或 none")
    enabled = mode != "none" if raw_enabled is None else raw_enabled
    if enabled != (mode != "none"):
        raise ValueError("维度开关与模式不一致")

    selected_keys = value.get("selected_keys")
    legacy_keys = value.get("enabled_keys")
    if selected_keys is not None and legacy_keys is not None and selected_keys != legacy_keys:
        raise ValueError("维度 selected_keys 与 enabled_keys 不一致")
    keys = selected_keys if selected_keys is not None else legacy_keys
    if keys is None:
        keys = []
    if (
        not isinstance(keys, list)
        or len(keys) > 100
        or any(not isinstance(item, str) or not item for item in keys)
    ):
        raise ValueError("维度 selected_keys 必须是有效字符串列表")
    if len(keys) != len(set(keys)):
        raise ValueError("维度 selected_keys 不能重复")

    allowed = set(allowed_dimension_keys)
    unknown_dimension_keys = set(keys) - allowed
    if unknown_dimension_keys:
        raise ValueError(
            "包含未知维度指标："
            + "、".join(sorted(unknown_dimension_keys))
        )
    if mode == "selected" and not keys:
        raise ValueError("指定维度模式必须至少选择一个指标")
    if mode != "selected" and keys:
        raise ValueError(f"维度模式 {mode} 不能携带 selected_keys")

    canonical_keys = list(keys) if mode == "selected" else []
    return {
        "enabled": enabled,
        "mode": mode,
        "selected_keys": canonical_keys,
        "enabled_keys": canonical_keys,
    }


def dimension_selection_payload(
    pipeline: Mapping[str, Any],
    *,
    dimension_options: Iterable[Mapping[str, Any]] = DIMENSION_OPTIONS,
    schema_key: str | None = None,
    schema_version: str | None = None,
    schema_hash: str | None = None,
) -> dict[str, Any]:
    """Build the immutable per-job selection projection for one category."""

    options = [dict(item) for item in dimension_options]
    available_keys = [
        str(item["key"])
        for item in options
        if isinstance(item.get("key"), str) and item.get("key")
    ]
    dimensions = _normalize_dimension_config(
        pipeline.get("dimensions") or {},
        allowed_dimension_keys=available_keys,
    )
    mode = dimensions["mode"]
    effective_keys = (
        available_keys
        if mode == "all"
        else list(dimensions["selected_keys"])
        if mode == "selected"
        else []
    )
    return {
        "schema_version": "category-dimension-selection-v1",
        "enabled": dimensions["enabled"],
        "mode": mode,
        "selected_keys": list(dimensions["selected_keys"]),
        "effective_keys": effective_keys,
        "prompt_only": mode == "none",
        "source_schema": (
            {
                "schema_key": schema_key,
                "version": schema_version,
                "canonical_hash": schema_hash,
            }
            if schema_key is not None
            and schema_version is not None
            and schema_hash is not None
            else None
        ),
    }


def project_dimension_definition(
    definition: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project an immutable published schema for one frozen job selection.

    The registry definition remains the source of truth and is never mutated.
    ``selected`` jobs receive a transient scoring projection with weights
    renormalized over the selected keys.  ``none`` deliberately returns no
    scoring contract because prompt-only runs must not enter dimension
    aggregation at all.
    """

    mode = selection.get("mode")
    effective_keys = selection.get("effective_keys")
    if mode not in DIMENSION_MODES:
        raise ValueError("冻结维度选择模式无效")
    if (
        not isinstance(effective_keys, list)
        or len(effective_keys) != len(set(effective_keys))
        or any(not isinstance(key, str) or not key for key in effective_keys)
    ):
        raise ValueError("冻结维度有效键无效")
    if mode == "none":
        if effective_keys:
            raise ValueError("关闭维度模式不能携带有效维度")
        return None

    projected = deepcopy(dict(definition))
    source_dimensions = projected.get("dimensions")
    output_contract = projected.get("output_contract")
    if not isinstance(source_dimensions, list) or not isinstance(output_contract, dict):
        raise ValueError("维度方案缺少维度或输出合同")
    by_key = {
        item.get("key"): item
        for item in source_dimensions
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    if set(effective_keys) - set(by_key):
        raise ValueError("冻结维度选择包含方案外指标")
    selected_dimensions = [deepcopy(by_key[key]) for key in effective_keys]
    if not selected_dimensions:
        raise ValueError("启用维度模式至少需要一个有效维度")

    scoring_dimensions = [
        item
        for item in selected_dimensions
        if item.get("aggregation_role") == "score"
    ]
    total_weight = sum(float(item.get("weight") or 0.0) for item in scoring_dimensions)
    if total_weight <= 0:
        raise ValueError("所选维度没有可用评分权重")
    for item in scoring_dimensions:
        item["weight"] = float(item["weight"]) / total_weight

    projected["dimensions"] = selected_dimensions
    output_contract["dimension_output_keys"] = list(effective_keys)
    projected["output_contract"] = output_contract
    core_keys = projected.get("core_dimension_keys")
    if isinstance(core_keys, list):
        projected["core_dimension_keys"] = [
            key for key in core_keys if key in effective_keys
        ]
    risk_review = projected.get("risk_review")
    if isinstance(risk_review, dict):
        risk_review["dimension_keys"] = list(effective_keys)

    # Eight-dimension collapse/evidence/L5 calibration cannot be applied to a
    # subset without changing its statistical meaning.  Keep deterministic
    # level thresholds and server caps, but fail closed on those full-set-only
    # heuristics by binding them to a non-matching profile marker.
    aggregation = projected.get("aggregation")
    if mode == "selected" and isinstance(aggregation, dict):
        collapse_rule = aggregation.get("collapse_rule")
        if isinstance(collapse_rule, dict):
            collapse_rule["same_grade_count_for_review"] = min(
                int(collapse_rule.get("same_grade_count_for_review") or 1),
                len(scoring_dimensions),
            )
            collapse_rule["applies_to_scoring_profile"] = (
                "__full_dimension_schema_only__"
            )
        high_evidence_rule = aggregation.get("high_evidence_rule")
        if isinstance(high_evidence_rule, dict):
            high_evidence_rule["dimensions_for_l3_cap"] = min(
                int(high_evidence_rule.get("dimensions_for_l3_cap") or 1),
                len(scoring_dimensions),
            )
        top_level_rule = aggregation.get("top_level_rule")
        if isinstance(top_level_rule, dict):
            top_level_rule["grade_five_minimum_count"] = min(
                int(top_level_rule.get("grade_five_minimum_count") or 1),
                len(scoring_dimensions),
            )
    projected["selection_projection"] = {
        "schema_version": "dimension-selection-projection-v1",
        "mode": mode,
        "effective_keys": list(effective_keys),
        "source_schema": deepcopy(selection.get("source_schema")),
        "weight_policy": (
            "source_weights"
            if mode == "all"
            else "renormalize_selected_to_one"
        ),
    }
    return projected


def dimension_selection_from_job_snapshot(
    snapshot: str | Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Read a previously validated immutable job selection, if present."""

    if snapshot is None:
        return None
    if isinstance(snapshot, str):
        try:
            payload = json.loads(snapshot)
        except json.JSONDecodeError as exc:
            raise ValueError("任务冻结类目快照不是有效 JSON") from exc
    else:
        payload = dict(snapshot)
    if payload.get("schema_version") != "evaluation-category-profile-v2":
        return None
    selection = payload.get("dimension_selection")
    # v2 jobs accepted before category-dimension-selection-v1 are immutable
    # historical all-dimension runs.
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError("任务冻结类目快照维度选择损坏")
    if selection.get("schema_version") != "category-dimension-selection-v1":
        raise ValueError("任务冻结维度选择版本不受支持")
    mode = selection.get("mode")
    effective_keys = selection.get("effective_keys")
    if (
        mode not in DIMENSION_MODES
        or not isinstance(effective_keys, list)
        or len(effective_keys) != len(set(effective_keys))
        or any(not isinstance(key, str) or not key for key in effective_keys)
        or (mode == "none" and effective_keys)
        or (mode != "none" and not effective_keys)
    ):
        raise ValueError("任务冻结维度选择损坏")
    return deepcopy(selection)


def validate_pipeline_config(
    value: Mapping[str, Any],
    *,
    allowed_dimension_keys: Iterable[str] = DIMENSION_KEYS,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != "category-pipeline-v1":
        raise ValueError("类目流水线版本不受支持")
    input_kind = value.get("input_kind")
    if input_kind not in {"image", "pdf"}:
        raise ValueError("首期输入类型仅支持 image 或 pdf")
    processors = value.get("processors")
    if not isinstance(processors, list) or not processors:
        raise ValueError("类目至少需要一个处理模块")
    current_kind = input_kind
    normalized_processors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in processors:
        if not isinstance(item, Mapping) or not isinstance(item.get("module"), str):
            raise ValueError("处理模块定义无效")
        module = str(item["module"])
        if module not in PROCESSOR_CATALOG:
            raise ValueError(f"未知处理模块：{module}")
        if module in seen:
            raise ValueError(f"处理模块不能重复：{module}")
        seen.add(module)
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"处理模块 {module}.enabled 必须是布尔值")
        config = _validate_config(module, item.get("config", {}))
        if enabled:
            accepted = PROCESSOR_CATALOG[module]["input_kinds"]
            if current_kind not in accepted:
                raise ValueError(f"处理模块 {module} 不能接收 {current_kind} 输入")
            current_kind = PROCESSOR_CATALOG[module]["output_kind"]
        normalized_processors.append({"module": module, "enabled": enabled, "config": config})
    active_modules = {item["module"] for item in normalized_processors if item["enabled"]}
    if input_kind == "image" and "image.prepare" not in active_modules:
        raise ValueError("图片流水线必须包含 image.prepare")
    if input_kind == "pdf" and not {"document.pdf_extract", "document.page_contact_sheet"}.issubset(active_modules):
        raise ValueError("PDF 流水线必须包含文本提取和页图接触表")
    prompt_mode = value.get("prompt_mode", "single")
    if prompt_mode not in {"follow", "single", "ab"}:
        raise ValueError("提示词模式必须是 follow、single 或 ab")
    dimensions = value.get("dimensions", {})
    if not isinstance(dimensions, Mapping):
        raise ValueError("维度配置必须是对象")
    normalized_dimensions = _normalize_dimension_config(
        dimensions,
        allowed_dimension_keys=allowed_dimension_keys,
    )
    suffixes = value.get("allowed_suffixes", IMAGE_SUFFIXES if input_kind == "image" else PDF_SUFFIXES)
    if not isinstance(suffixes, list) or not suffixes or any(not isinstance(item, str) or not item.startswith(".") for item in suffixes):
        raise ValueError("允许后缀列表无效")
    allowed_suffixes = [item.lower() for item in dict.fromkeys(suffixes)]
    legal_suffixes = set(IMAGE_SUFFIXES if input_kind == "image" else PDF_SUFFIXES)
    if not set(allowed_suffixes).issubset(legal_suffixes):
        raise ValueError("文件后缀与输入类型不匹配")
    prompt_context = value.get("prompt_context", {})
    if not isinstance(prompt_context, Mapping) or not isinstance(prompt_context.get("instruction", ""), str):
        raise ValueError("提示词上下文配置无效")
    instruction = str(prompt_context.get("instruction", "")).strip()
    if len(instruction) > 2000:
        raise ValueError("类目附加指令不能超过 2000 字符")
    model_nodes = value.get("model_nodes", {})
    if not isinstance(model_nodes, Mapping) or any(not isinstance(v, bool) for v in model_nodes.values()):
        raise ValueError("模型节点开关无效")
    unknown_model_nodes = set(model_nodes) - set(MODEL_NODE_KEYS)
    if unknown_model_nodes:
        raise ValueError(f"包含未知模型节点：{', '.join(sorted(unknown_model_nodes))}")
    normalized_model_nodes = {
        key: bool(model_nodes.get(key, False)) for key in MODEL_NODE_KEYS
    }
    if not normalized_model_nodes["evaluation_main"]:
        raise ValueError("类目必须启用 evaluation_main 主评测节点")
    requires_summary = "document.multimodal_summary" in active_modules
    if requires_summary and not normalized_model_nodes["pdf_summary"]:
        raise ValueError("启用文档多模态总结时必须启用 pdf_summary 模型节点")
    return {
        "schema_version": "category-pipeline-v1",
        "input_kind": input_kind,
        "allowed_suffixes": allowed_suffixes,
        "processors": normalized_processors,
        "prompt_mode": prompt_mode,
        "prompt_context": {"instruction": instruction},
        "dimensions": normalized_dimensions,
        "model_nodes": normalized_model_nodes,
    }


def allowed_mimes_for_pipeline(pipeline: Mapping[str, Any]) -> tuple[str, ...]:
    return IMAGE_MIME_TYPES if pipeline.get("input_kind") == "image" else PDF_MIME_TYPES


def active_modules(pipeline: Mapping[str, Any]) -> set[str]:
    return {
        str(item["module"])
        for item in pipeline.get("processors", [])
        if isinstance(item, Mapping) and item.get("enabled", True)
    }


def processor_config(pipeline: Mapping[str, Any], module: str) -> dict[str, Any]:
    for item in pipeline.get("processors", []):
        if isinstance(item, Mapping) and item.get("module") == module and item.get("enabled", True):
            return dict(item.get("config") or {})
    return {}


def pipeline_catalog_payload() -> dict[str, Any]:
    return {
        "schema_version": "category-pipeline-catalog-v1",
        "input_kinds": [
            {"key": "image", "label": "图片 / 动图", "mime_types": list(IMAGE_MIME_TYPES), "suffixes": list(IMAGE_SUFFIXES)},
            {"key": "pdf", "label": "PDF 文档", "mime_types": list(PDF_MIME_TYPES), "suffixes": list(PDF_SUFFIXES)},
        ],
        "processors": [{"module": key, **deepcopy(value)} for key, value in PROCESSOR_CATALOG.items()],
        "dimension_options": deepcopy(list(DIMENSION_OPTIONS)),
        "dimension_modes": deepcopy(list(DIMENSION_MODE_CATALOG)),
        "dimension_config_contract": {
            "schema_version": "category-dimension-config-v1",
            "selected_key_field": "selected_keys",
            "legacy_selected_key_field": "enabled_keys",
            "published_schemas_immutable": True,
            "selection_is_frozen_per_job": True,
        },
        "model_nodes": [
            {"key": key, **deepcopy(value)}
            for key, value in MODEL_NODE_CATALOG.items()
        ],
        "prompt_modes": ["follow", "single", "ab"],
    }


def pipeline_json(pipeline: Mapping[str, Any]) -> str:
    return json.dumps(validate_pipeline_config(pipeline), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
