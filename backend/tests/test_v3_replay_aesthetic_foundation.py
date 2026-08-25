"""纠偏重放必须保住调用B美感基础分。

背景：`recompute_qualified_v3` 是所有节点纠偏的重算入口。它曾经既没有开启
`require_foundation`，也没有把调用B的 `aesthetic_score` 作为 `initial_score`
传给撮合器，于是任何触发重放的纠偏都会从「维度满分」重新起算——输入一个字段
都不改，86 分 / L2 会被抬成 100 分 / L5 反向的 L1。爆炸半径是维度规则、
precheck 字段、红线、赛道四类纠偏（调用A字段与最终等级不触发重放）。

这批用例把三条边界一起钉住，避免再次回退：
1. 有美感分时重放必须幂等（原缺陷）；
2. 没有美感分的历史结果不能因此纠不了偏（修复时容易引入的反向回归）；
3. 赛道纠偏会重置维度集合，但美感分属于图片、不属于赛道，不能跟着被清掉。
"""

from __future__ import annotations

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.dimension_deduction_bridge import (
    compose_rule_deductions,
    empty_deduction_output,
)
from app.evaluation_v3_pipeline import recompute_qualified_v3
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)

AESTHETIC_SCORE = 86


def _context() -> dict:
    return {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 3,
    }


def _precheck() -> dict:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "建筑设计",
            "primary_confidence": 0.96,
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
        "hard_defects": [],
    }


def _output_with_foundation(config: dict) -> dict:
    """复刻一条正常评测落库的调用B输出：带美感分与可见证据。"""
    output = empty_deduction_output(config)
    output.update(
        {
            "aesthetic_score": AESTHETIC_SCORE,
            "aesthetic_evidence": ["构图均衡，主体清晰"],
            "aesthetic_confidence": 0.9,
            "raw_payload": {"provider": "fake"},
        }
    )
    # 正常结果没有失败告警；留着会被识别为「调用B失败」哨兵。
    output.pop("warning", None)
    return output


def _original_aggregate(context: dict, precheck: dict, output: dict) -> dict:
    """复刻 worker_v3_authoritative 的原始评测算法，作为重放的对照基准。"""
    config = context["subcategory_dimensions"]["class_one"]
    composed = compose_rule_deductions(
        config=config,
        dimension_output=output,
        require_foundation=True,
    )
    return aggregate_category_evaluation(
        context["contract"],
        precheck,
        composed,
        track_key="class_one",
        initial_score=composed.get("aesthetic_score"),
    )


def test_replay_keeps_call_b_score_so_corrections_do_not_inflate() -> None:
    """输入一字未改时，重放结果必须与原始评测完全一致。"""
    context = _context()
    precheck = _precheck()
    output = _output_with_foundation(context["subcategory_dimensions"]["class_one"])

    original = _original_aggregate(context, precheck, output)
    replayed = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=output,
    )

    assert original["score"] == AESTHETIC_SCORE, "对照基准本身应以美感分为初始分"
    assert replayed["score"] == original["score"], (
        "重放丢失调用B美感分会把分数抬回维度满分，使每次纠偏都虚高"
    )
    assert replayed["level"] == original["level"]
    assert replayed["initial_score"] == AESTHETIC_SCORE


def test_replay_still_works_for_rows_stored_without_call_b_score() -> None:
    """美感基座之前落库的历史结果不能因为缺美感分就纠不了偏。"""
    context = _context()
    precheck = _precheck()
    # 没有 aesthetic_score，等同于美感基座上线前的历史行。
    legacy_output = empty_deduction_output(
        context["subcategory_dimensions"]["class_one"]
    )

    replayed = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=legacy_output,
    )

    assert replayed["initial_score"] is None, "历史行没有美感分，应走旧的基准分口径"
    assert isinstance(replayed["score"], (int, float))
    assert replayed["level"].startswith("L")


def test_track_correction_keeps_call_b_score_when_dimensions_reset() -> None:
    """改赛道会重置维度命中，但美感分属于图片本身，必须保留。"""
    context = _context()
    precheck = _precheck()
    output = _output_with_foundation(context["subcategory_dimensions"]["class_one"])

    replayed = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=output,
        track_key="class_two",
    )

    assert replayed["track_key"] == "class_two"
    assert replayed["initial_score"] == AESTHETIC_SCORE, (
        "赛道纠偏重置维度集合时，不能把调用B美感分一起清掉"
    )


def test_redline_replay_reports_hit_without_needing_call_b_score() -> None:
    """红线在维度之前终止，缺少美感分也必须能重放。"""
    context = _context()
    precheck = _precheck()
    precheck["production_fields"]["reason"] = ["是截图"]
    output = empty_deduction_output(context["subcategory_dimensions"]["class_one"])

    replayed = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=output,
    )

    assert replayed["hit_rules"], "命中红线应记录命中规则"
    assert replayed["caps"], "红线必须留下封顶证据"
