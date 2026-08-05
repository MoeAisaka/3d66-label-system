from __future__ import annotations

from pathlib import Path

from app.prompt_loader import load_prompt_pairs, load_standalone_prompt


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_v21_prompt_is_split_into_two_complete_calls() -> None:
    pairs = load_prompt_pairs(PROJECT_ROOT / "prompts" / "3d66-aesthetic-v2.1.md")
    assert set(pairs) == {"A", "B"}
    assert len(pairs["A"].system) > 2000
    assert len(pairs["B"].system) > 2000
    assert "{{image_metadata}}" in pairs["A"].user
    assert "{{precheck_json}}" in pairs["B"].user
    assert "{{rubric_version}}" in pairs["B"].user


def test_v13_split_prompts_match_runtime_contract() -> None:
    prompt_a = load_standalone_prompt(
        PROJECT_ROOT / "prompts" / "space-precheck-v1.3-split.1.md"
    )
    prompt_b = load_standalone_prompt(
        PROJECT_ROOT / "prompts" / "space-aesthetic-dimensions-v1.3-split.1.md"
    )
    assert len(prompt_a.system) > 4000
    assert "scope_status" in prompt_a.system
    assert "{{image_metadata}}" in prompt_a.user
    assert len(prompt_b.system) > 5000
    assert '"scoring_profile": "space_aesthetic_v1.3"' in prompt_b.system
    assert "{{precheck_json}}" in prompt_b.user
    assert "{{rubric_version}}" in prompt_b.user


def test_v13_split2_calibration_has_strict_high_grade_gates() -> None:
    prompt_a_calibration = (
        PROJECT_ROOT / "prompts" / "space-precheck-v1.3-split.2-calibration.md"
    ).read_text(encoding="utf-8")
    prompt_b_calibration = (
        PROJECT_ROOT / "prompts" / "space-aesthetic-v1.3-split.2-calibration.md"
    ).read_text(encoding="utf-8")
    assert "professional_photography=yes" in prompt_a_calibration
    assert "documentary_record.status=yes" in prompt_a_calibration
    assert "以3级为默认基准" in prompt_b_calibration
    assert "每个4级维度必须" in prompt_b_calibration
    assert "至少五个维度为5级" in prompt_b_calibration


def test_v13_split3_calibration_catches_quality_and_grade_collapse() -> None:
    prompt_a_calibration = (
        PROJECT_ROOT / "prompts" / "space-precheck-v1.3-split.3-calibration.md"
    ).read_text(encoding="utf-8")
    prompt_b_calibration = (
        PROJECT_ROOT / "prompts" / "space-aesthetic-v1.3-split.3-calibration.md"
    ).read_text(encoding="utf-8")
    assert "效果图、AI图和商品合成图" in prompt_a_calibration
    assert "画质正常”必须全部通过" in prompt_a_calibration
    assert "八个维度全部相同视为无效初稿" in prompt_b_calibration
    assert "至少形成两个有证据支持的等级档位" in prompt_b_calibration


def test_v14_lite_prompts_are_compact_and_keep_runtime_contract() -> None:
    prompt_a = load_standalone_prompt(
        PROJECT_ROOT / "prompts" / "space-precheck-v1.4-lite.1.md"
    )
    prompt_b = load_standalone_prompt(
        PROJECT_ROOT / "prompts" / "space-aesthetic-v1.4-lite.1.md"
    )
    assert 2000 < len(prompt_a.system) < 4000
    assert 2000 < len(prompt_b.system) < 4500
    assert "professional_photography" in prompt_a.system
    assert "documentary_record" in prompt_a.system
    assert "{{image_metadata}}" in prompt_a.user
    assert '"scoring_profile": "space_aesthetic_v1.3"' in prompt_b.system
    assert "{{precheck_json}}" in prompt_b.user
    assert "{{rubric_version}}" in prompt_b.user


def test_v14_lite2_calibration_caps_snapshot_and_damaged_quality_at_l2() -> None:
    calibration = (
        PROJECT_ROOT / "prompts" / "space-aesthetic-v1.4-lite.2-calibration.md"
    ).read_text(encoding="utf-8")
    assert "casual_snapshot.status=yes" in calibration
    assert "slight|moderate|severe|unusable" in calibration
    assert "最高为 `L2`" in calibration


def test_inspiration_b_human_calibrated_prompt_contains_frozen_contract() -> None:
    prompt = (PROJECT_ROOT / "prompts" / "inspiration_image_call_b.txt").read_text(
        encoding="utf-8"
    )
    assert "从业10年以上" in prompt
    assert "红点/IF/普利兹克" in prompt
    assert "截图" in prompt and "文字标注占画面≥40%" in prompt
    assert "视觉结构(权重0.10)" in prompt
    assert "设计流行度(权重0.15)" in prompt
    assert "主题清晰(权重0.06)" in prompt
    assert "enabled=true,threshold=80,cap_to=79" in prompt
    assert "L1=81-100" in prompt and "L5=0-20" in prompt
    for field in (
        '"score"', '"grade"', '"title"', '"seotitle"', '"category"',
        '"style"', '"tags"', '"cons"', '"design"', '"reason"',
        '"image_defects"', '"trait"',
    ):
        assert field in prompt


def test_inspiration_a_human_calibrated_prompt_syncs_trait_and_hard_defects() -> None:
    prompt = (PROJECT_ROOT / "prompts" / "inspiration_image_call_a.txt").read_text(
        encoding="utf-8"
    )
    assert '"trait": "实景照片"' in prompt
    assert "比例严重失调" in prompt
    assert "主体被遮挡" in prompt
    for field in (
        '"reason"',
        '"image_defects"',
        '"decisive_evidence"',
        '"decision_status"',
        '"uncertain_fields"',
    ):
        assert field in prompt
    assert "未命中与不确定严格区分" in prompt
    assert "corner_small_watermark" in prompt
    assert "subject_obscuring_watermark" in prompt
    assert "large_area_watermark" in prompt

    rev3 = (PROJECT_ROOT / "prompts" / "inspiration_image_call_a_rev3.txt").read_text(
        encoding="utf-8"
    )
    assert "凡最终得分 ≥80 且命中任意硬伤者，一律压至 79 分" in rev3
    assert '"image_defects"' not in rev3
