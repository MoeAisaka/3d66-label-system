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
