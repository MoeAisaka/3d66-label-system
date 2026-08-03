# 标签实验台 交付总结（2026-08-03 夜间自主批次）

分支：`fix/baseline-fields-throughput-20260803`（已推 hub + codeup；**未合 main、未部署生产**）
执行：OpenClaw 中枢（本会话 `tepeng-claude/claude-fable-5`，Owner 手选 Fast）控制 + 验收；重活委派 MacBook-Company `claude-fable-5`，OpenClaw 逐件审代码、回传权威树重建、跨平台验证、提交。

---

## 一、本批次交付内容

### 1. 并发提速（已实机验证）
- 主评测模型默认并发 `2 → 8`（migration 51，只抬旧默认值，保留操作员自定义）。
- 依据：真实火山方舟金丝雀 —— 无限流，瓶颈是单次 24-27s 延迟，并发 8 为吞吐拐点（吞吐 ~2.9x）。

### 2. 生产消费字段闭环 + 10000 张基准回归吞吐
- 标准评分合同支持全部生产字段（title/seotitle/category/style/tags/cons/design/score/reason/image_defects/trait/image_quality/media_form），严格枚举/长度/结构校验。
- 基准回归单批上限 1000 → 10000；结果/素材分页加载，运行期不再全量传输。

### 3. ADR-0033 类目自定义评测底座（框架层 100% 完成）
权威流水线：**红线筛查(直筛L5) → 分类器(类目→子类目) → 子类目提示词A/B → 维度评测(共性维度+特有维度) → 产出(等级+分数+固定字段)**。

七个纯函数模块（全部确定性、可回归、无 IO/网络/DB/模型调用）：
| 模块 | 职责 |
|---|---|
| `redline_policy.py` | 红线（可自由增删/开关/无数量限制，命中→L5 淘汰、封顶 49） |
| `category_evaluation_contract.py` | v3 合同骨架（赛道/子类目 + 固定通用维度 + canonical hash） |
| `category_evaluation_aggregator.py` | 确定性聚合器（红线→赛道→维度扣分→媒介降权→高分压分→封顶→L等级；**doc-l5-worst 语义**） |
| `dimension_grade_bridge.py` | grade(1-5) → deduction 桥（扣分制，可回归） |
| `dimension_composition.py` | 共性维度 + 特有维度组合（两组均可自由增删、可为 0，空组重归一化，双空=仅提示词） |
| `subcategory_resolver.py` | 分类器信号 → 子类目 track 解析（置信门 + 兜底） |
| `inspiration_category_seed.py` | 灵感图端到端样板 + `evaluate_one` 编排器 |

### 4. 前端
- **A/B/维度边界说明组件**：提示词管理器、维度管理器嵌入固定用法说明。
- **类目评测预览页**（`/workflow/optimization/category-evaluation-preview`）：查看灵感图 v3 合同 + 干跑单图评测。

### 5. 只读/干跑预览 API（隔离、无副作用）
- `GET /api/category-evaluation/preview/inspiration/contract`（读取装配好的合同）
- `POST /api/category-evaluation/preview/inspiration/evaluate`（干跑评测，grades 为入参）
- `POST /api/category-evaluation/preview/validate`（校验合同/映射/维度配置）
- 全部登录鉴权、无 DB/队列/发布/模型副作用。

---

## 二、关键概念澄清（Owner 提问归档）

- **调用A 没被去除，也没被重构成红线**。调用A 保留为"识别事实"层（分类信号/reason/trait/生产字段）；红线是**新增的确定性判定层**，消费调用A 的 `reason` 做淘汰；分类器消费 `classification.*`；媒介降权消费 `trait`。
- **L 方向**：以钉钉文档为准 **L5=最差、L1=最优**。新引擎带 `level_semantics_version="doc-l5-worst-v1"`；现有 `scoring.py`（L5=最优）**未改动**，两套语义靠版本号隔离。
- **两条流水线只差数据源**：完整流水线=增量未定性素材，简易流水线(基准回归)=存量已定性素材；**评测能力必须完全一致、走同一套引擎**。这是聚合器/合同作为唯一共享引擎的根据。

---

## 三、跨平台实机验证

| 环境 | 结果 |
|---|---|
| **Mac** | 后端全量 910 passed / 1 skipped；前端 build + tsc typecheck 通过 |
| **Docker/Linux** | 镜像 build + 容器 health ok + 预览 API 401 鉴权 + 鉴权后干跑 200(class_one/85/L1) + migration 51 + mc=8 + 8 workers |
| **Windows 真机(13600K SSH)** | 184 passed（含全部 ADR-0033 层 + 预览 API）+ DPAPI 实密往返 + 应用启动 200 |

密钥三路隔离（macOS Keychain / Windows DPAPI / Linux file-aead）实机确认互不串读。

---

## 四、未做（高风险，按 ADR-0033 独立门禁，留待 Owner 拍板）

这些会改动**生产执行路径**或**已发布消费标签**，夜间不擅自动：
1. **Phase 4 worker 接线**：把新链按合同版本接进 worker 唯一算分步（一次接入两条流水线）。
2. **L 方向全局翻转迁移**：`level_semantics_version` + 已发布 PublishedLabel 兼容 + 消费接口按版本解释 + 独立回归门禁（最高风险）。
3. **前端类目配置 CRUD**：红线/子类目/维度组的可视化编辑器（当前是预览+干跑，尚不可在线编辑落库）。

现状：新引擎已可通过预览 API 完整试跑，但**尚未替代线上评分**。接线与迁移建议带完整回归门禁、单独分支、Owner 在场时执行。
