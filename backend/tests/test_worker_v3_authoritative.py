"""ADR-0033 v3-only authoritative worker tests.

Exercises ``worker_v3_authoritative`` against isolated in-memory SQLite with
no queue, real database, or network.

Coverage:
1. Missing/corrupt active configs and invalid frozen bundles fail closed.
2. Redline, normal scoring, missing grades, and 调用B errors are deterministic.
3. Higher quality maps to higher score and lower L; L5 remains worst.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker, worker_v3_authoritative
from app.worker_v3_authoritative import (
    V3AuthoritativeError,
    build_v3_authoritative_error_scoring,
    build_v3_authoritative_scoring,
    evaluate_v3_authoritative,
    evaluate_v3_redline_prefilter,
    precheck_for_v3_scoring,
    v3_bundle_for_scoring,
    v3_authoritative_category,
    v3_authoritative_for_job,
)
from app.database import Base
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations
from app.models import Asset, CategoryEvaluationV3Config

_CATEGORY_KEY = "inspiration_image"

# 方案 A：一类/二类的真实 6 维度全部放在 common_group（specific_group 置空）。共性
# grade 从 v1 aesthetic 按这些 key 忠实映射。三类是另一套 5 维度（此文件用一类/二类）。
_COMMON_KEYS = (
    "visual_structure",
    "color_aesthetics",
    "emotional_expression",
    "design_aesthetics",
    "originality",
    "design_trendiness",
)


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_active_config(db: Session, category_key: str = _CATEGORY_KEY) -> int:
    config = CategoryEvaluationV3Config(
        category_key=category_key,
        display_name="灵感图 v3",
        status="active",
        contract_json=json.dumps(build_inspiration_v3_contract(), ensure_ascii=False),
        classification_map_json=json.dumps(
            build_inspiration_classification_map(), ensure_ascii=False
        ),
        subcategory_dimensions_json=json.dumps(
            build_inspiration_subcategory_dimensions(), ensure_ascii=False
        ),
        revision=9,
    )
    db.add(config)
    db.commit()
    return config.revision


def test_baseline_job_prefers_frozen_v3_bundle(sessions) -> None:
    frozen = {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 17,
    }
    job = SimpleNamespace(
        category_key=_CATEGORY_KEY,
        baseline_regression_item_id=123,
        category_profile_snapshot_json=json.dumps(
            {"v3_authoritative_bundle": frozen}, ensure_ascii=False
        ),
    )
    with sessions() as db:
        _seed_active_config(db)
        assert v3_authoritative_for_job(db, job) == frozen


def test_historical_job_without_frozen_v3_bundle_uses_active_config(sessions) -> None:
    job = SimpleNamespace(
        category_key=_CATEGORY_KEY,
        baseline_regression_item_id=123,
        category_profile_snapshot_json=json.dumps({"schema_version": "legacy"}),
    )
    with sessions() as db:
        _seed_active_config(db)
        resolved = v3_authoritative_for_job(db, job)
        assert resolved is not None
        assert resolved["config_revision"] == 9


def test_worker_resolves_candidate_l5_stored_name_from_frozen_asset_id(sessions) -> None:
    contract = build_inspiration_v3_contract()
    for anchor in contract["aesthetic_foundation"]["anchors"]:
        anchor.pop("stored_name")
    contract["aesthetic_foundation"]["anchors"].append({
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": "f" * 64,
    })
    with sessions() as db:
        for index, anchor in enumerate(contract["aesthetic_foundation"]["anchors"]):
            stored_name = (
                "server-owned-candidate-l5.jpeg"
                if anchor["asset_id"] == 339
                else f"server-owned-anchor-{index}.jpeg"
            )
            db.add(Asset(
                id=anchor["asset_id"],
                original_name=f"anchor-{anchor['level']}.jpeg",
                stored_name=stored_name,
                mime_type=anchor["mime_type"],
                size_bytes=1,
                sha256=anchor["sha256"],
                category_key=_CATEGORY_KEY,
            ))
        db.commit()

        assets = worker.resolve_frozen_anchor_assets(db, contract)

    assert assets[339].stored_name == "server-owned-candidate-l5.jpeg"
    assert assets[339].mime_type == "image/jpeg"
    assert assets[339].sha256 == "f" * 64


def test_worker_reads_asset_backend_for_legacy_four_anchor_contract(sessions) -> None:
    """A frozen stored_name cannot reveal that an anchor now lives on the NAS.

    The legacy four anchors froze a ``stored_name`` back when every asset was
    local.  After those files moved to the read-only NAS share, only the asset
    table knows which backend holds them, so the lookup must still run.
    """
    contract = build_inspiration_v3_contract()
    anchors = contract["aesthetic_foundation"]["anchors"]
    assert all("stored_name" in anchor for anchor in anchors)

    with sessions() as db:
        for anchor in anchors:
            db.add(Asset(
                id=anchor["asset_id"],
                original_name=f"anchor-{anchor['level']}.png",
                stored_name=anchor["stored_name"],
                storage_backend="nas_maps",
                source_uri=f"nas://maps/anchors/{anchor['level']}.png",
                mime_type=anchor["mime_type"],
                size_bytes=1,
                sha256=anchor["sha256"],
                category_key=_CATEGORY_KEY,
            ))
        db.commit()

        assets = worker.resolve_frozen_anchor_assets(db, contract)

    assert assets is not None, "现役四锚必须查询资产表才能得知 NAS 后端"
    for anchor in anchors:
        resolved = assets[anchor["asset_id"]]
        assert resolved.storage_backend == "nas_maps"
        assert resolved.source_uri == f"nas://maps/anchors/{anchor['level']}.png"
        assert resolved.stored_name == anchor["stored_name"]


def test_worker_rejects_frozen_candidate_prompt_binding_drift() -> None:
    contract = build_inspiration_v3_contract()
    frozen_candidate = {
        "candidate_revision_id": 91,
        "contract": contract,
    }
    strategy_bundle = SimpleNamespace(
        prompt_a_version=contract["prompt_bindings"]["call_a_version"],
        prompt_b_version="unexpected-old-b-version",
    )

    # 拦下时必须点名哪一路不一致并给出修复办法，而不是只丢一句「绑定不一致」。
    with pytest.raises(RuntimeError, match="unexpected-old-b-version.*不一致") as excinfo:
        worker.validate_candidate_strategy_prompt_bindings(
            frozen_candidate,
            strategy_bundle,
        )

    detail = str(excinfo.value)
    # 点名候选修订、两路各自的声明值与选择值，运营才知道改哪一个。
    assert "id=91" in detail
    assert contract["prompt_bindings"]["call_b_version"] in detail
    assert "unexpected-old-b-version" in detail
    # 调用A本来就一致，不能被一起标成问题。
    assert "调用A：" in detail and "调用B：" in detail
    assert "修复办法" in detail
    assert "不出分" in detail


# 合成的特有维度 key（仅测试用）：方案 A 的真实合同 specific_group 为空，为回归
# 权威路径「特有 grade 拿不齐 → fail-closed」分支，测试把这些 key 注入 bundle。
_SYNTHETIC_SPECIFIC_KEYS = ("synthetic_specific_a", "synthetic_specific_b")


class _FakeResponse:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed
        self.raw_text = "{}"
        self.raw_payload = {}


class _FakeClient:
    """A fake 调用B client for the specific-dimension shadow call.

    ``specific_grade`` fills every specific dimension with the same grade.
    ``raise_exc`` makes the call blow up (simulating a network/timeout failure);
    ``bad_payload`` returns a structurally-wrong payload so the parse fails.
    """

    def __init__(
        self,
        *,
        specific_grade: int = 5,
        raise_exc: bool = False,
        bad_payload: bool = False,
    ) -> None:
        self.specific_grade = specific_grade
        self.raise_exc = raise_exc
        self.bad_payload = bad_payload
        self.calls = 0

    async def chat_json(
        self, system_prompt: str, user_prompt: str, **_kwargs: Any
    ) -> _FakeResponse:
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("调用B network exploded")
        if self.bad_payload:
            return _FakeResponse({"unexpected": "shape"})
        if "hit_rules" in user_prompt:
            configs = build_inspiration_subcategory_dimensions()
            all_dimensions = []
            seen: set[str] = set()
            for config in configs.values():
                for dimension in config["common_group"]["schema_definition"]["dimensions"]:
                    if dimension["key"] in user_prompt and dimension["key"] not in seen:
                        seen.add(dimension["key"])
                        hits = []
                        if self.specific_grade == 1:
                            hits = [
                                {
                                    "rule_id": rule["rule_id"],
                                    "confidence": "high",
                                    "evidence": f"{dimension['label']}命中{rule['rule_id']}",
                                }
                                for rule in dimension["deduction_rules"]
                            ]
                        all_dimensions.append(
                            {"dimension_key": dimension["key"], "hit_rules": hits}
                        )
            return _FakeResponse(
                {
                    "aesthetic_score": 88,
                    "aesthetic_evidence": ["主体结构、材质和光影均有可见证据"],
                    "aesthetic_confidence": 0.9,
                    "dimensions": all_dimensions,
                    "overall_note": "",
                }
            )
        # Grade every key the prompt mentions.  The prompt lists the specific
        # dimension keys verbatim, so scan the user prompt for known keys.  方案 A
        # 后 inspiration_image 的 specific_group 已置空，此路径只由注入了合成特有维度
        # 的 bundle（见 _bundle_with_specific）触发，用于回归特有 grade fail-closed 分支。
        dims: dict[str, dict[str, int]] = {}
        for key in _SYNTHETIC_SPECIFIC_KEYS:
            if key in user_prompt:
                dims[key] = {"grade": self.specific_grade}
        return _FakeResponse(
            {
                "aesthetic_score": 88,
                "overall_evidence": ["主体结构、材质和光影均有可见证据"],
                "confidence": 0.9,
                "dimensions": {
                    key: {
                        "grade": value["grade"],
                        "evidence": [f"{key}有可见表现"],
                    }
                    for key, value in dims.items()
                },
            }
        )


def _aesthetic(common_grade: int) -> dict[str, Any]:
    """Aesthetic payload with the unified foundation and legacy grade fields."""
    return {
        "aesthetic_score": 88,
        "overall_evidence": ["主体结构、材质和光影均有可见证据"],
        "confidence": 0.9,
        "dimensions": {
            key: {
                "grade": common_grade,
                "evidence": [f"{key}有可见表现"],
            }
            for key in _COMMON_KEYS
        },
    }


def _redline_precheck() -> dict[str, Any]:
    """Precheck that trips the '是截图' redline (needs no grades)."""
    return {
        "decisive_signal_validation": {"status": "valid", "reasons": []},
        "classification": {"scope_status": "in_scope", "primary_confidence": 0.9},
        "production_fields": {"reason": ["是截图"]},
    }


def _class_one_precheck(confidence: float = 0.95) -> dict[str, Any]:
    """Precheck resolving to class_one (建筑设计 → class_one), real_photo, no defects."""
    return {
        "decisive_signal_validation": {"status": "valid", "reasons": []},
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": confidence,
            "primary_category": "建筑设计",
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }


def _class_two_precheck(confidence: float = 0.95) -> dict[str, Any]:
    """Precheck resolving to class_two (产品设计 → class_two), real_photo, no defects.

    class_two has base_score=20 / dimension_max=60 / track_cap=80, so all-grade-5
    lands at 80 → L1 and all-grade-1 lands at 20 → L4 with a real_photo (0)
    media penalty — a clean high/low direction spread without media trickery.
    """
    return {
        "decisive_signal_validation": {"status": "valid", "reasons": []},
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": confidence,
            "primary_category": "产品设计",
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }


def _custom_redline_bundle() -> dict[str, Any]:
    contract = build_inspiration_v3_contract()
    contract["redline_policy"]["rules"] = [
        {
            "key": "transparent_checkerboard",
            "signal": "production_fields.reason",
            "match_any": ["透明棋盘格"],
            "exemptions": [],
            "enabled": True,
        }
    ]
    return {
        "contract": contract,
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
    }


def _custom_redline_precheck(*, evidence: list[str]) -> dict[str, Any]:
    redline_reasons = (
        []
        if evidence
        else ["missing_evidence:redline:transparent_checkerboard"]
    )
    return {
        "redline_triggered": {"transparent_checkerboard": True},
        "decisive_evidence": {
            "redline_triggered": {"transparent_checkerboard": evidence},
        },
        "redline_signal_validation": {
            "status": "valid" if evidence else "needs_review",
            "reasons": redline_reasons,
        },
        "non_redline_signal_validation": {"status": "valid", "reasons": []},
        "decisive_signal_validation": {
            "status": "valid" if evidence else "needs_review",
            "reasons": redline_reasons,
        },
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": 0.9,
            "primary_category": "建筑设计",
        },
        "production_fields": {
            "reason": ["透明棋盘格"],
            "trait": "实景照片",
        },
    }


def test_redline_prefilter_confirms_contract_rule_only_with_hit_evidence() -> None:
    decision = evaluate_v3_redline_prefilter(
        _custom_redline_bundle(),
        _custom_redline_precheck(evidence=["主体外区域显示透明棋盘格"]),
    )

    assert decision["hit"] is True
    assert decision["hit_rules"] == ["transparent_checkerboard"]
    assert decision["raw_hit"] is True
    assert decision["unconfirmed_hit_rules"] == []


def test_unconfirmed_redline_is_removed_only_from_scoring_copy() -> None:
    bundle = _custom_redline_bundle()
    precheck = _custom_redline_precheck(evidence=[])
    decision = evaluate_v3_redline_prefilter(bundle, precheck)

    assert decision["hit"] is False
    assert decision["raw_hit"] is True
    assert decision["unconfirmed_hit_rules"] == ["transparent_checkerboard"]

    scoring_precheck = precheck_for_v3_scoring(
        precheck,
        v3_bundle=bundle,
        redline_prefilter=decision,
    )
    assert scoring_precheck["production_fields"]["reason"] == []
    assert scoring_precheck["decisive_signal_validation"] == {
        "status": "valid",
        "reasons": [],
    }
    assert precheck["production_fields"]["reason"] == ["透明棋盘格"]


def test_mixed_redline_evidence_keeps_only_confirmed_rules_for_scoring() -> None:
    bundle = _custom_redline_bundle()
    bundle["contract"]["redline_policy"]["rules"].append(
        {
            "key": "hand_drawn_draft",
            "signal": "production_fields.reason",
            "match_any": ["手绘草稿"],
            "exemptions": [],
            "enabled": True,
        }
    )
    precheck = _custom_redline_precheck(
        evidence=["主体外区域显示透明棋盘格"]
    )
    precheck["redline_triggered"]["hand_drawn_draft"] = True
    precheck["decisive_evidence"]["redline_triggered"]["hand_drawn_draft"] = []
    precheck["production_fields"]["reason"].append("手绘草稿")
    precheck["redline_signal_validation"] = {
        "status": "needs_review",
        "reasons": ["missing_evidence:redline:hand_drawn_draft"],
    }
    precheck["decisive_signal_validation"] = dict(
        precheck["redline_signal_validation"]
    )

    decision = evaluate_v3_redline_prefilter(bundle, precheck)
    scoring_precheck = precheck_for_v3_scoring(
        precheck,
        v3_bundle=bundle,
        redline_prefilter=decision,
    )

    assert decision["hit"] is True
    assert decision["hit_rules"] == ["transparent_checkerboard"]
    assert decision["unconfirmed_hit_rules"] == ["hand_drawn_draft"]
    assert scoring_precheck["production_fields"]["reason"] == ["透明棋盘格"]
    assert scoring_precheck["decisive_signal_validation"] == {
        "status": "valid",
        "reasons": [],
    }
    assert precheck["production_fields"]["reason"] == ["透明棋盘格", "手绘草稿"]


def test_shared_reason_value_does_not_restore_unconfirmed_rule() -> None:
    bundle = _custom_redline_bundle()
    bundle["contract"]["redline_policy"]["rules"].append(
        {
            "key": "checkerboard_without_evidence",
            "signal": "production_fields.reason",
            "match_any": ["透明棋盘格"],
            "exemptions": [],
            "enabled": True,
        }
    )
    precheck = _custom_redline_precheck(
        evidence=["主体外区域显示透明棋盘格"]
    )
    precheck["redline_triggered"]["checkerboard_without_evidence"] = True
    precheck["decisive_evidence"]["redline_triggered"][
        "checkerboard_without_evidence"
    ] = []

    decision = evaluate_v3_redline_prefilter(bundle, precheck)
    scoring_precheck = precheck_for_v3_scoring(
        precheck,
        v3_bundle=bundle,
        redline_prefilter=decision,
    )
    scoring_bundle = v3_bundle_for_scoring(
        bundle,
        redline_prefilter=decision,
    )

    assert decision["hit_rules"] == ["transparent_checkerboard"]
    assert decision["unconfirmed_hit_rules"] == [
        "checkerboard_without_evidence"
    ]
    assert scoring_precheck["production_fields"]["reason"] == ["透明棋盘格"]
    scoring_rules = scoring_bundle["contract"]["redline_policy"]["rules"]
    assert scoring_rules[0]["enabled"] is True
    assert scoring_rules[1]["enabled"] is False
    assert bundle["contract"]["redline_policy"]["rules"][1]["enabled"] is True


# --------------------------------------------------------------------------- #
# 1. v3_authoritative_category — read-only routing gate
# --------------------------------------------------------------------------- #


def test_no_active_config_fails_closed(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, _CATEGORY_KEY)
        assert excinfo.value.code == "v3_active_config_missing"


def test_non_active_config_fails_closed(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key=_CATEGORY_KEY,
                display_name="灵感图 v3",
                status="draft",
                contract_json=json.dumps(build_inspiration_v3_contract()),
                classification_map_json=json.dumps(
                    build_inspiration_classification_map()
                ),
                subcategory_dimensions_json=json.dumps(
                    build_inspiration_subcategory_dimensions()
                ),
            )
        )
        db.commit()
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, _CATEGORY_KEY)
        assert excinfo.value.code == "v3_active_config_missing"


def test_active_config_returns_bundle(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        revision = _seed_active_config(db)
        bundle = v3_authoritative_category(db, _CATEGORY_KEY)
    assert bundle is not None
    assert bundle["config_revision"] == revision
    assert bundle["contract"]["category_key"] == _CATEGORY_KEY
    assert "class_one" in {
        t["key"] for t in bundle["contract"]["track_classification"]["tracks"]
    }
    assert isinstance(bundle["classification_map"], dict)
    assert isinstance(bundle["subcategory_dimensions"], dict)


def test_corrupt_config_json_fails_closed(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key=_CATEGORY_KEY,
                display_name="灵感图 v3",
                status="active",
                contract_json="{not valid json",
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
            )
        )
        db.commit()
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, _CATEGORY_KEY)
        assert excinfo.value.code == "v3_active_config_invalid"


def test_empty_json_blocks_fail_closed(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key=_CATEGORY_KEY,
                display_name="灵感图 v3",
                status="active",
                contract_json="{}",
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
            )
        )
        db.commit()
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, _CATEGORY_KEY)
        assert excinfo.value.code == "v3_active_config_invalid"


@pytest.mark.parametrize("bad_key", [None, "", 123])
def test_invalid_category_key_fails_closed(
    sessions: sessionmaker[Session], bad_key: Any
) -> None:
    with sessions() as db:
        _seed_active_config(db)
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, bad_key)
        assert excinfo.value.code == "v3_category_key_invalid"


# --------------------------------------------------------------------------- #
# 2. evaluate_v3_authoritative — authoritative scoring path
# --------------------------------------------------------------------------- #


def _bundle(db: Session) -> dict:
    _seed_active_config(db)
    bundle = v3_authoritative_category(db, _CATEGORY_KEY)
    assert bundle is not None
    # 本文件回归旧grade/rule-deduction兼容路径；新美感前置路径由
    # test_inspiration_aesthetic_foundation.py独立覆盖。
    bundle["contract"].pop("aesthetic_foundation", None)
    bundle["contract"]["level_thresholds"] = [
        {"min_score": 81, "level": "L1"},
        {"min_score": 61, "level": "L2"},
        {"min_score": 41, "level": "L3"},
        {"min_score": 21, "level": "L4"},
        {"min_score": 0, "level": "L5"},
    ]
    return bundle


def _legacy_bundle(db: Session) -> dict:
    """Freeze a pre-migration grade_points-only contract for fallback tests."""
    bundle = _bundle(db)
    for config in bundle["subcategory_dimensions"].values():
        for group_name in ("common_group", "specific_group"):
            group = config.get(group_name)
            if not isinstance(group, dict):
                continue
            for dimension in group["schema_definition"].get("dimensions", []):
                dimension.pop("deduction_rules", None)
    return bundle


def _bonus_cap_bundle(db: Session) -> dict:
    bundle = _bundle(db)
    class_one = bundle["subcategory_dimensions"]["class_one"]
    dimensions = class_one["common_group"]["schema_definition"]["dimensions"]
    for dimension in dimensions:
        dimension["dimension_score_cap"] = 80
        dimension["deduction_rules"] = []
        dimension["bonus_rules"] = [
            {
                "rule_id": f"{dimension['key']}_strength",
                "description": f"{dimension['label']}表现清晰完整",
                "bonus": 5,
                "tags": ["优势"],
            }
        ]
    return bundle


def _bundle_with_specific(db: Session) -> dict:
    """A bundle whose class_one track carries a synthetic non-empty specific group.

    方案 A 的真实合同 specific_group 为空，所以特有维度调用B（``fetch_v3_specific_grades``）
    在正常路径永不触发。这个 helper 往 class_one 注入两个合成特有维度，且把 common_group
    的 group_weight 与 specific 平分，专门用来回归「特有 grade 拿不齐 → V3AuthoritativeError」
    的 fail-closed 分支——不改任何引擎核心，只在测试侧构造带特有组的 config。
    """
    bundle = _legacy_bundle(db)
    class_one = bundle["subcategory_dimensions"]["class_one"]
    class_one["common_group"]["group_weight"] = 0.6
    class_one["specific_group"] = {
        "group_weight": 0.4,
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": "inspiration_specific",
            "version": "v1",
            "dimensions": [
                {
                    "key": key,
                    "label": key,
                    "weight": 0.5,
                    "grade_points": {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0},
                }
                for key in _SYNTHETIC_SPECIFIC_KEYS
            ],
        },
    }
    return bundle



def test_rev4_invalid_decisive_precheck_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    precheck = _class_one_precheck()
    precheck["decisive_signal_validation"] = {
        "status": "needs_review",
        "reasons": ["missing:image_defects"],
    }
    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=precheck,
                aesthetic=_aesthetic(common_grade=5),
            )
        )
    assert excinfo.value.code == "decisive_precheck_invalid"

def test_redline_hit_hard_reject(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    # Redline needs no grades → no model call; a client that would explode is fine.
    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(raise_exc=True),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_redline_precheck(),
            aesthetic=None,
        )
    )
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["score"] <= 49
    assert result["level_semantics_version"] == "doc-l5-worst-v1"


def test_normal_in_scope_produces_score(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=4),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_one_precheck(),
            aesthetic=_aesthetic(common_grade=4),
        )
    )
    assert result["hard_reject"] is False
    assert isinstance(result["score"], int) and 0 <= result["score"] <= 100
    assert result["level"] in {"L1", "L2", "L3", "L4", "L5"}
    assert result["track_key"] == "class_one"
    assert result["level_semantics_version"] == "doc-l5-worst-v1"
    assert result["dimension_scoring_mode"] == "rule_deduction"
    identity = result["dimension_deduction_output"]["prompt_identity"]
    assert identity["template_version"] == "dimension-deduction-prompt-v1"
    assert len(identity["system_sha256"]) == 64
    assert len(identity["user_sha256"]) == 64
    scoring = build_v3_authoritative_scoring(
        result, precheck=_class_one_precheck()
    )
    assert scoring["dimension_deduction_output"]["prompt_identity"] == identity
    assert scoring["_dimension_deduction_raw_payload"]["prompt_identity"] == identity
    assert "provider_payload" in scoring["_dimension_deduction_raw_payload"]


def test_bonus_cap_provider_failure_fails_closed_without_foundation(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        bundle = _bonus_cap_bundle(db)
    precheck = _class_one_precheck()
    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(raise_exc=True),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=precheck,
                aesthetic=None,
            )
        )
    # 桥接层现在更早 fail-closed（call_b_unavailable）：调用B失败不出分，
    # 不再等 fallback 输出流到后续校验才拒。
    assert excinfo.value.code == "call_b_unavailable"
    # provider 故障的可重试语义必须透传到 worker 抛出的异常上。
    assert getattr(excinfo.value, "retryable", None) is True
    assert getattr(excinfo.value, "technical_error_type", None) == "network"
    scoring = build_v3_authoritative_error_scoring(excinfo.value)
    assert scoring["score"] is None
    assert scoring["level"] is None
    assert scoring["needs_review"] is True
    assert scoring["interpretation_status"] == "manual_required"


def test_common_grade_unavailable_raises(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _legacy_bundle(db)
    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(specific_grade=4),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=_class_one_precheck(),
                aesthetic=None,  # no common grades → cannot map, must fail closed
            )
        )
    assert excinfo.value.code == "common_grade_unavailable"


def test_specific_call_exception_raises(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle_with_specific(db)
    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(raise_exc=True),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=_class_one_precheck(),
                aesthetic=_aesthetic(common_grade=4),
            )
        )
    assert excinfo.value.code == "specific_grade_unavailable"


def test_specific_call_bad_payload_raises(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle_with_specific(db)
    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(bad_payload=True),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=_class_one_precheck(),
                aesthetic=_aesthetic(common_grade=4),
            )
        )
    assert excinfo.value.code == "specific_grade_unavailable"


# --------------------------------------------------------------------------- #
# 3. score direction: 高质量 → 高 score + 低 L; 低质量 → 低 score + 高 L
# --------------------------------------------------------------------------- #


def test_score_direction_high_vs_low(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)

    high = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=5),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_two_precheck(),
            aesthetic=_aesthetic(common_grade=5),
        )
    )
    low = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=1),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_two_precheck(),
            aesthetic=_aesthetic(common_grade=1),
        )
    )

    # score 越高越好：高质量分数严格高于低质量。
    assert high["score"] > low["score"]
    # doc-l5-worst：越好 → L 号越小。高质量 L 号 <= 低质量 L 号，且高质量落 L1/L2、
    # 低质量落 L4/L5，两个方向并存不矛盾。
    assert high["level"] in {"L1", "L2"}
    assert low["level"] in {"L4", "L5"}
    assert int(high["level"][1]) < int(low["level"][1])


# --------------------------------------------------------------------------- #
# 4. scoring-dict mappers (worker-facing)
# --------------------------------------------------------------------------- #


def test_build_scoring_maps_score_and_level(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    precheck = _class_one_precheck(confidence=0.88)
    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=5),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=precheck,
            aesthetic=_aesthetic(common_grade=5),
        )
    )
    scoring = build_v3_authoritative_scoring(result, precheck=precheck)
    assert scoring["scoring_mode"] == "v3_authoritative"
    assert scoring["formal"] is True
    assert scoring["score"] == result["score"]
    assert scoring["level"] == result["level"]
    assert scoring["confidence"] == 0.88
    assert scoring["level_semantics_version"] == "doc-l5-worst-v1"
    assert scoring["track_key"] == "class_one"
    json.dumps(scoring, ensure_ascii=False)  # must be JSON-serializable


def test_redline_scoring_needs_review(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    precheck = _redline_precheck()
    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=precheck,
            aesthetic=None,
        )
    )
    scoring = build_v3_authoritative_scoring(result, precheck=precheck)
    assert scoring["needs_review"] is True
    assert scoring["level"] == "L5"
    assert scoring["hard_reject"] is True


def test_build_error_scoring_no_downgrade() -> None:
    exc = V3AuthoritativeError("common_grade_unavailable", "共性 grade 缺失")
    scoring = build_v3_authoritative_error_scoring(exc)
    assert scoring["score"] is None
    assert scoring["level"] is None
    assert scoring["needs_review"] is True
    assert scoring["scoring_mode"] == "v3_authoritative_failed"
    assert scoring["v3_error_code"] == "common_grade_unavailable"
    # fail-closed 标 v3 语义，不冒充 v1。
    assert scoring["level_semantics_version"] == "doc-l5-worst-v1"
    json.dumps(scoring, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 5. 非侵入证明（最重要）：老类目（space_image，无 v3 config）逐字节不变
# --------------------------------------------------------------------------- #

# 老类目 v1 scoring 的固定输入（复用 test_scoring 的形状），用于比对关键字段。
_V1_DIMENSIONS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)


def _v1_precheck() -> dict[str, Any]:
    return {
        "classification": {"scope_status": "in_scope", "primary_confidence": 0.95},
        "image_quality": {"quality_severity": "good", "confidence": 0.95, "evidence": []},
        "media_form": {},
        "needs_review": False,
        "review_reasons": [],
    }


def _v1_aesthetic(grade: int = 4) -> dict[str, Any]:
    return {
        "dimensions": {key: {"grade": grade} for key in _V1_DIMENSIONS},
        "special_checks": {},
        "assessment_confidence": 0.9,
        "needs_review": False,
        "review_reasons": [],
    }


def test_space_image_no_v3_config_routes_to_none(
    sessions: sessionmaker[Session],
) -> None:
    """A space_image job (a v1-only category) never has a v3 config → the routing
    gate returns None even when an unrelated inspiration_image config is active."""
    with sessions() as db:
        _seed_active_config(db, category_key=_CATEGORY_KEY)  # 不同类目的 active config
        for category_key in ("space_image", "material_image", "pdf_text"):
            with pytest.raises(V3AuthoritativeError) as excinfo:
                v3_authoritative_category(db, category_key)
            assert excinfo.value.code == "v3_active_config_missing"


def test_old_category_calculate_score_byte_identical(
    sessions: sessionmaker[Session],
) -> None:
    """The old v1 calculate_score path is byte-for-byte identical whether or not
    the v3 authoritative module is present/imported and whether or not an
    inspiration_image v3 config is active — because the gate returns None for
    space_image, the old scoring is never touched."""
    from app.scoring import calculate_score

    precheck = _v1_precheck()
    aesthetic = _v1_aesthetic(4)

    # Baseline: pure v1 scoring, no v3 involvement at all.
    baseline = calculate_score(precheck, aesthetic)

    # With an active inspiration_image v3 config present in the DB and the v3
    # module imported, a space_image job still resolves to None → the exact same
    # scoring dict.  (Comparing canonical JSON proves byte-identity.)
    with sessions() as db:
        _seed_active_config(db, category_key=_CATEGORY_KEY)
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, "space_image")
        assert excinfo.value.code == "v3_active_config_missing"
    with_v3_present = calculate_score(_v1_precheck(), _v1_aesthetic(4))

    assert json.dumps(with_v3_present, ensure_ascii=False, sort_keys=True) == json.dumps(
        baseline, ensure_ascii=False, sort_keys=True
    )
    # Spot-check the load-bearing authoritative fields explicitly.
    assert with_v3_present["score"] == baseline["score"]
    assert with_v3_present["level"] == baseline["level"]
    assert with_v3_present["engine_version"] == baseline["engine_version"]


def test_routing_gate_issues_no_writes(sessions: sessionmaker[Session]) -> None:
    """The routing gate performs only SELECTs — the config table is unchanged."""
    with sessions() as db:
        _seed_active_config(db)
        before = db.scalars(select(CategoryEvaluationV3Config)).all()
        count_before = len(before)
        rev_before = before[0].revision

        for _ in range(3):
            v3_authoritative_category(db, _CATEGORY_KEY)
            with pytest.raises(V3AuthoritativeError):
                v3_authoritative_category(db, "space_image")

        after = db.scalars(select(CategoryEvaluationV3Config)).all()
        assert len(after) == count_before
        assert after[0].revision == rev_before


def test_missing_active_config_fails_closed_instead_of_returning_v1_route(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, "space_image")
    assert excinfo.value.code == "v3_active_config_missing"
    assert "缺少 active v3 合同" in str(excinfo.value)


def test_corrupt_active_config_fails_closed_instead_of_returning_v1_route(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        db.add(
            CategoryEvaluationV3Config(
                category_key="space_image",
                display_name="空间图片",
                status="active",
                contract_json="{broken",
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
            )
        )
        db.commit()
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_category(db, "space_image")
    assert excinfo.value.code == "v3_active_config_invalid"
    assert "active v3 合同无效" in str(excinfo.value)


def test_frozen_bundle_with_mismatched_category_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    frozen = {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 3,
    }
    job = SimpleNamespace(
        category_key="space_image",
        baseline_regression_item_id=None,
        category_profile_snapshot_json=json.dumps(
            {"v3_authoritative_bundle": frozen}, ensure_ascii=False
        ),
    )
    with sessions() as db:
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_for_job(db, job)
    assert excinfo.value.code == "v3_frozen_config_invalid"
    assert "类目不匹配" in str(excinfo.value)


def _valid_frozen_bundle() -> dict[str, Any]:
    return {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 3,
    }


def _assert_invalid_frozen_bundle(
    sessions: sessionmaker[Session], frozen: dict[str, Any]
) -> V3AuthoritativeError:
    job = SimpleNamespace(
        category_key=_CATEGORY_KEY,
        baseline_regression_item_id=None,
        category_profile_snapshot_json=json.dumps(
            {"v3_authoritative_bundle": frozen}, ensure_ascii=False
        ),
    )
    with sessions() as db:
        with pytest.raises(V3AuthoritativeError) as excinfo:
            v3_authoritative_for_job(db, job)
    assert excinfo.value.code == "v3_frozen_config_invalid"
    return excinfo.value


def test_frozen_bundle_with_empty_contract_tracks_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    frozen = _valid_frozen_bundle()
    frozen["contract"]["track_classification"]["tracks"] = []

    error = _assert_invalid_frozen_bundle(sessions, frozen)

    assert str(error) == "基线作业的冻结 v3 配置无效"


@pytest.mark.parametrize("corruption", ["invalid_structure", "unknown_target"])
def test_frozen_bundle_with_invalid_classification_map_fails_closed(
    sessions: sessionmaker[Session], corruption: str
) -> None:
    frozen = _valid_frozen_bundle()
    if corruption == "invalid_structure":
        frozen["classification_map"]["category_to_subcategory"] = []
    else:
        frozen["classification_map"]["category_to_subcategory"][
            "建筑设计"
        ] = "payload-must-not-leak"

    error = _assert_invalid_frozen_bundle(sessions, frozen)

    assert str(error) == "基线作业的冻结 v3 配置无效"
    assert "payload-must-not-leak" not in str(error)


@pytest.mark.parametrize(
    "corruption",
    ["invalid_structure", "missing_track", "incomplete_dimension_reference"],
)
def test_frozen_bundle_with_invalid_subcategory_dimensions_fails_closed(
    sessions: sessionmaker[Session], corruption: str
) -> None:
    frozen = _valid_frozen_bundle()
    dimensions = frozen["subcategory_dimensions"]
    if corruption == "invalid_structure":
        dimensions["class_one"]["format_version"] = "broken"
    elif corruption == "missing_track":
        dimensions.pop("class_three")
    else:
        dimensions["class_one"]["common_group"]["schema_definition"][
            "dimensions"
        ][0].pop("key")

    error = _assert_invalid_frozen_bundle(sessions, frozen)

    assert str(error) == "基线作业的冻结 v3 配置无效"


# --------------------------------------------------------------------------- #
# 运营手选调用 B：偏离合同绑定时接管正文，合同原生配对不受影响
# --------------------------------------------------------------------------- #


def _operator_b(version: str, *, takeover: bool) -> SimpleNamespace:
    return SimpleNamespace(
        stage="B",
        version=version,
        system_prompt="你是资深灵感图审美评估专家。",
        user_prompt=(
            "请逐条核验：\n{{dimension_rules}}" if takeover else "八维评分并给出等级"
        ),
    )


def test_contract_bound_b_version_still_runs_its_own_body(
    sessions: sessionmaker[Session],
) -> None:
    """手选B即使等于合同绑定，也要跑它自己的正文并归因到它。

    合同正文是从维度 schema 机器生成的，不是任何 Prompt 版本的正式执行体，所以
    「版本等于合同绑定就改跑合同正文」同样是拿别的提示词冒名顶替。何况候选回归
    启动时会把合同 prompt_bindings 改写成实际执行版本，此处一比较就永远相等，
    偏离将彻底隐形。
    """
    with sessions() as db:
        bundle = _bundle(db)
    bound = bundle["contract"]["prompt_bindings"]["call_b_version"]
    operator = _operator_b(bound, takeover=False)

    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=4),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_one_precheck(),
            aesthetic=_aesthetic(common_grade=4),
            operator_prompt_b=operator,
        )
    )

    output = result["dimension_deduction_output"]
    assert output["warning"] is None
    assert output["prompt_identity"]["bypassed_operator_prompt_version"] is None
    assert output["prompt_identity"]["operator_prompt_version"] == operator.version


def test_deviating_operator_b_takes_over_rule_prompt(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        bundle = _bundle(db)
    operator = _operator_b("insp-b-v6-levels-20260821", takeover=True)

    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=4),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_one_precheck(),
            aesthetic=_aesthetic(common_grade=4),
            operator_prompt_b=operator,
        )
    )

    identity = result["dimension_deduction_output"]["prompt_identity"]
    assert identity["operator_prompt_version"] == operator.version
    assert identity["template_version"] == (
        "dimension-deduction-prompt-v3-operator-selected"
    )
    assert result["dimension_deduction_output"]["warning"] is None


def test_deviating_operator_b_without_placeholder_runs_and_is_credited(
    sessions: sessionmaker[Session],
) -> None:
    """没写占位符的手选版本照样如实执行，并如实归因到该版本。

    运营手选的版本大多没有占位符；拒单会让机制吃不下运营的正常调整，而拿合同
    正文冒名顶替则是必须禁止的静默降级。正确行为是执行运营正文、服务端补齐规则。
    """
    with sessions() as db:
        bundle = _bundle(db)
    operator = _operator_b("insp-b-v6-levels-20260821", takeover=False)

    result = asyncio.run(
        evaluate_v3_authoritative(
            _FakeClient(specific_grade=4),
            "img.jpg",
            "image/jpeg",
            v3_bundle=bundle,
            precheck=_class_one_precheck(),
            aesthetic=_aesthetic(common_grade=4),
            operator_prompt_b=operator,
        )
    )

    identity = result["dimension_deduction_output"]["prompt_identity"]
    assert identity["operator_prompt_version"] == operator.version
    assert identity["bypassed_operator_prompt_version"] is None
    assert result["dimension_deduction_output"]["warning"] is None

    # 正常出分，不再被隔离成人工复核。
    scoring = build_v3_authoritative_scoring(result, precheck=_class_one_precheck())
    assert scoring["score"] is not None
    assert scoring["level"] is not None


def test_operator_b_with_empty_body_fails_closed_with_actionable_reason(
    sessions: sessionmaker[Session],
) -> None:
    """手选版本没有正文时才拒单，且必须给出运营能照着改的原因。"""
    with sessions() as db:
        bundle = _bundle(db)
    operator = _operator_b("insp-b-v6-levels-20260821", takeover=False)
    # 两处都空才算没有可执行内容：只空一处属于正常形状，服务端会补齐另一处。
    operator.user_prompt = "   "
    operator.system_prompt = "  "

    with pytest.raises(V3AuthoritativeError) as excinfo:
        asyncio.run(
            evaluate_v3_authoritative(
                _FakeClient(specific_grade=4),
                "img.jpg",
                "image/jpeg",
                v3_bundle=bundle,
                precheck=_class_one_precheck(),
                aesthetic=_aesthetic(common_grade=4),
                operator_prompt_b=operator,
            )
        )

    assert excinfo.value.code == "operator_prompt_body_empty"
    detail = str(excinfo.value)
    assert operator.version in detail
    assert "修复办法" in detail
    assert "不出分" in detail

    # 运营在回归明细里读到的就是这段文字，必须保留完整可行动原因。
    scoring = build_v3_authoritative_error_scoring(excinfo.value)
    assert scoring["score"] is None
    assert scoring["level"] is None
    assert scoring["needs_review"] is True
    assert any("修复办法" in reason for reason in scoring["review_reasons"])
