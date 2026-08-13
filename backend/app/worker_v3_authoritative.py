"""ADR-0033 v3-only authoritative category evaluation.

All production categories must resolve an active or frozen v3 contract.
Missing, inactive, malformed, or category-mismatched contracts raise
V3AuthoritativeError before execution; v1 fallback is forbidden.

Scoring failures become manual review with score/level=None. Deterministic
grade and deduction helpers remain shared with the former shadow path.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .level_semantics import UNIFIED_LEVEL_SEMANTICS_VERSION

# 复用影子模块已有的只读加载与 grade 映射机件（不另造）。
from .mechanism_profiles import (
    MechanismProfileError,
    validate_mechanism_artifacts,
)

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


def _validate_v3_bundle(
    *,
    category_key: str,
    contract: Any,
    classification_map: Any,
    subcategory_dimensions: Any,
) -> None:
    """Apply the authoritative v3 validators to active and frozen bundles."""
    if (
        not isinstance(contract, dict)
        or not contract
        or not isinstance(classification_map, dict)
        or not classification_map
        or not isinstance(subcategory_dimensions, dict)
        or not subcategory_dimensions
    ):
        raise ValueError("合同、分类映射或赛道维度为空")
    if contract.get("category_key") != category_key:
        raise ValueError("合同 category_key 与评测类目不匹配")
    try:
        validate_mechanism_artifacts(
            contract,
            classification_map,
            subcategory_dimensions,
        )
    except MechanismProfileError as exc:
        raise ValueError(str(exc)) from exc


def v3_authoritative_category(db: Session, category_key: Any) -> dict:
    """Load and validate the active v3 authoritative bundle.

    Read-only; any missing or invalid state raises a stable fail-closed error.
    """
    if not isinstance(category_key, str) or not category_key:
        raise V3AuthoritativeError("v3_category_key_invalid", "评测类目标识无效")
    config = _load_active_v3_config(db, category_key)
    if config is None:
        raise V3AuthoritativeError(
            "v3_active_config_missing",
            f"类目 {category_key} 缺少 active v3 合同，已拒绝回退 v1",
        )
    try:
        contract = json.loads(config.contract_json or "{}")
        classification_map = json.loads(config.classification_map_json or "{}")
        subcategory_dimensions = json.loads(config.subcategory_dimensions_json or "{}")

        _validate_v3_bundle(
            category_key=category_key,
            contract=contract,
            classification_map=classification_map,
            subcategory_dimensions=subcategory_dimensions,
        )
        return {
            "contract": contract,
            "classification_map": classification_map,
            "subcategory_dimensions": subcategory_dimensions,
            "config_revision": config.revision,
        }
    except V3AuthoritativeError:
        raise
    except Exception as exc:  # noqa: BLE001 — 合同异常必须 fail-closed
        logger.error(
            "ADR-0033 active v3 contract invalid (v1 fallback forbidden): %s",
            exc,
        )
        raise V3AuthoritativeError(
            "v3_active_config_invalid",
            f"类目 {category_key} 的 active v3 合同无效：{exc}",
        ) from exc


def v3_authoritative_for_job(db: Session, job: Any) -> dict:
    """Resolve a job-bound v3 bundle, preferring its frozen snapshot.

    Historical jobs without a frozen marker still require an active v3 row.
    """

    snapshot_text = getattr(job, "category_profile_snapshot_json", None)
    if snapshot_text:
        try:
            snapshot = json.loads(snapshot_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise V3AuthoritativeError(
                "v3_frozen_config_invalid", "基线作业的冻结执行快照无法解析"
            ) from exc
        if isinstance(snapshot, dict) and "v3_authoritative_bundle" in snapshot:
            bundle = snapshot["v3_authoritative_bundle"]
            if not isinstance(bundle, dict) or any(
                not isinstance(bundle.get(key), dict) or not bundle.get(key)
                for key in ("contract", "classification_map", "subcategory_dimensions")
            ):
                raise V3AuthoritativeError(
                    "v3_frozen_config_invalid", "基线作业的冻结 v3 配置不完整"
                )
            if bundle["contract"].get("category_key") != getattr(
                job, "category_key", None
            ):
                raise V3AuthoritativeError(
                    "v3_frozen_config_invalid", "冻结 v3 合同与作业类目不匹配"
                )
            try:
                _validate_v3_bundle(
                    category_key=getattr(job, "category_key", None),
                    contract=bundle["contract"],
                    classification_map=bundle["classification_map"],
                    subcategory_dimensions=bundle["subcategory_dimensions"],
                )
            except Exception as exc:  # noqa: BLE001 — 冻结合同必须 fail-closed
                logger.error(
                    "ADR-0033 frozen v3 contract invalid "
                    "(v1 fallback forbidden): %s",
                    type(exc).__name__,
                )
                raise V3AuthoritativeError(
                    "v3_frozen_config_invalid", "基线作业的冻结 v3 配置无效"
                ) from exc
            return bundle
    return v3_authoritative_category(db, getattr(job, "category_key", None))


def v3_uses_rule_deductions(v3_bundle: Any, precheck: Any) -> bool:
    """Resolve whether this image's active track uses the new calling-B path."""
    if not isinstance(v3_bundle, dict) or not isinstance(precheck, dict):
        return False
    try:
        from .dimension_deduction_bridge import has_deduction_rules
        from .redline_policy import evaluate_redlines
        from .subcategory_resolver import resolve_subcategory

        contract = v3_bundle["contract"]
        if (
            contract.get("category_key") == "inspiration_image"
            and isinstance(contract.get("aesthetic_foundation"), dict)
        ):
            # 美感前置合同：红线短路B；非红线必须进入新的锚图B。
            return bool(evaluate_redlines(precheck, policy=contract["redline_policy"]).get("hit"))
        if evaluate_redlines(precheck, policy=contract["redline_policy"]).get("hit"):
            # Redlines terminate before B, but still need to bypass legacy B.
            return True
        resolved = resolve_subcategory(
            precheck,
            classification_map=v3_bundle["classification_map"],
            track_classification=contract["track_classification"],
        )
        config = v3_bundle["subcategory_dimensions"].get(resolved["track_key"])
        return has_deduction_rules(config)
    except Exception:  # noqa: BLE001 - probe only; authoritative path will report
        return False


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
    if contract.get("profile_type") == "text-proposal-additive-v1":
        from .proposal_text_aggregator import (
            ProposalTextAggregationError,
            aggregate_proposal_text_evaluation,
        )
        try:
            return aggregate_proposal_text_evaluation(
                contract, precheck_obj, aesthetic if isinstance(aesthetic, dict) else None
            )
        except ProposalTextAggregationError as exc:
            raise V3AuthoritativeError(
                "proposal_text_engine_failed", f"PDF方案文本权威定级失败：{exc}"
            ) from exc
    authoritative_precheck = contract.get("authoritative_precheck_contract")
    if (
        contract.get("category_key") == "inspiration_image"
        and isinstance(authoritative_precheck, dict)
    ):
        if authoritative_precheck.get("format_version") != (
            "inspiration-authoritative-precheck-v1"
        ):
            raise V3AuthoritativeError(
                "decisive_precheck_contract_invalid",
                "v3 权威前检合同版本无效",
            )
        validation = precheck_obj.get("decisive_signal_validation")
        if not isinstance(validation, dict) or validation.get("status") != (
            authoritative_precheck.get("required_validation_status")
        ):
            raise V3AuthoritativeError(
                "decisive_precheck_invalid", "调用A决定性信号缺失、不确定或证据冲突"
            )
    if (
        contract.get("category_key") == "inspiration_image"
        and isinstance(contract.get("aesthetic_foundation"), dict)
    ):
        from .inspiration_aesthetic_foundation import (
            AestheticFoundationError, apply_aesthetic_v3_rules,
        )
        try:
            return apply_aesthetic_v3_rules(
                contract=contract, classification_map=classification_map,
                precheck=precheck_obj, foundation=aesthetic,
            )
        except AestheticFoundationError as exc:
            raise V3AuthoritativeError(exc.code, str(exc)) from exc

    common_grades_by_track: dict[str, dict[str, int]] = {}
    specific_grades_by_track: dict[str, dict[str, int]] = {}
    rule_dimension_output: dict[str, Any] | None = None
    resolved_track_key: str | None = None

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
        resolved_track_key = track_key
        track_config = subcategory_dimensions.get(track_key)
        if not isinstance(track_config, dict):
            raise V3AuthoritativeError(
                "missing_track_config",
                f"track {track_key} 缺少 subcategory_dimensions 配置，无法权威评分",
            )

        from .dimension_deduction_bridge import (
            call_multimodal_for_dimension_deductions,
            compose_rule_deductions,
            has_deduction_rules,
            rule_scoring_mode,
        )

        if has_deduction_rules(track_config):
            active_rule_mode = rule_scoring_mode(track_config)
            public_scoring_mode = (
                "bonus_cap_v2"
                if active_rule_mode == "bonus_cap_v2"
                else "rule_deduction"
            )
            # New rule-deduction path: calling B judges rule hits only.  Provider
            # failure is converted by the bridge into empty hits + warning.
            rule_dimension_output = await call_multimodal_for_dimension_deductions(
                image_path,
                track_config,
                client=client,
                mime_type=mime_type,
                precheck=precheck_obj,
            )
            try:
                composed = compose_rule_deductions(
                    config=track_config,
                    dimension_output=rule_dimension_output,
                )
                from .category_evaluation_aggregator import (
                    aggregate_category_evaluation,
                )

                result = aggregate_category_evaluation(
                    contract, precheck_obj, composed, track_key=track_key
                )
                result["dimension_deduction_output"] = rule_dimension_output
                result["dimension_scoring_mode"] = public_scoring_mode
                return result
            except Exception as exc:  # noqa: BLE001 - deterministic contract fault
                raise V3AuthoritativeError(
                    "v3_rule_engine_failed", f"v3 规则计分聚合失败：{exc}"
                ) from exc

        # @deprecated fallback: contracts without deduction_rules keep the
        # historic 1-5 grade bridge byte-for-byte compatible.
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
                detail = shadow.get("error") if isinstance(shadow, dict) else "调用B 未返回结果"
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

    outcome["result"]["dimension_scoring_mode"] = "grade_fallback"
    outcome["result"]["resolved_track_key"] = resolved_track_key
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
    if v3_result.get("engine_version") == "proposal-text-additive-engine-v1":
        needs_review = bool(v3_result.get("needs_review"))
        reason = v3_result.get("reason")
        review_reasons = [str(reason)] if needs_review and reason else []
        return {
            "engine_version": v3_result.get("engine_version"),
            "scoring_mode": "v3_authoritative",
            "formal": not needs_review,
            "experimental": False,
            "score": v3_result.get("score"),
            "level": v3_result.get("level"),
            "raw_level": v3_result.get("level"),
            "confidence": None,
            "needs_review": needs_review,
            "caps": [],
            "review_reasons": review_reasons,
            "hard_reject": bool(v3_result.get("hard_reject")),
            "hit_rules": list(v3_result.get("redline_hits") or []),
            "track_key": v3_result.get("scoring_track"),
            "proposal_aesthetic_score": v3_result.get("proposal_aesthetic_score"),
            "visual_score": v3_result.get("visual_score"),
            "narrative_score": v3_result.get("narrative_score"),
            "innovation_timeliness_score": v3_result.get("innovation_timeliness_score"),
            "reason": reason,
            "evidence_notes": list(v3_result.get("evidence_notes") or []),
            "redline_hits": list(v3_result.get("redline_hits") or []),
            "status": v3_result.get("status"),
            "level_semantics_version": UNIFIED_LEVEL_SEMANTICS_VERSION,
        }
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
    dimension_output = v3_result.get("dimension_deduction_output")
    raw_dimension_payload = (
        {
            "provider_payload": dimension_output.get("raw_payload"),
            "prompt_identity": dimension_output.get("prompt_identity"),
        }
        if isinstance(dimension_output, dict)
        else None
    )
    public_dimension_output = (
        {
            key: value
            for key, value in dimension_output.items()
            if key != "raw_payload"
        }
        if isinstance(dimension_output, dict)
        else None
    )
    bridge_warning = (
        dimension_output.get("warning")
        if isinstance(dimension_output, dict)
        else None
    )
    if bridge_warning:
        review_reasons.append(str(bridge_warning))
        needs_review = True
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
        "hard_defect_action": v3_result.get("hard_defect_action"),
        "hit_rules": list(hit_rules),
        "track_key": v3_result.get("track_key"),
        "steps": v3_result.get("steps") or [],
        "dimension_scoring_mode": v3_result.get("dimension_scoring_mode"),
        "dimension_deduction_output": public_dimension_output,
        # Worker consumes then removes this transport-only field before
        # persisting scoring_json.  The provider payload belongs exclusively in
        # raw_response_b and must not be duplicated into the decision graph.
        "_dimension_deduction_raw_payload": raw_dimension_payload,
        "dimension_evidence": v3_result.get("dimension_evidence"),
        "media_penalty_enabled": v3_result.get("media_penalty_enabled"),
        "media_key": v3_result.get("media_key"),
        "media_penalty": v3_result.get("media_penalty"),
        "level_semantics_version": v3_result.get("level_semantics_version"),
        "inspiration_aesthetic_score": v3_result.get("inspiration_aesthetic_score"),
        "inspiration_aesthetic_level": v3_result.get("inspiration_aesthetic_level"),
        "foundation_sha256": v3_result.get("foundation_sha256"),
        "foundation_before_rules": v3_result.get("foundation_before_rules"),
        "foundation_after_rules": v3_result.get("foundation_after_rules"),
        "aesthetic_dimensions": v3_result.get("dimensions"),
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
        "level_semantics_version": UNIFIED_LEVEL_SEMANTICS_VERSION,
    }
