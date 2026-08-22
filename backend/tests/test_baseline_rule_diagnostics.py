from __future__ import annotations

from app.baseline_rule_diagnostics import (
    build_rule_diagnostics,
    declared_rules,
)


def _track(dimensions):
    return {
        "sub_category_key": "functional_model",
        "common_group": {"schema_definition": {"dimensions": dimensions}},
    }


def _bundle(dimensions, *, level_scale=None, redline=None):
    return {
        "candidate_revision_id": 26,
        "config_revision": 9,
        "contract": {
            "level_scale": level_scale
            or {
                "levels": [
                    {"level": "L1", "min_score": 80, "enabled": True},
                    {"level": "L2", "min_score": 61, "enabled": True},
                    {"level": "L3", "min_score": 41, "enabled": True},
                    {"level": "L4", "min_score": 0, "enabled": True},
                    {"level": "L5", "enabled": False},
                ]
            },
            "redline_policy": redline
            or {"enabled": True, "hit_level": "L3", "hit_score_cap": 60, "rules": [{"key": "cg"}]},
        },
        "subcategory_dimensions": {"functional_model": _track(dimensions)},
    }


DIMENSIONS = [
    {
        "key": "model_detail",
        "label": "模型细节",
        "deduction_rules": [
            {"rule_id": "minor_defect", "description": "微瑕", "deduction": 30},
            {"rule_id": "major_defect", "description": "明显缺陷", "deduction": 60},
        ],
        "bonus_rules": [{"rule_id": "extra_polish", "description": "额外精修", "bonus": 5}],
    },
    {
        "key": "lighting",
        "label": "光照",
        "deduction_rules": [{"rule_id": "flat_light", "description": "平光", "deduction": 30}],
    },
]


def _item(score, level, *, hits=None, bonus=None, caps=None):
    dimensions = {}
    for dimension_key, rule_ids in (hits or {}).items():
        dimensions.setdefault(dimension_key, {})["hit_rules"] = [
            {"rule_id": rule_id} for rule_id in rule_ids
        ]
    for dimension_key, rule_ids in (bonus or {}).items():
        dimensions.setdefault(dimension_key, {})["hit_bonus_rules"] = [
            {"rule_id": rule_id} for rule_id in rule_ids
        ]
    if not dimensions:
        dimensions = {"model_detail": {"hit_rules": [], "hit_bonus_rules": []}}
    return {
        "predicted_level": level,
        "authoritative_score": score,
        "cap_reasons": caps or [],
        "scoring": {"dimension_deduction_output": {"dimensions": dimensions}},
    }


def test_declared_rules_flattens_deduction_and_bonus_rules():
    rules = declared_rules({"functional_model": _track(DIMENSIONS)})

    assert [(r["dimension_key"], r["rule_id"], r["kind"]) for r in rules] == [
        ("lighting", "flat_light", "deduction"),
        ("model_detail", "extra_polish", "bonus"),
        ("model_detail", "major_defect", "deduction"),
        ("model_detail", "minor_defect", "deduction"),
    ]
    assert rules[3]["points"] == 30
    assert rules[1]["points"] == 5


def test_same_rule_across_tracks_is_counted_once_and_lists_both_tracks():
    dimensions = {
        "functional_model": _track(DIMENSIONS),
        "soft_furnishing": _track(DIMENSIONS),
    }

    rules = declared_rules(dimensions)

    assert len(rules) == 4
    assert rules[0]["tracks"] == ["functional_model", "soft_furnishing"]


def test_inert_rule_layer_is_reported_when_no_rule_ever_fires():
    """规则层空转必须被明确标出。

    这正是把所有图判成 L1 的真实成因：模型一条规则都不报，于是调高扣分、
    下调红线阈值全部空转。看不到这一点，运营只会继续调错地方。
    """
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[_item(88, "L1") for _ in range(5)],
    )

    assert report["rule_layer_inert"] is True
    assert report["scored_item_count"] == 5
    assert report["items_without_rule_hits"] == 5
    assert report["declared_rule_count"] == 4
    assert report["never_hit_rule_count"] == 4
    assert report["items_with_score_caps"] == 0
    # 无扣分时分数落在哪一档 —— 解释了为什么全是 L1
    assert report["unpenalised_level"] == "L1"
    assert report["level_distribution"] == {"L1": 5}


def test_partial_hits_are_counted_per_rule_and_layer_is_not_inert():
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[
            _item(70, "L2", hits={"model_detail": ["minor_defect"]}),
            _item(70, "L2", hits={"model_detail": ["minor_defect"]}),
            _item(40, "L4", hits={"lighting": ["flat_light"]}),
            _item(88, "L1"),
        ],
    )

    assert report["rule_layer_inert"] is False
    assert report["items_without_rule_hits"] == 1
    by_rule = {(r["dimension_key"], r["rule_id"]): r["hit_count"] for r in report["rules"]}
    assert by_rule[("model_detail", "minor_defect")] == 2
    assert by_rule[("lighting", "flat_light")] == 1
    assert by_rule[("model_detail", "major_defect")] == 0
    assert report["never_hit_rule_count"] == 2  # major_defect 与 extra_polish
    assert report["unpenalised_level"] == "L1"


def test_bonus_hits_count_and_undeclared_hits_are_surfaced():
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[
            _item(92, "L1", bonus={"model_detail": ["extra_polish"]}),
            _item(60, "L3", hits={"model_detail": ["ghost_rule"]}),
        ],
    )

    by_rule = {(r["dimension_key"], r["rule_id"]): r["hit_count"] for r in report["rules"]}
    assert by_rule[("model_detail", "extra_polish")] == 1
    # 模型报了合同未声明的规则 id —— 必须暴露而非静默忽略
    assert report["undeclared_hits"] == [
        {"dimension_key": "model_detail", "rule_id": "ghost_rule", "hit_count": 1}
    ]


def test_score_buckets_and_redline_summary_are_included():
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[_item(88, "L1"), _item(84, "L1"), _item(52, "L3")],
    )

    assert report["score_buckets"] == {"50-59": 1, "80-89": 2}
    assert report["redline_policy"] == {
        "enabled": True,
        "hit_level": "L3",
        "hit_score_cap": 60,
    }
    assert report["redline_rule_count"] == 1
    assert "rules" not in report["redline_policy"]


def test_empty_run_is_not_reported_as_inert():
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS), item_snapshots=[]
    )

    assert report["rule_layer_inert"] is False
    assert report["scored_item_count"] == 0
    assert report["unpenalised_level"] is None


def test_unscored_items_still_contribute_level_and_cap_counts():
    """尚未产出维度输出的条目不算入规则统计，但等级与上限计数仍要如实反映。"""
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[
            {"predicted_level": "L1", "authoritative_score": 88, "cap_reasons": ["cg"]},
            _item(70, "L2", hits={"lighting": ["flat_light"]}),
        ],
    )

    assert report["scored_item_count"] == 1
    assert report["items_with_score_caps"] == 1
    assert report["level_distribution"] == {"L1": 1, "L2": 1}


def test_unpenalised_level_uses_median_so_one_outlier_does_not_skew_it():
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS),
        item_snapshots=[_item(88, "L1"), _item(85, "L1"), _item(10, "L4")],
    )

    # 中位数 85 → L1；若用均值 (61) 会误报成 L2
    assert report["unpenalised_level"] == "L1"


def test_disabled_levels_are_ignored_when_mapping_scores():
    scale = {
        "levels": [
            {"level": "L1", "min_score": 80, "enabled": True},
            {"level": "L5", "min_score": 95, "enabled": False},
        ]
    }
    report = build_rule_diagnostics(
        frozen_bundle=_bundle(DIMENSIONS, level_scale=scale),
        item_snapshots=[_item(97, "L1")],
    )

    assert report["unpenalised_level"] == "L1"
