# ADR-0033 Phase 3.6 委派任务书（子类目 共性维度+特有维度 组合 · 纯函数）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 定位（Owner 2026-08-03 校正的权威流水线）

```
红线(直筛L5) → 分类器(类目→子类目) → 子类目: 提示词A/B → 维度评测(子类目共性维度 + 子类目特有维度) → 产出(等级+分数+固定字段)
```

关键：维度**不是每子类目一套 schema，而是「共性维度 + 特有维度」两组**合并评测。当前 v3 合同每个 track（=子类目）只挂单个 `dimension_schema_ref`，需要扩成**两个引用**并提供确定性的**两组维度合并器**。本阶段只做框架（纯函数 + 校验 + 测试），不接 worker、不做 L 翻转、不碰前端。

## 边界（硬约束，同前几期）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- **不改任何现有文件**。只新建：
  1. `backend/app/dimension_composition.py`
  2. `backend/tests/test_dimension_composition.py`

（不要动 `category_evaluation_contract.py`；本阶段用**独立的可选扩展校验器**处理"双维度引用"，避免破坏已冻结的 v3 合同与其 34 个测试。终态合并进合同由后续阶段做。）

## 背景（先读）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`（"权威流水线定义"与"子类目 共性/特有维度"）
- `backend/app/dimension_grade_bridge.py`（Phase 3.5：`grades_to_deductions` 把单组维度 grade→deduction，本阶段复用它）
- `backend/app/dimension_schema_registry.py`：schema 定义结构（dimensions[].key/weight/grade_points；`core_dimension_keys`）
- `backend/app/category_evaluation_aggregator.py`（最终消费方：`deductions[key]`）

## 数据契约：子类目双维度引用

一个子类目的维度评测配置（本阶段用独立 dict 表达，不塞进 v3 合同）：
```
{
  "format_version": "subcategory-dimensions-v1",
  "sub_category_key": "class_one",
  "dimension_max": 60,
  "common_group": {           # 共性维度组（跨子类目共享）
    "group_weight": 0.4,      # 该组占 dimension_max 的比例
    "schema_definition": { "dimensions": [ {key,weight,grade_points}, ... ] }  # 组内 weights 和=1
  },
  "specific_group": {         # 特有维度组（子类目自定义）
    "group_weight": 0.6,
    "schema_definition": { "dimensions": [ ... ] }  # 组内 weights 和=1
  }
}
```
约束：`common_group.group_weight + specific_group.group_weight == 1`（容差1e-9）；每组内部 weights 和=1；两组维度 key 不得重叠；dimension_max>=0。

## 必做函数（`dimension_composition.py`）

```python
SUBCATEGORY_DIMENSIONS_FORMAT_VERSION = "subcategory-dimensions-v1"

class DimensionCompositionError(ValueError):  # 带 .code
    ...

def validate_subcategory_dimensions(config: dict) -> None:
    """fail-closed 校验双维度引用配置。"""

def compose_deductions(
    *,
    config: dict,                         # 上面的 subcategory-dimensions-v1
    common_grades: dict[str, int],        # 调用B 对共性维度的 grade
    specific_grades: dict[str, int],      # 调用B 对特有维度的 grade
) -> dict:
    """把两组维度各自 grade→deduction 后合并成一个 deductions 映射。

    实现：
    - 校验 config。
    - 共性组 effective_max = group_weight_common * dimension_max；
      特有组 effective_max = group_weight_specific * dimension_max。
    - 分别调用 dimension_grade_bridge.grades_to_deductions（复用！不要重写扣分数学），
      传各自 schema_definition、各自 grades、各自 effective_max。
    - 合并两个 deductions 到一个 dict（key 不重叠已校验）。
    - 返回 {"deductions": {...合并...}, "dimension_max": ..,
            "common": <桥输出>, "specific": <桥输出>, "evidence": {...}}。
    输出可直接作为聚合器的 dimension_result（含 deductions）。纯函数、确定性。
    """
```

不确定项：两组 grades 的 key 必须分别与各自 schema 完全一致（交给桥的 `grade_keys_mismatch` fail-closed）；两组 key 交集必须为空（本模块校验，code `dimension_key_overlap`）。

## 测试（`test_dimension_composition.py`）

- 合法配置：共性组(2维, group_weight 0.4)+特有组(2维, group_weight 0.6), dimension_max=60。
  - 全 grade5 → 合并 deductions 全 0，和=0。
  - 全 grade1 → 合并扣分和 == dimension_max(60)（共性组扣满 24 + 特有组扣满 36）。
  - 手算混合：共性 effective_max=24，某共性维 weight=0.5→share=12，grade3→ratio0.6→deduction 4.8；断言精确值。
- **合并→聚合器 round-trip**：把 compose_deductions 的输出喂给 `aggregate_category_evaluation`（灵感图 class_one 合同, dimension_max 60），全5→100/L1、全1→40/L3。
- 两组 key 重叠 → `dimension_key_overlap` fail-closed。
- group_weight 和≠1 → fail-closed。
- 组内 weights≠1 / grade 越界 / key 不匹配 → 经桥 fail-closed（断言抛 DimensionCompositionError 或 DimensionGradeBridgeError 之一，任选清晰方案：建议本模块把桥的错误透传或包成 DimensionCompositionError 带原 code 前缀）。
- 确定性 + JSON 可序列化。

## 完成信号

写 `ADR33_PHASE3_6_DONE.md`（文件、导出名、覆盖场景、共性/特有合并公式），写完即停，等 OpenClaw 验收。
