from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Mapping

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


def validate_pipeline_config(value: Mapping[str, Any]) -> dict[str, Any]:
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
    dimensions_enabled = dimensions.get("enabled", True)
    dimension_mode = dimensions.get("mode", "all")
    keys = dimensions.get("enabled_keys", [])
    if not isinstance(dimensions_enabled, bool) or dimension_mode not in {"all", "selected"}:
        raise ValueError("维度开关或模式无效")
    if not isinstance(keys, list) or any(not isinstance(item, str) or not item for item in keys):
        raise ValueError("启用维度列表无效")
    if dimensions_enabled is False:
        raise ValueError("当前 L1-L5 评分引擎必须至少启用一组维度")
    unknown_dimension_keys = set(keys) - DIMENSION_KEYS
    if unknown_dimension_keys:
        raise ValueError(f"包含未知维度指标：{', '.join(sorted(unknown_dimension_keys))}")
    if dimension_mode == "selected" and not keys:
        raise ValueError("按需维度模式必须至少选择一个指标")
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
        "dimensions": {
            "enabled": dimensions_enabled,
            "mode": dimension_mode,
            "enabled_keys": list(dict.fromkeys(keys)) if dimension_mode == "selected" else [],
        },
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
        "model_nodes": [
            {"key": key, **deepcopy(value)}
            for key, value in MODEL_NODE_CATALOG.items()
        ],
        "prompt_modes": ["follow", "single", "ab"],
    }


def pipeline_json(pipeline: Mapping[str, Any]) -> str:
    return json.dumps(validate_pipeline_config(pipeline), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
