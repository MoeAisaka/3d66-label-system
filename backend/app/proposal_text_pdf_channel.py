from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .proposal_text_pipeline import call_validated_json


@dataclass(frozen=True)
class ProposalCallAResult:
    precheck: dict[str, Any]
    responses: tuple[Any, ...]
    scanned_pages: tuple[int, ...]
    stop_reason: str
    batch_count: int


def _is_missing(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _manual_review(
    filename: str,
    page_count: int | None,
    reasons: Sequence[str],
) -> dict[str, Any]:
    message = "；".join(dict.fromkeys(reason for reason in reasons if reason)) or "跨批结果需要人工复核"
    return {
        "预检结果": {"状态": "人工复核", "是否进入B": False, "结论说明": message},
        "材料扫描": {
            "文件列表": [filename],
            "文件格式": ["PDF"],
            "总页数": page_count,
            "页面可读性": "无法判断",
        },
        "红线检查": {"是否命中": None, "命中项": []},
        "信息提取": None,
        "待复核项": list(dict.fromkeys(reasons)) or [message],
        "置信度": 0.0,
    }


def _merge_values(
    target: dict[str, Any],
    incoming: Mapping[str, Any],
    *,
    path: tuple[str, ...],
    conflicts: list[str],
) -> None:
    for key, incoming_value in incoming.items():
        current_path = (*path, key)
        if key not in target or _is_missing(target[key]):
            target[key] = deepcopy(incoming_value)
            continue
        if _is_missing(incoming_value):
            continue
        current_value = target[key]
        if isinstance(current_value, dict) and isinstance(incoming_value, Mapping):
            _merge_values(
                current_value,
                incoming_value,
                path=current_path,
                conflicts=conflicts,
            )
        elif current_value != incoming_value:
            conflicts.append(".".join(current_path))


def _deduplicated_redlines(outputs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for output in outputs:
        redline = output.get("红线检查")
        if not isinstance(redline, Mapping):
            continue
        for raw in redline.get("命中项", []):
            if not isinstance(raw, Mapping):
                continue
            evidence = raw.get("证据")
            fingerprint = (
                str(raw.get("类型") or ""),
                str(raw.get("说明") or ""),
                repr(evidence),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(deepcopy(dict(raw)))
    return merged


def merge_call_a_batches(
    outputs: Sequence[Mapping[str, Any]],
    *,
    filename: str,
    page_count: int | None,
) -> dict[str, Any]:
    if not outputs:
        return _manual_review(filename, page_count, ["调用A没有可合并的批次输出"])

    redlines = _deduplicated_redlines(outputs)
    if redlines:
        return {
            "预检结果": {
                "状态": "淘汰",
                "是否进入B": False,
                "结论说明": "调用A分批扫描命中红线",
            },
            "材料扫描": {
                "文件列表": [filename],
                "文件格式": ["PDF"],
                "总页数": page_count,
                "页面可读性": "正常",
            },
            "红线检查": {"是否命中": True, "命中项": redlines},
            "信息提取": None,
            "待复核项": [],
            "置信度": min(float(item.get("置信度", 0.0)) for item in outputs),
        }

    manual_reasons: list[str] = []
    for index, output in enumerate(outputs, start=1):
        result = output.get("预检结果")
        if not isinstance(result, Mapping) or result.get("状态") != "通过":
            reason = (
                str(result.get("结论说明") or "")
                if isinstance(result, Mapping)
                else "输出缺少预检结果"
            )
            manual_reasons.append(f"调用A第{index}批：{reason}")
        for item in output.get("待复核项", []):
            if isinstance(item, str) and item:
                manual_reasons.append(f"调用A第{index}批：{item}")
    if manual_reasons:
        return _manual_review(filename, page_count, manual_reasons)

    first_info = outputs[0].get("信息提取")
    if not isinstance(first_info, Mapping):
        return _manual_review(filename, page_count, ["调用A首批缺少信息提取"])
    merged_info = deepcopy(dict(first_info))
    image_totals = {"效果图数量": 0, "分析图数量": 0, "意向图数量": 0}
    image_seen = {key: False for key in image_totals}
    conflicts: list[str] = []
    for output in outputs:
        info = output.get("信息提取")
        if not isinstance(info, Mapping):
            continue
        image_stats = info.get("图像统计")
        if isinstance(image_stats, Mapping):
            for key in image_totals:
                value = image_stats.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    image_totals[key] += value
                    image_seen[key] = True
        comparable = {key: value for key, value in info.items() if key != "图像统计"}
        _merge_values(merged_info, comparable, path=("信息提取",), conflicts=conflicts)
    merged_info["图像统计"] = {
        key: image_totals[key] if image_seen[key] else None
        for key in image_totals
    }
    if conflicts:
        return _manual_review(
            filename,
            page_count,
            [f"调用A跨批字段冲突：{path}" for path in sorted(set(conflicts))],
        )

    return {
        "预检结果": {
            "状态": "通过",
            "是否进入B": True,
            "结论说明": f"调用A已分批扫描{len(outputs)}批，未命中红线",
        },
        "材料扫描": {
            "文件列表": [filename],
            "文件格式": ["PDF"],
            "总页数": page_count,
            "页面可读性": "正常",
        },
        "红线检查": {"是否命中": False, "命中项": []},
        "信息提取": merged_info,
        "待复核项": [],
        "置信度": min(float(item.get("置信度", 0.0)) for item in outputs),
    }


async def run_call_a_batches(
    batches: Sequence[Sequence[int]],
    *,
    invoke: Callable[[int, tuple[int, ...]], Awaitable[Any]],
    validator: Callable[[Any], dict[str, Any]],
    filename: str,
    page_count: int | None,
) -> ProposalCallAResult:
    validated: list[dict[str, Any]] = []
    responses: list[Any] = []
    scanned_pages: list[int] = []
    stop_reason = "completed"
    for batch_index, raw_batch in enumerate(batches):
        batch = tuple(int(page) for page in raw_batch)
        outcome = await call_validated_json(
            lambda: invoke(batch_index, batch),
            validator,
        )
        responses.extend(outcome.responses)
        scanned_pages.extend(batch)
        if outcome.value is None:
            precheck = _manual_review(
                filename,
                page_count,
                [f"调用A第{batch_index + 1}批连续2次校验失败：{outcome.error}"],
            )
            return ProposalCallAResult(
                precheck, tuple(responses), tuple(scanned_pages),
                "invalid_output", batch_index + 1,
            )
        validated.append(outcome.value)
        state = outcome.value["预检结果"]["状态"]
        if state == "淘汰":
            stop_reason = "redline"
            break
        if state == "人工复核":
            stop_reason = "manual_review"
            break

    return ProposalCallAResult(
        merge_call_a_batches(validated, filename=filename, page_count=page_count),
        tuple(responses),
        tuple(scanned_pages),
        stop_reason,
        len(validated),
    )


_EFFECT_KEYWORDS = ("效果图", "鸟瞰", "透视", "渲染", "实景", "render", "perspective")
_ANALYSIS_KEYWORDS = ("分析图", "分析", "场地", "动线", "区位", "流线", "diagram", "analysis")
_CONCEPT_KEYWORDS = ("概念", "推导", "理念", "策略", "意向", "concept", "strategy")


def _keyword_score(text: str, keywords: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(keyword.lower()) for keyword in keywords)


def _evenly_spaced_pages(pages: Sequence[int], count: int) -> list[int]:
    if count <= 0 or not pages:
        return []
    if count >= len(pages):
        return list(pages)
    if count == 1:
        return [pages[len(pages) // 2]]
    indices = {
        round(position * (len(pages) - 1) / (count - 1))
        for position in range(count)
    }
    return [pages[index] for index in sorted(indices)]


def select_representative_pages(
    page_texts: Mapping[int, str],
    *,
    table_of_contents: Sequence[tuple[int, str, int]],
    image_stats: Mapping[str, Any],
    sample_size: int = 16,
) -> tuple[int, ...]:
    if not 1 <= sample_size <= 16:
        raise ValueError("调用B代表页数量必须在1到16之间")
    pages = sorted(page for page in page_texts if page >= 1)
    if not pages:
        return ()
    toc_titles: dict[int, list[str]] = {}
    for _level, title, page in table_of_contents:
        if page in page_texts:
            toc_titles.setdefault(page, []).append(title)
    combined_text = {
        page: " ".join([page_texts.get(page, ""), *toc_titles.get(page, [])])
        for page in pages
    }
    selected: list[int] = [pages[0]]
    groups = (
        (_EFFECT_KEYWORDS, int(image_stats.get("效果图数量") or 0)),
        (_ANALYSIS_KEYWORDS, int(image_stats.get("分析图数量") or 0)),
        (_CONCEPT_KEYWORDS, int(image_stats.get("意向图数量") or 0) + 1),
    )
    ranked_groups: list[list[int]] = []
    for keywords, expected_count in groups:
        ranked = sorted(
            pages,
            key=lambda page: (-_keyword_score(combined_text[page], keywords), page),
        )
        ranked = [page for page in ranked if _keyword_score(combined_text[page], keywords) > 0]
        ranked_groups.append(ranked)
        target = min(max(1, expected_count), max(1, (sample_size - 1) // 3))
        for page in ranked[:target]:
            if page not in selected and len(selected) < sample_size:
                selected.append(page)
    for ranked in ranked_groups:
        for page in ranked:
            if page not in selected and len(selected) < sample_size:
                selected.append(page)
    remaining = [page for page in pages if page not in selected]
    for page in _evenly_spaced_pages(remaining, sample_size - len(selected)):
        if page not in selected:
            selected.append(page)
    if len(selected) < sample_size:
        for page in remaining:
            if page not in selected:
                selected.append(page)
            if len(selected) == sample_size:
                break
    return tuple(selected[:sample_size])


def _stage_usage(responses: Sequence[Any]) -> tuple[dict[str, int | None], bool]:
    if not responses:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, True
    fields = ("input_tokens", "output_tokens", "total_tokens")
    if any(
        not isinstance(getattr(response, field, None), int)
        for response in responses
        for field in fields
    ):
        return {field: None for field in fields}, False
    return (
        {
            field: sum(int(getattr(response, field)) for response in responses)
            for field in fields
        },
        True,
    )


def summarize_stage_usage(
    call_a_responses: Sequence[Any],
    call_b_responses: Sequence[Any],
) -> dict[str, Any]:
    call_a, a_measured = _stage_usage(call_a_responses)
    call_b, b_measured = _stage_usage(call_b_responses)
    measured = a_measured and b_measured and bool(call_a_responses or call_b_responses)
    total = {
        field: (
            int(call_a[field]) + int(call_b[field])
            if measured and call_a[field] is not None and call_b[field] is not None
            else None
        )
        for field in ("input_tokens", "output_tokens", "total_tokens")
    }
    return {"measured": measured, "call_a": call_a, "call_b": call_b, "total": total}


_TEXT_SOURCE_LABELS = {
    "text_layer": "PDF文本层",
    "ocr": "OCR补充",
    "image": "无文本层，依据页图",
}


def build_call_a_batch_context(
    *,
    batch_index: int,
    pages: Sequence[Any],
    total_pages: int,
) -> str:
    if batch_index < 0 or total_pages < 1:
        raise ValueError("调用A批次参数不合法")
    sections = [
        (
            f"\n\n【引擎确定性分页输入：第{batch_index + 1}批】\n"
            f"本批共{len(pages)}页；整份PDF共{total_pages}页。"
            "必须逐页检查红线并只依据本批页面输出调用A结构。"
        )
    ]
    for page in pages:
        page_number = int(page.page_number)
        source = _TEXT_SOURCE_LABELS.get(str(page.text_source), str(page.text_source))
        text = str(page.text or "").strip() or "（本页无可用文本，以随附页图为准）"
        sections.append(
            f"\n--- 第{page_number}页/共{total_pages}页｜文本来源：{source} ---\n{text}"
        )
    return "".join(sections)


def build_text_layer_summary(
    page_texts: Mapping[int, str],
    *,
    table_of_contents: Sequence[tuple[int, str, int]],
    max_chars: int = 30_000,
) -> str:
    if max_chars < 256:
        raise ValueError("文本层摘要字符上限过小")
    toc_lines = ["【目录结构】"]
    if table_of_contents:
        toc_lines.extend(
            f"{'  ' * max(0, int(level) - 1)}{int(level)}. {str(title).strip()}（第{int(page)}页）"
            for level, title, page in table_of_contents
        )
    else:
        toc_lines.append("PDF未提供可读目录书签。")
    page_lines = ["", "【文本层逐页摘要】"]
    for page_number in sorted(page_texts):
        normalized = " ".join(str(page_texts[page_number] or "").split())
        if normalized:
            page_lines.append(f"[第{page_number}页] {normalized}")
        else:
            page_lines.append(f"[第{page_number}页] 无可用文本，需结合页图判断。")
    full = "\n".join([*toc_lines, *page_lines])
    if len(full) <= max_chars:
        return full
    suffix = "\n【已按确定性字符上限截断；代表页图仍完整随调用B提供】"
    return full[: max_chars - len(suffix)].rstrip() + suffix
