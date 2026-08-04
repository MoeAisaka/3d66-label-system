"""ADR-0033 Task 2b：v3 引擎**权威化**路由（仅对有 active v3 config 的新类目生效）。

Owner 拍板「直接换」（Path B）：为 ``inspiration_image`` 这一 **v1 里根本不存在** 的
新类目新增一条分支，直接用 v3 引擎产出权威 ``score``(0-100 越高越好) + ``level``；下游
直接取 ``score`` 百分值。这条分支**只**对 DB 里有 active v3 config 的类目生效，老类目
（space_image / material_image / pdf_text）一个字节都不碰。

与 ``worker_v3_shadow`` 影子模块的关键区别（**fail-closed，绝不降级**）：

- 影子失败 → skip / None，不影响权威 v1 分。
- 权威路径若共性 / 特有 grade 拿不齐、或引擎任意异常 → 抛 ``V3AuthoritativeError``，
  由 worker 侧转成「人工复核 + score/level=None」，**绝不静默降级成老引擎给出误导性
  分数**。

本模块自身是纯函数 + 一个 async 编排函数，完全隔离、可脱离 worker 单测。它复用（不另造）
影子模块里已有的 grade 映射与特有维度调用B 机件，只是把它们从「旁挂影子」提升为「权威
路径」。``v3_authoritative_category`` 只读、绝不 raise；能力接线好但 DB 无 active config
时行为必须与现状完全一致（返回 ``None`` → 老引擎）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .level_semantics import LEVEL_SEMANTICS_V3_L5_WORST

# 复用影子模块已有的只读加载与 grade 映射机件（不另造）。
from .worker_v3_shadow import (
    _common_grades_from_aesthetic,
    _dimension_defs,
    _dimension_keys,
    _load_active_v3_config,
    fetch_v3_specific_grades,
)

logger = logging.getLogger("3d66.worker.v3_authoritative")


class V3AuthoritativeError(RuntimeError):
    """v3 权威评分无法完成（fail-closed）。

    携带一个稳定的 ``code`` 便于程序化分支（与框架其它层的错误约定一致），message 是
    面向人的中文说明。worker 侧捕获它 → 记 review_reasons + needs_review=True +
    score/level=None，**绝不**掉进 ``calculate_score`` 给出老引擎的分。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def v3_authoritative_category(db: Session, category_key: Any) -> dict | None:
    """只读判定：``category_key`` 是否走 v3 权威引擎；是则装配并返回 bundle。

    查 ``_load_active_v3_config``（只读 active 记录）。有 active config 且能从它的三个
    json 字段解析出合法的 ``contract`` / ``classification_map`` /
    ``subcategory_dimensions`` → 返回 ``{"contract", "classification_map",
    "subcategory_dimensions", "config_revision"}``；否则（无 config / json 损坏 / 任意
    异常）返回 ``None``，让 worker fail-closed 到老引擎。

    **绝不 raise**：这是接线「能力」的只读闸门——DB 里没有 active config 时行为必须与
    现状逐字节一致。只发只读 SELECT，写任何东西都不做。
    """
    try:
        if not isinstance(category_key, str) or not category_key:
            return None
        config = _load_active_v3_config(db, category_key)
        if config is None:
            return None

        contract = json.loads(config.contract_json or "{}")
        classification_map = json.loads(config.classification_map_json or "{}")
        subcategory_dimensions = json.loads(config.subcategory_dimensions_json or "{}")

        # 结构最低限度自检：三块都必须是非空对象，否则视为不可用（fail-closed → None）。
        if (
            not isinstance(contract, dict)
            or not contract
            or not isinstance(classification_map, dict)
            or not classification_map
            or not isinstance(subcategory_dimensions, dict)
            or not subcategory_dimensions
        ):
            return None

        return {
            "contract": contract,
            "classification_map": classification_map,
            "subcategory_dimensions": subcategory_dimensions,
            "config_revision": config.revision,
        }
    except Exception as exc:  # noqa: BLE001 — 只读闸门：任何异常都 fail-closed 到老引擎
        logger.warning(
            "ADR-0033 v3 authoritative routing probe failed (fail-closed to v1): %s",
            exc,
        )
        return None


async def evaluate_v3_authoritative(
    client: Any,
    image_path: Any,
    mime_type: Any,
    *,
    v3_bundle: dict,
    precheck: Any,
    aesthetic: Any,
) -> dict:
    """编排一次 v3 权威评分，返回 ``evaluate_one`` 的 ``result``。

    链路（与影子 ``compute_v3_shadow`` 复用同一批纯函数，但**权威 / fail-closed**）：

    1. 先跑红线。命中 → 红线短路，无需任何 grade，直接 ``evaluate_one`` 走聚合器红线
       分支，返回 hard_reject / L5 / score≤49。
    2. 未命中 → ``resolve_subcategory`` 解析 track_key；取该 track 的 subcategory 配置，
       算出共性维度 key 集与特有维度 key 集。
    3. 共性 grade：用 ``_common_grades_from_aesthetic`` 从 v1 aesthetic 映射；若该 track
       有共性维度却映射不出完整 grade → 抛 ``V3AuthoritativeError``。
    4. 特有 grade：若该 track 有非空特有维度组 → 调 ``fetch_v3_specific_grades``（权威模式
       ``enabled=True``，因为已确定走 v3），拿不到完整 ok 结果 → 抛 ``V3AuthoritativeError``。
    5. ``evaluate_one`` 拿到齐备 grade 后产出 score/level/level_semantics_version/track_key/
       steps，返回其 ``result``。

    与影子的根本区别：影子拿不齐 grade → skip/None；这里是权威路径，拿不齐 → **必须
    fail-closed 抛错**，绝不静默降级成老引擎给出误导性分数。``evaluate_one`` 内部的
    确定性异常（合同 / 组合 / 聚合错误）也统一包成 ``V3AuthoritativeError`` 上抛。
    """
    # 延迟 import：seed 会拉起整套框架栈，保持惰性，且让 import 失败也走 fail-closed。
    from .inspiration_category_seed import evaluate_one
    from .redline_policy import evaluate_redlines
    from .subcategory_resolver import resolve_subcategory

    contract = v3_bundle["contract"]
    classification_map = v3_bundle["classification_map"]
    subcategory_dimensions = v3_bundle["subcategory_dimensions"]
    precheck_obj = precheck if isinstance(precheck, dict) else {}

    common_grades_by_track: dict[str, dict[str, int]] = {}
    specific_grades_by_track: dict[str, dict[str, int]] = {}

    try:
        redline = evaluate_redlines(precheck_obj, policy=contract["redline_policy"])
    except Exception as exc:  # noqa: BLE001 — 合同/红线装配异常，fail-closed 上抛
        raise V3AuthoritativeError(
            "redline_eval_failed", f"v3 红线评估失败：{exc}"
        ) from exc

    if not redline.get("hit"):
        # 非红线：必须把该 track 的共性 + 特有 grade 拿齐，否则 fail-closed 抛错。
        try:
            resolved = resolve_subcategory(
                precheck_obj,
                classification_map=classification_map,
                track_classification=contract["track_classification"],
            )
        except Exception as exc:  # noqa: BLE001 — 分类器装配异常，fail-closed 上抛
            raise V3AuthoritativeError(
                "subcategory_resolve_failed", f"v3 子类目解析失败：{exc}"
            ) from exc

        track_key = resolved["track_key"]
        track_config = subcategory_dimensions.get(track_key)
        if not isinstance(track_config, dict):
            raise V3AuthoritativeError(
                "missing_track_config",
                f"track {track_key} 缺少 subcategory_dimensions 配置，无法权威评分",
            )

        # 共性 grade：从 v1 aesthetic 忠实映射（v3 共性组复用 v13 空间 schema key）。
        common_keys = _dimension_keys(track_config.get("common_group"))
        if common_keys:
            common_grades = _common_grades_from_aesthetic(aesthetic, common_keys)
            if common_grades is None:
                raise V3AuthoritativeError(
                    "common_grade_unavailable",
                    f"track {track_key} 的共性维度 grade 无法从 aesthetic 完整映射，"
                    f"权威路径拒绝硬猜",
                )
            common_grades_by_track[track_key] = common_grades

        # 特有 grade：走专用调用B（权威模式 enabled=True），拿不齐即 fail-closed。
        specific_dims = _dimension_defs(track_config.get("specific_group"))
        if specific_dims:
            shadow = await fetch_v3_specific_grades(
                client,
                image_path,
                mime_type,
                track_key,
                specific_dims,
                enabled=True,
            )
            if not (isinstance(shadow, dict) and shadow.get("status") == "ok"):
                detail = (
                    shadow.get("error")
                    if isinstance(shadow, dict)
                    else "调用B 未返回结果"
                )
                raise V3AuthoritativeError(
                    "specific_grade_unavailable",
                    f"track {track_key} 的特有维度调用B 未产出完整 grade（{detail}），"
                    f"权威路径拒绝硬猜",
                )
            specific_grades_by_track[track_key] = shadow["grades"]

    try:
        outcome = evaluate_one(
            contract=contract,
            classification_map=classification_map,
            subcategory_dimensions=subcategory_dimensions,
            precheck=precheck_obj,
            common_grades_by_track=common_grades_by_track,
            specific_grades_by_track=specific_grades_by_track,
        )
    except Exception as exc:  # noqa: BLE001 — 引擎确定性异常，fail-closed 上抛，绝不降级
        raise V3AuthoritativeError(
            "v3_engine_failed", f"v3 权威评分引擎失败：{exc}"
        ) from exc

    return outcome["result"]


def _primary_confidence(precheck: Any) -> float | None:
    """从 precheck.classification 取 primary_confidence（0..1 数值，否则 None）。"""
    if not isinstance(precheck, dict):
        return None
    classification = precheck.get("classification")
    if not isinstance(classification, dict):
        return None
    value = classification.get("primary_confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_v3_authoritative_scoring(v3_result: dict, *, precheck: Any) -> dict:
    """把 ``evaluate_v3_authoritative`` 的 v3 result 映射成与老 scoring 同结构的 dict。

    ``score`` 是 0-100 权威分（越高越好），``level`` 是 doc-l5-worst 语义（L5 最差）。
    这两者并存不矛盾：高 score → 低 L 号。下游 ``EvaluationResult.score/level`` 与导出
    直接取用。needs_review 依 redline/raw_level 规则：红线命中或高分一票压分导致
    ``raw_level != level`` 时置 True。
    """
    hard_reject = bool(v3_result.get("hard_reject"))
    level = v3_result.get("level")
    raw_level = v3_result.get("raw_level")
    hit_rules = v3_result.get("hit_rules") or []
    confidence = _primary_confidence(precheck)

    review_reasons: list[str] = []
    if hard_reject:
        review_reasons.append(
            f"v3 红线命中 {list(hit_rules)}，直出 {level}（最差档），需人工确认"
        )
    if raw_level is not None and level is not None and raw_level != level:
        review_reasons.append(
            f"v3 高分一票压分触发：原始 {raw_level} → 压至 {level}，需人工确认"
        )
    needs_review = hard_reject or (
        raw_level is not None and level is not None and raw_level != level
    )

    caps = v3_result.get("caps") or []
    return {
        "engine_version": v3_result.get("aggregator_version"),
        "scoring_mode": "v3_authoritative",
        "formal": True,
        "experimental": False,
        "score": v3_result.get("score"),
        "level": level,
        "raw_level": raw_level,
        "confidence": confidence,
        "needs_review": bool(needs_review),
        "caps": list(caps),
        "review_reasons": list(dict.fromkeys(review_reasons)),
        "hard_reject": hard_reject,
        "hit_rules": list(hit_rules),
        "track_key": v3_result.get("track_key"),
        "steps": v3_result.get("steps") or [],
        "level_semantics_version": v3_result.get("level_semantics_version"),
    }


def build_v3_authoritative_error_scoring(exc: "V3AuthoritativeError") -> dict:
    """v3 权威路径 fail-closed 时的 scoring：score/level=None + 人工复核，绝不给老引擎分。

    与老引擎的 freeform_manual 失败语义同构（``score``/``level`` 皆 None、
    ``needs_review=True``、带 ``not_formal_reason``），但 ``scoring_mode`` 明确标注
    ``v3_authoritative_failed`` 以便区分。level_semantics 仍标 v3 语义。
    """
    reason = f"v3 权威评分失败（{exc.code}）：{exc}，需要人工判读，不降级为老引擎分"
    return {
        "engine_version": None,
        "scoring_mode": "v3_authoritative_failed",
        "formal": False,
        "experimental": False,
        "score": None,
        "level": None,
        "confidence": None,
        "needs_review": True,
        "caps": [],
        "review_reasons": [reason],
        "interpretation_status": "manual_required",
        "not_formal_reason": reason,
        "v3_error_code": exc.code,
        "level_semantics_version": LEVEL_SEMANTICS_V3_L5_WORST,
    }
