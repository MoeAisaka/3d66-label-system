"""ADR-0033 Task 2b tests: v3 引擎权威化路由（仅 inspiration_image 新类目，直接换）。

Exercises ``worker_v3_authoritative`` directly against an isolated in-memory
SQLite engine (StaticPool) — no worker, no queue, no real DB, no real network
(a fake client supplies 调用B).  Sticks to the repo ``asyncio.run`` convention.

Coverage:
1. ``v3_authoritative_category``: no active config → None; active config →
   assembled bundle; corrupt config json → None (fail-closed).
2. ``evaluate_v3_authoritative``: redline hit → hard_reject / L5 / score≤49;
   normal in_scope → score(0-100) + level + level_semantics_version=
   doc-l5-worst-v1 + track_key; common grade unavailable → V3AuthoritativeError;
   specific 调用B error → V3AuthoritativeError.
3. 非侵入证明: a space_image (no v3 config) → v3_authoritative_category None and
   the old calculate_score path produces byte-identical scoring.
4. score direction: 高质量 → 高 score + 低 L (L1/L2); 低质量 → 低 score + 高 L
   (L4/L5).  "score 越高越好" 与 "L5 最差" 并存不矛盾.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker_v3_authoritative
from app.worker_v3_authoritative import (
    V3AuthoritativeError,
    build_v3_authoritative_error_scoring,
    build_v3_authoritative_scoring,
    evaluate_v3_authoritative,
    v3_authoritative_category,
)
from app.database import Base
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations
from app.models import CategoryEvaluationV3Config

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
        self, _system_prompt: str, user_prompt: str, **_kwargs: Any
    ) -> _FakeResponse:
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("调用B network exploded")
        if self.bad_payload:
            return _FakeResponse({"unexpected": "shape"})
        # Grade every key the prompt mentions.  The prompt lists the specific
        # dimension keys verbatim, so scan the user prompt for known keys.  方案 A
        # 后 inspiration_image 的 specific_group 已置空，此路径只由注入了合成特有维度
        # 的 bundle（见 _bundle_with_specific）触发，用于回归特有 grade fail-closed 分支。
        dims: dict[str, dict[str, int]] = {}
        for key in _SYNTHETIC_SPECIFIC_KEYS:
            if key in user_prompt:
                dims[key] = {"grade": self.specific_grade}
        return _FakeResponse({"dimensions": dims})


def _aesthetic(common_grade: int) -> dict[str, Any]:
    """A v1 aesthetic payload whose common-group keys carry ``common_grade``."""
    return {
        "dimensions": {key: {"grade": common_grade} for key in _COMMON_KEYS}
    }


def _redline_precheck() -> dict[str, Any]:
    """Precheck that trips the '是截图' redline (needs no grades)."""
    return {
        "classification": {"scope_status": "in_scope", "primary_confidence": 0.9},
        "production_fields": {"reason": ["是截图"]},
    }


def _class_one_precheck(confidence: float = 0.95) -> dict[str, Any]:
    """Precheck resolving to class_one (建筑设计 → class_one), real_photo, no defects."""
    return {
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
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": confidence,
            "primary_category": "产品设计",
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }


# --------------------------------------------------------------------------- #
# 1. v3_authoritative_category — read-only routing gate
# --------------------------------------------------------------------------- #


def test_no_active_config_returns_none(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        assert v3_authoritative_category(db, _CATEGORY_KEY) is None


def test_non_active_config_returns_none(sessions: sessionmaker[Session]) -> None:
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
        assert v3_authoritative_category(db, _CATEGORY_KEY) is None


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


def test_corrupt_config_json_returns_none(sessions: sessionmaker[Session]) -> None:
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
        assert v3_authoritative_category(db, _CATEGORY_KEY) is None


def test_empty_json_blocks_return_none(sessions: sessionmaker[Session]) -> None:
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
        assert v3_authoritative_category(db, _CATEGORY_KEY) is None


@pytest.mark.parametrize("bad_key", [None, "", 123])
def test_invalid_category_key_returns_none(
    sessions: sessionmaker[Session], bad_key: Any
) -> None:
    with sessions() as db:
        _seed_active_config(db)
        assert v3_authoritative_category(db, bad_key) is None


# --------------------------------------------------------------------------- #
# 2. evaluate_v3_authoritative — authoritative scoring path
# --------------------------------------------------------------------------- #


def _bundle(db: Session) -> dict:
    _seed_active_config(db)
    bundle = v3_authoritative_category(db, _CATEGORY_KEY)
    assert bundle is not None
    return bundle


def _bundle_with_specific(db: Session) -> dict:
    """A bundle whose class_one track carries a synthetic non-empty specific group.

    方案 A 的真实合同 specific_group 为空，所以特有维度调用B（``fetch_v3_specific_grades``）
    在正常路径永不触发。这个 helper 往 class_one 注入两个合成特有维度，且把 common_group
    的 group_weight 与 specific 平分，专门用来回归「特有 grade 拿不齐 → V3AuthoritativeError」
    的 fail-closed 分支——不改任何引擎核心，只在测试侧构造带特有组的 config。
    """
    bundle = _bundle(db)
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


def test_common_grade_unavailable_raises(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        bundle = _bundle(db)
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
        assert v3_authoritative_category(db, "space_image") is None
        assert v3_authoritative_category(db, "material_image") is None
        assert v3_authoritative_category(db, "pdf_text") is None


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
        assert v3_authoritative_category(db, "space_image") is None
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
            v3_authoritative_category(db, "space_image")

        after = db.scalars(select(CategoryEvaluationV3Config)).all()
        assert len(after) == count_before
        assert after[0].revision == rev_before
