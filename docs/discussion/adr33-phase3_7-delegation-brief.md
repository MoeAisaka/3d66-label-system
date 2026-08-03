# ADR-0033 Phase 3.7 委派任务书（分类信号 → 子类目解析器 · 纯函数）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 定位

权威流水线：红线 → **分类器(类目→子类目)** → 子类目 A/B → 维度(共性+特有) → 产出。
本阶段做**分类器节点的确定性解析器**：把调用A 产出的分类信号（`classification.primary_category` 等）映射到 v3 合同里的**子类目 key（track key）**，供聚合器 `track_key` 使用。纯函数、框架层，不接 worker、不做 L 翻转、不碰前端。

## 边界（硬约束，同前几期）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- **不改任何现有文件**。只新建：
  1. `backend/app/subcategory_resolver.py`
  2. `backend/tests/test_subcategory_resolver.py`

## 背景（先读）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`（权威流水线、分类器→子类目）
- `backend/app/dimension_router.py`：调用A 的分类信号读法——`classification.scope_status`(in_scope/boundary/out_of_scope)、`classification.primary_category`(字符串)、`classification.primary_confidence`(0-1 float)。
- `backend/app/category_evaluation_contract.py`：v3 合同 `track_classification`（tracks[].key、default_track）。子类目 == track。
- `backend/app/category_evaluation_aggregator.py`：消费 `track_key`。

## 数据契约：分类映射（挂在 track_classification 上，本阶段用独立可选字段）

解析器读一个**分类映射表**（本阶段作为独立入参，不改冻结合同）：
```
classification_map = {
  "format_version": "subcategory-classification-map-v1",
  "min_confidence": 0.6,                 # 低于此置信度 → 落 default 并标记 needs_review
  "category_to_subcategory": {           # 调用A primary_category → 子类目(track) key
     "建筑设计": "class_one",
     "产品设计": "class_two",
     "其它": "class_three",
     ...
  },
  "out_of_scope_subcategory": "class_three"  # scope_status=out_of_scope 时直接落此
}
```

## 必做函数（`subcategory_resolver.py`）

```python
CLASSIFICATION_MAP_FORMAT_VERSION = "subcategory-classification-map-v1"

class SubcategoryResolverError(ValueError):  # 带 .code
    ...

def validate_classification_map(classification_map: dict, *, valid_track_keys: set[str]) -> None:
    """fail-closed 校验；映射目标与 out_of_scope 目标必须都是合同已定义的 track key。"""

def resolve_subcategory(
    precheck: dict,
    *,
    classification_map: dict,
    track_classification: dict,   # v3 合同的 track_classification 块（拿 default_track + valid keys）
) -> dict:
    """把调用A 分类信号解析为子类目 key。纯函数、确定性。

    返回 {"track_key": "...", "resolved_by": "...", "needs_review": bool,
          "primary_category": "...", "confidence": float, "notes": [...]}。
    """
```

解析顺序（确定性，逐步记入 notes）：
1. 校验 classification_map（委托 valid_track_keys=合同 tracks 的 key 集合）与 track_classification（至少含 default_track 且在 keys 内）。
2. 读 `precheck.classification`：scope_status / primary_category / primary_confidence。缺失或非法 → 落 default_track，resolved_by="invalid_classification"，needs_review=true。
3. scope_status == "out_of_scope" → track_key = out_of_scope_subcategory，resolved_by="out_of_scope"。
4. primary_confidence < min_confidence → 落 default_track，resolved_by="low_confidence"，needs_review=true（记录原始 primary_category）。
5. primary_category 命中 category_to_subcategory → 该子类目，resolved_by="mapped"。
6. 未命中映射 → 落 default_track，resolved_by="unmapped_category"，needs_review=true。
- 任何落 default 分支，最终 track_key 必须是合同里存在的 key（default_track 已校验）。
- boundary scope_status 视为在范围内继续走 4/5/6（可在 notes 标注 boundary）。
- 纯函数：无 IO/网络/DB/模型。

## 测试（`test_subcategory_resolver.py`）

用灵感图三子类目（class_one/class_two/class_three）+ 一份映射表覆盖：
- 正常命中：primary_category 建筑设计 + 高置信 → class_one，resolved_by=mapped，needs_review=false。
- out_of_scope → out_of_scope_subcategory，resolved_by=out_of_scope。
- 低置信 → default_track + low_confidence + needs_review。
- 未映射类目 → default_track + unmapped_category + needs_review。
- 缺失/非法 classification → default_track + invalid_classification + needs_review。
- boundary scope_status 仍按映射解析。
- **解析器→聚合器串联**：resolve 得到 track_key 后喂给 `aggregate_category_evaluation`，得到对应子类目的基底分/封顶正确（如 class_two 满分≤80）。
- fail-closed：映射目标/out_of_scope 目标不在合同 track keys 内 → 报错；classification_map 版本错误 → 报错；default_track 不在 keys → 报错。
- 确定性 + JSON 可序列化。

## 完成信号

写 `ADR33_PHASE3_7_DONE.md`（文件、导出名、解析顺序、覆盖场景），写完即停，等 OpenClaw 验收。
