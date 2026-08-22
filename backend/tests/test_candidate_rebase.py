from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.candidate_rebase import (
    CandidateRebaseError,
    nearest_common_ancestor,
    plan_candidate_rebase,
    rebase_candidate_artifacts,
)
from app.database import Base
from app.models import CategoryEvaluationV3Revision


def _artifacts(contract=None, classification_map=None, subcategory_dimensions=None):
    return {
        "contract": contract or {},
        "classification_map": classification_map or {},
        "subcategory_dimensions": subcategory_dimensions or {},
    }


def test_rebase_replays_candidate_edits_onto_active_without_dropping_active_work():
    """候选自己的改动要带过来，现役版本独有的新增不能丢。

    这是变基的核心价值：分叉候选之所以不能直接启用，就是因为重放它会静默
    丢弃现役版本引入的东西。
    """
    base = _artifacts(contract={
        "redline_policy": {"enabled": False, "hit_level": "L4"},
        "spec_version": "v3",
    })
    active = _artifacts(contract={
        "redline_policy": {"enabled": False, "hit_level": "L4"},
        "spec_version": "v4",
        # 现役独有：候选那条线上从未存在
        "b_aesthetic_foundation": {"source": "call_b", "score_range": [0, 100]},
    })
    candidate = _artifacts(contract={
        "redline_policy": {"enabled": True, "hit_level": "L3"},
        "spec_version": "v3",
    })

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert result["conflicts"] == []
    merged = result["artifacts"]["contract"]
    # 候选的改动被采纳
    assert merged["redline_policy"] == {"enabled": True, "hit_level": "L3"}
    # 现役独有的块完整保留
    assert merged["b_aesthetic_foundation"] == {
        "source": "call_b",
        "score_range": [0, 100],
    }
    # 候选没碰过的字段保持现役取值
    assert merged["spec_version"] == "v4"


def test_active_only_addition_is_not_a_conflict():
    """现役新增、候选未涉及的键必须原样保留，且不得报冲突。

    手工三方合并时最易踩的坑：把「祖先无、现役有、候选无」误判为双方各自
    新增，从而虚报冲突并阻塞变基。
    """
    base = _artifacts(contract={"keep": 1})
    active = _artifacts(contract={"keep": 1, "added_by_active": {"deep": True}})
    candidate = _artifacts(contract={"keep": 2})

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert result["conflicts"] == []
    assert result["artifacts"]["contract"]["added_by_active"] == {"deep": True}
    assert result["artifacts"]["contract"]["keep"] == 2


def test_both_sides_changed_same_value_is_reported_as_conflict():
    base = _artifacts(contract={"hit_score_cap": 40})
    active = _artifacts(contract={"hit_score_cap": 50})
    candidate = _artifacts(contract={"hit_score_cap": 60})

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert [c["path"] for c in result["conflicts"]] == ["contract.hit_score_cap"]
    # 冲突时保留现役取值，绝不擅自选边
    assert result["artifacts"]["contract"]["hit_score_cap"] == 50


def test_both_sides_added_same_key_differently_is_a_conflict():
    base = _artifacts(contract={})
    active = _artifacts(contract={"foundation": {"source": "call_b"}})
    candidate = _artifacts(contract={"foundation": {"source": "call_a"}})

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert [c["path"] for c in result["conflicts"]] == ["contract.foundation"]


def test_identical_independent_addition_is_not_a_conflict():
    base = _artifacts(contract={})
    active = _artifacts(contract={"foundation": {"source": "call_b"}})
    candidate = _artifacts(contract={"foundation": {"source": "call_b"}})

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert result["conflicts"] == []
    assert result["artifacts"]["contract"]["foundation"] == {"source": "call_b"}


def test_candidate_deletion_is_adopted_but_conflicts_when_active_changed_it():
    base = _artifacts(contract={"legacy": {"a": 1}, "other": 1})
    active = _artifacts(contract={"legacy": {"a": 1}, "other": 1})
    candidate = _artifacts(contract={"other": 1})

    clean = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)
    assert clean["conflicts"] == []
    assert "legacy" not in clean["artifacts"]["contract"]

    active_touched = _artifacts(contract={"legacy": {"a": 2}, "other": 1})
    conflicted = rebase_candidate_artifacts(
        base=base, active=active_touched, candidate=candidate
    )
    assert [c["path"] for c in conflicted["conflicts"]] == ["contract.legacy"]


def test_lists_are_merged_atomically_not_element_wise():
    """规则数组按整体取舍：逐元素合并会把两套顺序悄悄交织在一起。"""
    base = _artifacts(contract={"rules": [{"key": "a"}]})
    active = _artifacts(contract={"rules": [{"key": "a"}]})
    candidate = _artifacts(contract={"rules": [{"key": "a"}, {"key": "b"}]})

    result = rebase_candidate_artifacts(base=base, active=active, candidate=candidate)

    assert result["conflicts"] == []
    assert result["artifacts"]["contract"]["rules"] == [{"key": "a"}, {"key": "b"}]


def _revision(db, *, revision, status, parent_id, contract, category_key="model_3d_su"):
    row = CategoryEvaluationV3Revision(
        category_key=category_key,
        display_name=f"rev{revision}",
        revision=revision,
        status=status,
        parent_revision_id=parent_id,
        contract_json=json.dumps(contract, ensure_ascii=False),
        classification_map_json="{}",
        subcategory_dimensions_json="{}",
        dimension_deduction_rules_json="{}",
        media_penalty_enabled=False,
        contract_hash=f"{revision:064d}",
        created_by="test",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_plan_finds_nearest_common_ancestor_across_sibling_branches(db):
    """复现真实分叉：候选与现役是兄弟分支，共同祖先在更早的版本上。"""
    root = _revision(db, revision=5, status="retired", parent_id=None,
                     contract={"spec_version": "v3", "cap": 40})
    mid = _revision(db, revision=6, status="candidate", parent_id=root.id,
                    contract={"spec_version": "v3", "cap": 40, "extra": 1})
    candidate = _revision(db, revision=7, status="candidate", parent_id=mid.id,
                          contract={"spec_version": "v3", "cap": 60, "extra": 1})
    active = _revision(db, revision=8, status="active", parent_id=root.id,
                       contract={"spec_version": "v4", "cap": 40, "foundation": True})

    assert nearest_common_ancestor(db, candidate, active).id == root.id

    plan = plan_candidate_rebase(db, candidate=candidate, active=active)
    assert plan["needed"] is True
    assert plan["base_revision_id"] == root.id
    assert plan["conflicts"] == []
    merged = plan["artifacts"]["contract"]
    assert merged["cap"] == 60           # 候选改动
    assert merged["extra"] == 1          # 中间版本引入、候选继承
    assert merged["foundation"] is True  # 现役独有，保留
    assert merged["spec_version"] == "v4"


def test_plan_reports_no_rebase_needed_for_direct_child(db):
    active = _revision(db, revision=8, status="active", parent_id=None, contract={"a": 1})
    candidate = _revision(db, revision=9, status="candidate", parent_id=active.id,
                          contract={"a": 2})

    plan = plan_candidate_rebase(db, candidate=candidate, active=active)

    assert plan["needed"] is False
    assert plan["conflicts"] == []


def test_plan_rejects_non_candidate_and_foreign_category(db):
    active = _revision(db, revision=8, status="active", parent_id=None, contract={})
    retired = _revision(db, revision=6, status="retired", parent_id=None, contract={})
    foreign = _revision(db, revision=3, status="candidate", parent_id=None, contract={},
                        category_key="inspiration_image")

    with pytest.raises(CandidateRebaseError) as retired_error:
        plan_candidate_rebase(db, candidate=retired, active=active)
    assert retired_error.value.code == "candidate_status_conflict"

    with pytest.raises(CandidateRebaseError) as foreign_error:
        plan_candidate_rebase(db, candidate=foreign, active=active)
    assert foreign_error.value.code == "candidate_category_conflict"


def test_plan_rejects_candidate_without_shared_history(db):
    active = _revision(db, revision=8, status="active", parent_id=None, contract={})
    orphan = _revision(db, revision=4, status="candidate", parent_id=None, contract={})

    with pytest.raises(CandidateRebaseError) as exc_info:
        plan_candidate_rebase(db, candidate=orphan, active=active)
    assert exc_info.value.code == "no_common_ancestor"


