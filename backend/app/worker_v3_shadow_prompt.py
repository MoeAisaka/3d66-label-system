"""ADR-0033 Task 1b：v3 特有维度影子调用B 的 prompt 构造（纯函数、无 IO）。

非红线灵感图的 v3 特有维度（``spatial_originality`` / ``design_trendiness`` /
``product_form_language`` / ``artistic_expression`` / ``visual_impact`` 等）在
v1 的调用B（``aesthetic["dimensions"]``）里没有对应 key，所以 Task1 的影子路径
只能对含特有维度的赛道 skip。本模块给这些特有维度**单独**生成一路影子调用B 的
system + user prompt：要求模型**只**对给定的特有维度输出 1-5 档 grade + evidence，
严格 JSON、不含任何其它字段。

设计约束：
- **纯函数**：给定同一组维度定义返回同一对 prompt，无 IO / 网络 / DB / 随机。
- **档位语义与 v1 一致**：``grade`` 是**维度级**分档，``5`` 表示该维度表现最好、
  ``1`` 表示最差。这与 ADR-0033 最终 L 等级方向（v3 L5=最差）**无关**——这里评的是
  单个维度好坏，不是整图等级，切勿在此处翻转方向。
- 输出契约与 ``_extract_specific_grades`` 严格对齐：``{"dimensions": {key:
  {"grade": 1..5, "evidence": "..."}}}``。
"""

from __future__ import annotations

from typing import Any

# The exact JSON shape 调用B must emit — kept as a literal so the prompt text and
# the parser (``worker_v3_shadow._extract_specific_grades``) stay in lockstep.
_OUTPUT_CONTRACT_HINT = (
    '{"dimensions": {"<维度key>": {"grade": <1到5的整数>, '
    '"evidence": "<该维度打分的图面证据，一句话>"}}}'
)


def _dimension_lines(specific_dims: list[dict[str, Any]]) -> str:
    """Render each specific dimension as a ``- key（label）`` bullet for the prompt."""
    lines: list[str] = []
    for dim in specific_dims:
        if not isinstance(dim, dict):
            continue
        key = dim.get("key")
        if not isinstance(key, str) or not key:
            continue
        label = dim.get("label")
        label_text = label if isinstance(label, str) and label else key
        lines.append(f"- {key}（{label_text}）")
    return "\n".join(lines)


def build_specific_dimension_shadow_prompt(
    track_key: str, specific_dims: list[dict[str, Any]]
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for the v3 specific-dimension 影子调用B.

    ``specific_dims`` is a list of ``{"key", "label"}`` dicts for one resolved
    track (drawn from the seed's ``_SPECIFIC_DIMENSIONS`` / the active v3 config's
    ``subcategory_dimensions``).  The returned prompts instruct the model to grade
    **only** those dimensions on the shared 1-5 anchor scale and emit strictly the
    ``{"dimensions": {...}}`` JSON above — nothing else.

    Pure: no IO, no side effects; deterministic for a given input.
    """
    keys = [
        dim["key"]
        for dim in specific_dims
        if isinstance(dim, dict) and isinstance(dim.get("key"), str) and dim["key"]
    ]

    system_prompt = (
        "你是灵感图特有维度评审助手。你只负责对给定的若干“特有维度”做 1-5 档打分，"
        "不评其它任何维度，也不给整图等级或总分。\n"
        "打分方向：grade=5 表示该维度在图中表现最好，grade=1 表示最差，2/3/4 居中，"
        "必须是 1 到 5 的整数。\n"
        "只输出一个合法 JSON 对象，不要包含 Markdown、代码块、解释或多余字段。"
    )

    user_prompt = (
        f"请针对赛道「{track_key}」评估以下特有维度，逐维给出 1-5 的 grade 与一句"
        "图面证据：\n"
        f"{_dimension_lines(specific_dims)}\n\n"
        "严格只输出如下结构的 JSON（键为上面列出的维度 key，一个都不能少、"
        "不要新增其它维度）：\n"
        f"{_OUTPUT_CONTRACT_HINT}\n\n"
        f"需要打分的维度 key 列表：{keys}"
    )

    return system_prompt, user_prompt
