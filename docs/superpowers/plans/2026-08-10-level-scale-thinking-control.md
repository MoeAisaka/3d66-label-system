# LabelLab 等级档位与 Thinking 控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在测试环境提供可审计的类目等级档位配置和豆包 thinking 控制，默认行为保持兼容。

**Architecture:** 以 `CategoryEvaluationV3Config.contract` 为等级合同真值，保留 830188a 的统一语义并把更完整的等级校验能力移植进来；模型配置新增显式 `thinking_mode`，Doubao client 在 provider 边界做参数映射；前端在既有配置页面增加紧凑表单面板并复用 API 的 revision/hash 机制。

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, Pydantic, React + TypeScript, existing UI primitives, pytest, Vite.

---

### Task 1: 保存基线与导入草稿能力

**Files:**
- Modify: `backend/app/level_scale.py`
- Modify: `backend/app/category_evaluation_aggregator.py`
- Modify: `backend/app/category_evaluation_contract.py`
- Test: `backend/tests/test_level_scale.py`
- Test: `backend/tests/test_category_evaluation_aggregator.py`

- [ ] 将草稿中的 `enabled`、展示名校验、`assert_level_enabled`、`to_threshold_table` 合并到 830188a 结构；保留 `category-level-scale-v1` 版本号、L1-L5 完整声明和旧阈值只读兼容。
- [ ] 先运行现有等级/合同测试，确认合并前失败点，再补覆盖关闭 L5、关闭中间档、红线引用关闭档和旧合同兼容的失败测试。
- [ ] 用最小实现通过定向测试，确认停用档不会出现在 `thresholds` 和聚合结果中。

### Task 2: 等级档位 API

**Files:**
- Modify: `backend/app/category_evaluation_v3_config_api.py`
- Modify: `backend/app/main.py`（仅在既有路由注册需要时）
- Test: `backend/tests/test_category_evaluation_v3_config_api.py`

- [ ] 增加 `GET /api/category-evaluation/v3-config/{config_id}/level-scale`，返回规范化合同、revision、contract_hash 和可编辑字段。
- [ ] 增加 `PUT`，要求 `expected_revision` 与可选 `expected_contract_hash`，在事务内合并 `level_scale`、调用现有合同校验、递增 revision、重算 hash、追加审计。
- [ ] 非法切点、无 0 分兜底、关闭红线命中档和 revision 冲突分别返回稳定 422/409，且数据库不变。

### Task 3: ModelConfig thinking 合同与 Doubao 映射

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`（模型配置 schema/payload）
- Modify: `backend/app/doubao.py`
- Modify: `backend/app/strategy_bundle.py`
- Test: `backend/tests/test_doubao.py`（若不存在则创建）
- Test: `backend/tests/test_model_config_api.py`（按现有模型 API 测试文件复用/创建）

- [ ] 先写 payload 失败测试：`auto` 不覆盖 provider 默认，`enabled`/`disabled` 生成 `extra_body.thinking.type`，OpenAI provider 不受影响，未知值 fail-closed。
- [ ] 增加 `thinking_mode` 字段默认 `auto`，兼容历史 schema 默认值；模型配置读写和策略快照包含该字段但不输出密钥。
- [ ] 在非 OpenAI provider 的 `_generation_options` 中使用 provider-specific `thinking` payload，不再发送无效的 `reasoning_effort`；保留内部 trace 的语义字段以便回放。
- [ ] 将实验模式写入 provider trace/strategy snapshot，确保一次评测可追溯到实际模式。

### Task 4: 前端等级与 thinking 面板

**Files:**
- Modify: `frontend/src/pages/category-evaluation-v3-config-page.tsx`
- Modify: `frontend/src/pages/model-page.tsx`
- Modify: `frontend/src/lib/types.ts`
- Test: `frontend/scripts/check-category-evaluation-v3-config.ts`（新增确定性契约检查）

- [ ] 复用现有页面布局，增加 L1-L5 行式编辑器：启用开关、0-100 整数切点、展示名；显示 L1 最优/L 序号越大越差的固定语义。
- [ ] 保存前调用 API 校验；禁用 L5 时明确提示 L4 必须接管 0 分兜底，红线档引用关闭档显示错误，不允许提交。
- [ ] 在模型配置表单增加 `thinking_mode` segmented control（自动/开启/关闭），默认选中自动，提示实验用途和生产默认不变。
- [ ] 完成 loading、保存中、422/409、成功状态；保持高密度、无装饰性卡片嵌套、移动端不溢出。

### Task 5: 验证、工件与测试服部署

**Files:**
- Create: `docs/decisions/0040-level-scale-and-thinking-control.md`
- Create: `docs/superpowers/receipts/2026-08-10-level-scale-thinking-control.md`

- [ ] 跑后端定向测试、全量测试、`compileall`、前端 `tsc -b`/`npm run build`、`git diff --check`。
- [ ] 创建公布 `refs/remotes/origin/main` 的 bundle，先在 MacBook 隔离 worktree 保存现状并三方核对，再用受保护脚本部署测试环境。
- [ ] 验收健康 200、数据库 integrity/FK/migration、容器进程、revision/hash、前端实际可见配置；不调用真实业务评测，不改生产。
- [ ] 保存部署回执、bundle SHA 和回滚 ref；thinking 默认值验证为 `auto`。
