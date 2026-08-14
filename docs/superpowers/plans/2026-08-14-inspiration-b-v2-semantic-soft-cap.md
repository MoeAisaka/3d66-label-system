# 灵感图调用 B v2 与语义软封顶实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建未发布的灵感图候选，修复调用 B 八维方向和随手拍软封顶语义，并用固定 100 张基准集验证，不触发 2,200 张全量回归。

**Architecture:** 调用 B v2 是独立草稿提示词，仍输出同一冻结 JSON 结构。V3 新增候选专用的 `cap_to_level` 解释器，把质量上限映射到冻结分数档；旧合同继续读 `cap_to`。候选合同通过基准回归快照绑定新草稿提示词和新修订，现役投影保持 revision 9。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、pytest、SQLite、Docker 测试环境。

## 全局约束

- 只写入 `/Volumes/WorkSSD/Codex/2026-08-11/dan-qi` 及测试服务器已授权候选对象；禁止写入 `/Volumes/WorkSSD/OpenClaw/Codex`。
- 不修改人工真值、R0–R4、运行 20/23、现役 revision 9、现役提示词或模型配置。
- 不记录、复制、回显任何密钥、Cookie 或会话令牌。
- 候选保持 `draft/candidate`；未通过 100 张门禁不得启用、发布或启动 2,200 张。
- 技术失败单列，禁止伪造预测。

---

### 任务 1：为语义软封顶写失败测试

**文件：**
- 修改：`backend/tests/test_inspiration_quality_repair.py`
- 修改：`backend/app/inspiration_aesthetic_foundation.py`

**接口：**
- 消费：`aesthetic_foundation.casual_snapshot_soft_cap`。
- 产出：候选 `cap_to_level` 和 `filter_escalation` 的确定性分数上限与审计记录。

- [ ] **步骤 1：先写四个失败测试**

测试一构造新的 `cap_to_level=L4` 合同、分数 88 的随手拍，断言最终 75/L4 与 `resolved_cap_to=75`；测试二使两个升级维度均为 2，断言 59/L5；测试三只降低其中一个维度，断言仍为 75/L4；测试四复制旧合同，断言仍是 59/L4。

- [ ] **步骤 2：运行测试确认红灯**

运行：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" backend/.venv/bin/python -m pytest backend/tests/test_inspiration_quality_repair.py -q
```

预期：新合同因为 `soft_cap_invalid` 失败；旧合同测试继续通过。

- [ ] **步骤 3：实现最小的合同解释器**

在 `_validated_quality_rules` 中兼容且只兼容两种形状：历史 `cap_to`，或候选 `cap_to_level` 加完整 `filter_escalation`。新增纯函数从 `score_thresholds` 得到指定等级的最高合法分数。`apply_aesthetic_v3_rules` 只在命中随手拍时应用 L4 语义上限；仅当升级维度全部不高于阈值时，再应用 L5 语义上限并记录规则。

- [ ] **步骤 4：运行聚焦测试确认绿灯**

运行同一条 pytest 命令。

预期：所有旧/新语义测试通过。

### 任务 2：固化调用 B v2 草稿文本与合同校验

**文件：**
- 创建：`backend/prompts/inspiration_image_call_b_aesthetic_v6.txt`
- 修改：`backend/tests/test_inspiration_quality_repair.py`
- 创建：`runtime/regression/inspiration_b_v2_candidate_20260814.txt`
- 创建：`runtime/regression/inspiration_b_v2_candidate_20260814.json`

**接口：**
- 消费：五锚输入与八维 JSON 合同。
- 产出：可创建为草稿 `PromptVersion` 的完整系统提示词和其 SHA-256 元数据。

- [ ] **步骤 1：先写失败测试**

测试通过候选文本的可观察规则断言：明确 `grade=5` 优、`grade=1` 差；声明调用 B 不执行红线/最终等级；保留五锚顺序、普通清晰图不自动高分、八维独立证据、非空整体证据与不足项。

- [ ] **步骤 2：运行测试确认红灯**

运行：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" backend/.venv/bin/python -m pytest backend/tests/test_inspiration_quality_repair.py -q
```

预期：因为 v6 文本尚不存在或不满足新断言而失败。

- [ ] **步骤 3：写最小候选文本与元数据**

新文本只修正信息合同，不更改 JSON 字段。元数据写入提示词 SHA-256、固定模型、revision 11 的阈值、第五锚和“草稿、未激活”的状态；不含 API Key。

- [ ] **步骤 4：运行聚焦测试确认绿灯**

运行同一条 pytest 命令。

预期：候选文本和 V3 规则共同通过。

### 任务 3：回归、提交与测试服候选门禁

**文件：**
- 修改：`PROJECT_STATUS.md`
- 创建：`outputs/灵感图_调用Bv2与语义软封顶_100张门禁_20260814.md`

**接口：**
- 消费：测试服真实登录态、草稿调用 B、候选 V3 revision、固定 100 张基准集。
- 产出：未发布候选、单次 100 张运行及包含技术覆盖率的中文门禁报告。

- [ ] **步骤 1：运行本地相关回归与全量后端回归**

运行：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" backend/.venv/bin/python -m pytest \
  backend/tests/test_inspiration_quality_repair.py \
  backend/tests/test_inspiration_aesthetic_foundation.py \
  backend/tests/test_worker_v3_authoritative.py \
  backend/tests/test_baseline_regression.py -q
```

再运行：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" backend/.venv/bin/python -m pytest backend/tests -q
```

- [ ] **步骤 2：提交只含本任务的代码与文档，受保护流程部署测试服**

先核验服务器提交、容器健康、数据库完整性、无运行中基准队列与现役投影 hash；使用受保护部署脚本，仅传入本次 Git bundle 和提交。部署失败由脚本回滚上一提交。

- [ ] **步骤 3：通过已有登录态创建草稿提示词和候选修订**

草稿调用 B 的 version 固定为 `inspiration-b-v2-grade-direction-20260814`；候选修订从 revision 11 派生，唯一改动是调用 B 绑定与 `casual_snapshot_soft_cap` 新形状。立即读取回显，核验草稿、候选、祖先 revision 9 和现役投影未变。

- [ ] **步骤 4：创建固定 100 张候选回归并等待终态**

只使用基准集 #8 和 `candidate_revision_id`。轮询仅读取状态；完成后验证 SQLite `integrity_check`、`foreign_key_check`、运行快照中的模型/提示词/修订摘要、有效预测数和技术失败条目。

- [ ] **步骤 5：生成中文门禁报告并决定是否停止**

报告五档精确率、相邻准确率、L1 精确率、L5 召回率，三档推荐/常规/过滤准召，技术覆盖率，运行 20/23/新运行配对差异，以及 L1/L2、L4/L5 重点样本。若任一既定门禁不通过，明确写“停止全量回归”；不启用候选。
