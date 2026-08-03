# ADR-0033 Phase 1 委派任务书（通用框架先行 · 纯确定性底座）

给执行器（MacBook Claude Fable 5）。OpenClaw 负责控制与验收；你是本任务唯一写入者。

## 你的边界（硬约束）

- 只允许用 Read / Edit / Write / Glob / Grep。**禁止** Bash、git、网络、安装、运行测试/构建。测试、lint、build、提交由 OpenClaw 侧完成。
- 工作根目录：本 worktree（`~/OpenClaw/labellab-adr33-framework`），基线 `cad79b6`。不要碰其他目录、不要改 `.venv`、不要动 `frontend/node_modules`。
- 只写下列「允许写入范围」内的文件。其余一律不动。
- 这是**框架先行**阶段：只建「可冻结的合同 + 确定性纯函数 + 校验器 + 单元测试」。**不要**把新逻辑接进 worker 生产执行路径，**不要**做 L 等级方向翻转迁移，**不要**改已发布标签/消费接口，**不要**改前端。这些是后续独立阶段。

## 背景（读这些文件先建立上下文）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`（本阶段总纲）
- `docs/reference/category-inspiration-image-rules-20260803.md`（灵感图规则原文）
- `backend/app/category_pipeline.py`（现有类目流水线合同 v2，`evaluation-category-profile-v2`，见 `default_pipeline`/`validate_pipeline_config`）
- `backend/app/schema_adapter.py`（调用A产出字段与枚举：`PRODUCTION_REASON_VALUES`、`PRODUCTION_FIELD_KEYS`、`_validate_media_form`）
- `backend/app/dimension_schema_registry.py`、`backend/app/scoring.py`（现有维度/分数/等级，**本阶段只读参考，不改**）

## 允许写入范围（且仅限这些）

1. 新增 `backend/app/redline_policy.py`
2. 新增 `backend/app/category_evaluation_contract.py`
3. 新增 `backend/tests/test_redline_policy.py`
4. 新增 `backend/tests/test_category_evaluation_contract.py`

不要修改任何现有 .py、不要动前端、不要动迁移 runner、不要动 ADR。

## 任务 1：`backend/app/redline_policy.py`（确定性红线，纯函数）

实现一个**无副作用、无 IO、无模型调用**的红线判定纯函数模块。

### 数据契约

定义常量与校验器：

```python
REDLINE_POLICY_FORMAT_VERSION = "redline-policy-v1"
```

一个合法 `redline_policy` dict 结构：
```
{
  "format_version": "redline-policy-v1",
  "enabled": true,
  "hit_level": "L1",            # 必须是 L1..L5 之一；语义="淘汰档"，方向问题由后续阶段处理，本阶段只校验取值合法
  "hit_score_cap": 49,          # 0..100 整数
  "rules": [
    {
      "key": "screenshot",      # ^[a-z][a-z0-9_]{2,39}$，规则内唯一
      "signal": "production_fields.reason",   # 本阶段只支持这一个信号源，其它值 fail-closed
      "match_any": ["是截图"],   # 非空；每个值必须 ∈ schema_adapter.PRODUCTION_REASON_VALUES
      "exemptions": []          # 字符串数组，可空；命中 match 但 evidence 命中任一 exemption 时不算命中（见下）
    }, ...
  ]
}
```

### 必做函数

- `validate_redline_policy(policy: dict) -> None`：结构/枚举/唯一性/取值全部 fail-closed，非法抛 `RedlinePolicyError`（继承 ValueError，带 `.code`）。空 rules 且 enabled=true 也非法。复用 `schema_adapter.PRODUCTION_REASON_VALUES` 校验 match 值，不要另抄一份枚举。
- `evaluate_redlines(precheck: dict, *, policy: dict) -> dict`：
  - 先 `validate_redline_policy`。
  - `enabled=false` → 返回 `{"hit": false, "hit_rules": [], "hard_reject": false}`（不淘汰）。
  - 从 `precheck["production_fields"]["reason"]`（缺失/非 list 视为空）逐条匹配：某规则 `match_any` 与 reason 有交集即初步命中；若该规则有 exemptions 且 `precheck` 的豁免证据命中（本阶段：`production_fields.reason` 里出现 exemption 文案即视为豁免——保持确定、可回归），则该规则不计命中。
  - 任一规则命中 → `{"hit": true, "hit_rules": [key...(有序去重)], "hit_level": policy["hit_level"], "hit_score_cap": policy["hit_score_cap"], "hard_reject": true}`。
  - 输出结构固定、可 JSON 序列化、对同一输入稳定（确定性）。
- 模块 docstring 顶部写明：纯函数、无 IO/网络/DB/模型、可回归。

## 任务 2：`backend/app/category_evaluation_contract.py`（v3 合同骨架，纯定义+校验）

定义 v3 评测合同的**数据结构与校验器**（本阶段不接执行、不接 worker）：

```python
CATEGORY_EVALUATION_CONTRACT_VERSION = "evaluation-category-profile-v3"
```

v3 = 在 v2 基础上新增三块（**不要重定义 v2 已有部分**，v3 合同以 `dict` 承载，新增键为）：
- `redline_policy`：交给 `redline_policy.validate_redline_policy` 校验。
- `track_classification`：
  ```
  {
    "format_version": "track-classification-v1",
    "tracks": [
      {"key": "class_one", "label": "一类", "base_score": 40, "dimension_max": 60, "track_cap": 100,
       "dimension_schema_ref": {"schema_key": "...", "version": "..."}},
      ...
    ],
    "default_track": "class_three"   # 必须是 tracks 里的 key
  }
  ```
  校验：track key 唯一且匹配 `^[a-z][a-z0-9_]{2,39}$`；base_score/dimension_max/track_cap 为 0..100 整数且 `base_score+dimension_max<=track_cap<=100`（对齐灵感图：一类40+60=100、二类20+60=80、三类40+30=70）；`default_track` 存在；至少一个 track。
- `common_modifiers`：
  ```
  {
    "format_version": "common-modifiers-v1",
    "media_type_penalty": {"baseline": "real_photo",
       "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0}},
    "high_score_veto": {"threshold": 80, "cap_to": 79}
  }
  ```
  校验：penalties 四键齐全、值为 <=0 整数、baseline penalty 必须为 0；veto threshold/cap_to 为 0..100 整数且 cap_to<threshold。

必做函数：
- `validate_category_evaluation_contract(contract: dict) -> None`：逐块 fail-closed，抛 `CategoryEvaluationContractError`（继承 ValueError，带 `.code`）。对 `redline_policy` 委托任务1的校验器。
- `canonical_contract_hash(contract: dict) -> str`：对合同做**稳定** canonical JSON 后 sha256 十六进制。复用现有 `dimension_schema_registry.canonical_hash` / `canonical_json` 如果签名匹配；否则本文件内实现等价 canonical（sort_keys、separators 紧凑、ensure_ascii=False）。要求：键顺序无关、同义结构同 hash。
- 不做任何 DB / 文件 / 网络 / 模型调用。

## 任务 3 & 4：单元测试

- `backend/tests/test_redline_policy.py`：覆盖——合法策略命中/未命中；enabled=false 直通；exemption 生效；非法 signal / 空 rules / 重复 key / 非法 reason 枚举 / 非法 hit_level / 越界 hit_score_cap 全部抛错；确定性（同输入多次同输出）。
- `backend/tests/test_category_evaluation_contract.py`：覆盖——合法 v3 合同通过；三块各自的非法用例 fail-closed；base+dim<=cap 边界；canonical hash 键顺序无关且同结构一致、不同结构不同。
- 测试只 import 上述两个新模块和（只读）`schema_adapter`；不依赖 DB、网络、真实模型。
- 断言用例请覆盖灵感图三赛道的真实数值（40/60/100、20/60/80、40/30/70）和四类红线（截图/随手拍/大面积文字/二维码）。

## 完成信号

在 worktree 根写一个 `ADR33_PHASE1_DONE.md`，列出：你新建的文件清单、每个模块导出的函数/常量名、你自检过的关键用例点（不运行，只列你设计覆盖的场景）。写完即停，等 OpenClaw 侧跑测试与验收。
