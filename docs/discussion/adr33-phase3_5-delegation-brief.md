# ADR-0033 Phase 3.5 委派任务书（grade → deduction 桥 · 纯函数）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 定位（重要）

红线 + v3 合同 + 聚合器是「调用A/调用B/维度」的**重构版**，终态两条流水线都收敛到它。本阶段补上**缺失的一环**：把现有调用B 产出的 `grade`(1-5) 确定性地转成聚合器消费的 `deductions[dimension_key]`。这是 worker 接线（Phase 4）前的最后一块纯函数框架。

## 边界（硬约束，同前几期）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- 只新建下列文件，**不改任何现有文件**、不接 worker、不做 L 翻转、不碰前端：
  1. `backend/app/dimension_grade_bridge.py`
  2. `backend/tests/test_dimension_grade_bridge.py`

## 背景（先读）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`（尤其"零、最终形态"与"grade→deduction 桥"）
- `backend/app/category_evaluation_aggregator.py`（消费方：`aggregate_category_evaluation` 的 `dimension_result["deductions"]`，值为 >=0 的每维度扣分，累计封顶到 `dimension_max`）
- `backend/app/dimension_schema_registry.py`：`_GRADE_POINTS = {"1":20,"2":45,"3":65,"4":82,"5":95}`；维度定义每项含 `key`、`weight`（同一 Schema 内 `weight_sum_rule="strictly_equals_1"`）、`grade_points`。
- `backend/app/scoring.py` 现有加权算法只读参考，不改。

## 桥的数学（确定性、可回归）

给定：一个赛道的 `dimension_max`（该赛道维度块满分，如灵感图一类=60）、维度 Schema（每维度 `key`+`weight`，weights 求和=1、`grade_points`），以及调用B 每维度 `grade`。

对每个维度 key：
- 该维度**满分份额** `share = weight * dimension_max`。
- grade 归一化得分率 `ratio = (grade_points[grade] - min_points) / (max_points - min_points)`，其中 `min_points=grade_points["1"]`、`max_points=grade_points["5"]`。ratio ∈ [0,1]，grade 5→1（满分、不扣），grade 1→0（扣满该维度份额）。
- 该维度扣分 `deduction = share * (1 - ratio)`，保留合理精度（round 到 4 位小数即可，聚合器最终会整体取整）。

产出 `deductions[key] = deduction`（>=0）。所有维度扣分之和 ≈ `dimension_max * (1 - 平均得分率)`，天然 <= dimension_max，与聚合器的 clamp 语义一致。

## 必做函数（`dimension_grade_bridge.py`）

```python
GRADE_BRIDGE_VERSION = "dimension-grade-bridge-v1"

class DimensionGradeBridgeError(ValueError):  # 带 .code，同前几期约定
    ...

def grades_to_deductions(
    *,
    dimension_grades: dict[str, int],          # 调用B 每维度 grade(1-5)
    dimension_schema_definition: dict,          # 含 dimensions[].key/weight/grade_points 或顶层 grade_points
    dimension_max: int | float,                 # 该赛道维度块满分
) -> dict:
    """返回 {"deductions": {key: 扣分}, "evidence": {...}}，纯函数、确定性。"""
```

要求与 fail-closed：
- Schema 的维度 keys 与 `dimension_grades` 必须一致（多、缺都 fail-closed，code 如 `grade_keys_mismatch`）。
- grade 必须是 1-5 整数，否则 `grade_out_of_range`。
- weights 求和必须严格=1（容差 1e-9），否则 `weights_not_normalized`。
- `dimension_max` 必须 >=0 数值。
- grade_points 取维度自带的，缺失回退顶层 aggregation.grade_points / 顶层 grade_points；都没有则 `grade_points_missing`。要求 `max_points>min_points`。
- 输出 `evidence` 至少含每维度 `{grade, ratio, share, deduction}`，便于回归解释。
- 纯函数：无 IO/网络/DB/模型；同输入同输出。

（可选但推荐）再加一个便捷函数把桥 + 聚合器串起来验证方向一致，但**不要 import worker**：
```python
def deductions_from_bridge(...) -> dict  # 仅返回 deductions dict，方便调用方直接喂给聚合器
```
如果加，保持同文件、纯函数。

## 测试（`test_dimension_grade_bridge.py`）

用真实 `_GRADE_POINTS` 与一套 2-3 维度、weights 和=1 的 mini schema，覆盖：
- 全 grade 5 → 所有 deduction=0（满分不扣）。
- 全 grade 1 → 扣分之和 == dimension_max（扣满）。
- 混合 grade → 单维度 deduction == share*(1-ratio)，数值精确断言（举一个手算例：weight=0.5, dimension_max=60, grade=3 → share=30, ratio=(65-20)/(95-20)=0.6, deduction=30*0.4=12.0）。
- 扣分之和 <= dimension_max（永不超过维度块满分）。
- **桥→聚合器方向自洽**：全 grade 5 喂进灵感图一类合同 → 聚合器 score=100/L1；全 grade 1 → 维度块扣满 → score=base_score(40)/对应 L 档。用 `category_evaluation_aggregator.aggregate_category_evaluation` 实跑验证。
- fail-closed：keys 不匹配 / grade 越界 / weights 不为1 / grade_points 缺失 / dimension_max 负数。
- 确定性：同输入多次同输出。

## 完成信号

写 `ADR33_PHASE3_5_DONE.md`（文件清单、导出名、覆盖场景、grade→deduction 公式），写完即停，等 OpenClaw 验收。
