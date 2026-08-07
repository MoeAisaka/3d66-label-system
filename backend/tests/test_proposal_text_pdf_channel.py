from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.proposal_text_pdf_channel import (
    build_call_a_batch_context,
    build_text_layer_summary,
    merge_call_a_batches,
    run_call_a_batches,
    select_representative_pages,
    summarize_stage_usage,
)


def passed_batch(
    *,
    project_name: str = "示例项目",
    category: str = "A",
    effect_count: int = 2,
    analysis_count: int = 1,
    completeness: str = "是",
) -> dict:
    return {
        "预检结果": {"状态": "通过", "是否进入B": True, "结论说明": "本批材料正常"},
        "材料扫描": {
            "文件列表": ["sample.pdf"],
            "文件格式": ["PDF"],
            "总页数": 32,
            "页面可读性": "正常",
        },
        "红线检查": {"是否命中": False, "命中项": []},
        "信息提取": {
            "项目分类": {
                "审核类别": category,
                "一级分类": "建筑设计",
                "二级分类": "公共建筑",
                "分类依据": "页面可见",
            },
            "项目基本信息": {
                "项目名称": project_name,
                "专业标题": "公共建筑",
                "SEO标题": "示例公共建筑设计方案",
                "设计主题": "共生",
                "概念摘要": "围绕场地形成完整叙事",
                "风格": "现代",
                "标签": ["建筑"],
                "设计师或设计公司": "示例事务所",
                "所在城市": "上海",
                "项目或文本年份": 2025,
                "项目工期": "2年",
            },
            "图像统计": {
                "效果图数量": effect_count,
                "分析图数量": analysis_count,
                "意向图数量": 0,
            },
            "内容完整性": {
                "项目背景": completeness,
                "场地或问题分析": completeness,
                "概念推导": completeness,
                "空间策略": completeness,
                "动线展示": completeness,
                "效果图": completeness,
            },
        },
        "待复核项": [],
        "置信度": 0.9,
    }


def redline_batch() -> dict:
    return {
        "预检结果": {"状态": "淘汰", "是否进入B": False, "结论说明": "发现竞品水印"},
        "材料扫描": {
            "文件列表": ["sample.pdf"],
            "文件格式": ["PDF"],
            "总页数": 32,
            "页面可读性": "正常",
        },
        "红线检查": {
            "是否命中": True,
            "命中项": [{
                "类型": "竞品水印",
                "说明": "第3页存在竞品网址",
                "证据": [{"source": "sample.pdf", "page": 3, "observation": "可见竞品网址"}],
            }],
        },
        "信息提取": None,
        "待复核项": [],
        "置信度": 0.99,
    }


def test_merge_call_a_batches_uses_first_seen_and_sums_batch_image_counts() -> None:
    merged = merge_call_a_batches(
        [passed_batch(effect_count=2), passed_batch(effect_count=3)],
        filename="sample.pdf",
        page_count=32,
    )
    assert merged["预检结果"]["状态"] == "通过"
    assert merged["信息提取"]["项目基本信息"]["项目名称"] == "示例项目"
    assert merged["信息提取"]["图像统计"]["效果图数量"] == 5


def test_merge_call_a_batches_keeps_ordinary_conflict_as_audit_and_scores_document() -> None:
    merged = merge_call_a_batches(
        [passed_batch(project_name="甲项目"), passed_batch(project_name="乙项目")],
        filename="sample.pdf",
        page_count=32,
    )
    assert merged["预检结果"]["状态"] == "通过"
    assert merged["信息提取"]["项目基本信息"]["项目名称"] == "甲项目"
    assert "项目基本信息.项目名称" in merged["信息提取"]["_聚合审计"]["字段冲突"]


def test_merge_call_a_batches_requires_document_classification_consensus() -> None:
    merged = merge_call_a_batches(
        [passed_batch(category="A"), passed_batch(category="B")],
        filename="sample.pdf",
        page_count=32,
    )
    assert merged["预检结果"]["状态"] == "人工复核"
    assert any("审核类别" in item for item in merged["待复核项"])


def test_merge_call_a_batches_weights_classification_by_covered_pages() -> None:
    merged = merge_call_a_batches(
        [
            passed_batch(category="A"),
            passed_batch(category="B"),
            passed_batch(category="B"),
        ],
        filename="sample.pdf",
        page_count=48,
        batch_page_counts=[32, 8, 8],
    )
    assert merged["预检结果"]["状态"] == "通过"
    assert merged["信息提取"]["项目分类"]["审核类别"] == "A"
    assert merged["信息提取"]["_聚合审计"]["分类页权重"] == {"A": 32, "B": 16}


def test_merge_call_a_batches_uses_conservative_completeness_union() -> None:
    merged = merge_call_a_batches(
        [passed_batch(completeness="否"), passed_batch(completeness="是")],
        filename="sample.pdf",
        page_count=32,
    )
    assert merged["预检结果"]["状态"] == "通过"
    assert merged["信息提取"]["内容完整性"]["效果图"] == "是"


def test_run_call_a_batches_stops_immediately_after_redline() -> None:
    calls: list[int] = []

    async def invoke(batch_index: int, _batch: tuple[int, ...]):
        calls.append(batch_index)
        return SimpleNamespace(
            parsed=redline_batch(),
            raw_text="{}",
            raw_payload={"batch": batch_index},
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )

    result = asyncio.run(run_call_a_batches(
        [(1, 2), (3, 4), (5, 6)],
        invoke=invoke,
        validator=lambda value: value,
        filename="sample.pdf",
        page_count=6,
    ))
    assert calls == [0]
    assert result.precheck["预检结果"]["状态"] == "淘汰"
    assert result.stop_reason == "redline"
    assert result.scanned_pages == (1, 2)


def test_run_call_a_batches_recovers_invalid_batch_by_binary_split() -> None:
    calls: list[tuple[int, ...]] = []

    async def invoke(_batch_index: int, batch: tuple[int, ...]):
        calls.append(batch)
        parsed = {"valid": True} if len(batch) <= 8 else {"valid": False}
        return SimpleNamespace(
            parsed=parsed,
            raw_text="{}",
            raw_payload={"batch": list(batch)},
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )

    def validator(value):
        if value.get("valid") is not True:
            raise ValueError("invalid batch")
        return passed_batch()

    result = asyncio.run(run_call_a_batches(
        [tuple(range(1, 17))],
        invoke=invoke,
        validator=validator,
        filename="sample.pdf",
        page_count=16,
    ))
    assert calls == [tuple(range(1, 17))] * 2 + [tuple(range(1, 9)), tuple(range(9, 17))]
    assert result.precheck["预检结果"]["状态"] == "通过"
    assert result.scanned_pages == tuple(range(1, 17))
    assert result.stop_reason == "completed"


def test_run_call_a_batches_keeps_unrecoverable_page_fail_closed() -> None:
    async def invoke(_batch_index: int, _batch: tuple[int, ...]):
        return SimpleNamespace(
            parsed={"valid": False},
            raw_text="{}",
            raw_payload={"invalid": True},
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    result = asyncio.run(run_call_a_batches(
        [(1,)],
        invoke=invoke,
        validator=lambda _value: (_ for _ in ()).throw(ValueError("invalid")),
        filename="sample.pdf",
        page_count=1,
    ))
    assert result.precheck["预检结果"]["状态"] == "人工复核"
    assert result.stop_reason == "invalid_output"
    assert result.failed_pages == (1,)


def test_run_call_a_batches_recovers_default_batch_to_single_pages() -> None:
    calls: list[tuple[int, ...]] = []

    async def invoke(_batch_index: int, batch: tuple[int, ...]):
        calls.append(batch)
        parsed = {"valid": len(batch) == 1}
        return SimpleNamespace(
            parsed=parsed,
            raw_text="{}",
            raw_payload={"batch": list(batch)},
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def validator(value):
        if value.get("valid") is not True:
            raise ValueError("invalid batch")
        return passed_batch()

    result = asyncio.run(run_call_a_batches(
        [tuple(range(1, 17))],
        invoke=invoke,
        validator=validator,
        filename="sample.pdf",
        page_count=16,
    ))
    assert result.stop_reason == "completed"
    assert result.scanned_pages == tuple(range(1, 17))
    assert all((page,) in calls for page in range(1, 17))


def test_select_representative_pages_is_deterministic_and_bounded() -> None:
    page_texts = {
        1: "封面 项目名称",
        2: "目录 CONTENTS",
        3: "概念推导 设计理念",
        4: "场地分析 动线分析",
        5: "效果图 鸟瞰图",
        6: "效果图 室内透视",
        7: "空间策略 分析图",
        **{page: f"普通页面 {page}" for page in range(8, 41)},
    }
    toc = ((1, "封面", 1), (1, "概念设计", 3), (1, "效果展示", 5))
    stats = {"效果图数量": 8, "分析图数量": 5, "意向图数量": 1}
    first = select_representative_pages(
        page_texts, table_of_contents=toc, image_stats=stats, sample_size=16
    )
    second = select_representative_pages(
        page_texts, table_of_contents=toc, image_stats=stats, sample_size=16
    )
    assert first == second
    assert first[0] == 1
    assert len(first) == 16
    assert {3, 4, 5}.issubset(first)


def test_summarize_stage_usage_keeps_a_and_b_separate() -> None:
    a = [
        SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
        SimpleNamespace(input_tokens=110, output_tokens=21, total_tokens=131),
    ]
    b = [SimpleNamespace(input_tokens=80, output_tokens=15, total_tokens=95)]
    assert summarize_stage_usage(a, b) == {
        "measured": True,
        "call_a": {"input_tokens": 210, "output_tokens": 41, "total_tokens": 251},
        "call_b": {"input_tokens": 80, "output_tokens": 15, "total_tokens": 95},
        "total": {"input_tokens": 290, "output_tokens": 56, "total_tokens": 346},
    }

def test_build_call_a_batch_context_includes_every_page_text_and_source() -> None:
    pages = [
        SimpleNamespace(page_number=1, text="封面文本", text_source="text_layer"),
        SimpleNamespace(page_number=2, text="", text_source="image"),
    ]
    context = build_call_a_batch_context(
        batch_index=0,
        pages=pages,
        total_pages=32,
    )
    assert "第1批" in context
    assert "第1页/共32页" in context
    assert "文本层" in context
    assert "封面文本" in context
    assert "第2页/共32页" in context
    assert "本页无可用文本" in context


def test_build_text_layer_summary_covers_toc_and_all_pages_deterministically() -> None:
    page_texts = {1: "封面 项目名称", 2: "场地分析 " + "甲" * 1000, 3: "概念推导"}
    summary = build_text_layer_summary(
        page_texts,
        table_of_contents=((1, "封面", 1), (1, "概念", 3)),
        max_chars=300,
    )
    assert "目录结构" in summary
    assert "1. 封面（第1页）" in summary
    assert "文本层逐页摘要" in summary
    assert "[第1页]" in summary and "[第2页]" in summary
    assert "已按确定性字符上限截断" in summary
