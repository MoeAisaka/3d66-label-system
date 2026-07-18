from __future__ import annotations

from pathlib import Path

from app.prompt_loader import load_prompt_pairs


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_v21_prompt_is_split_into_two_complete_calls() -> None:
    pairs = load_prompt_pairs(PROJECT_ROOT / "prompts" / "3d66-aesthetic-v2.1.md")
    assert set(pairs) == {"A", "B"}
    assert len(pairs["A"].system) > 2000
    assert len(pairs["B"].system) > 2000
    assert "{{image_metadata}}" in pairs["A"].user
    assert "{{precheck_json}}" in pairs["B"].user
    assert "{{rubric_version}}" in pairs["B"].user
