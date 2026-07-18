from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptPair:
    system: str
    user: str


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    try:
        start = source.index(start_marker) + len(start_marker)
        end = source.index(end_marker, start)
    except ValueError as exc:
        raise RuntimeError(f"提示词文件缺少段落：{start_marker.strip()}") from exc
    return source[start:end].strip()


def load_prompt_pairs(path: Path) -> dict[str, PromptPair]:
    source = path.read_text(encoding="utf-8")
    system_a = _extract(source, "### System Prompt A\n", "### User Prompt A")
    user_a = _extract(source, "### User Prompt A\n", "\n---")
    system_b = _extract(source, "### System Prompt B\n", "### User Prompt B")
    user_b = _extract(source, "### User Prompt B\n", "\n---\n\n## 三、外部评分引擎规则")
    return {
        "A": PromptPair(system=system_a, user=user_a),
        "B": PromptPair(system=system_b, user=user_b),
    }
