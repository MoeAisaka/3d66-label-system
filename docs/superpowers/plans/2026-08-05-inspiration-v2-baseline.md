# 灵感图人工校准版 v2 与基线测量实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `inspiration_image` 的 active v3 合同替换为 2026-08-05 人工校准版，发布新调用 B 提示词，冻结人工文件名前缀黄金集，并取得真实新引擎基线指标与部署证据。

**Architecture:** 保持 ADR-0033/0034 的“模型判规则、服务端确定性计分”边界；合同存原始业务权重（0.60/0.30），桥接层兼容旧归一化权重并保证 `Σ维度分×原始权重`。启动种子以 `spec_version` 幂等升级已有灵感图配置、保留旧评测快照；黄金集继续跨类目引用 `asset_id`，绝不改 `Asset.category_key`。

**Tech Stack:** Python 3.14、FastAPI、SQLAlchemy/SQLite、pytest、React/Vite、Docker、Git bundle、现有基线回归 Worker。

---

### Task 1: 冻结评分合同的可执行测试

**Files:**
- Modify: `backend/tests/test_inspiration_category_seed.py`
- Modify: `backend/tests/test_dimension_deduction_aggregator.py`
- Modify: `backend/tests/test_category_evaluation_aggregator.py`
- Modify: `backend/tests/test_media_penalty_toggle.py`

- [ ] **Step 1: 写业务权重手算失败测试**

新增一类维度分 `[80,70,60,90,50,40]` 的规则命中输入，断言维度池为 `38`、最终为 `78`；同时断言合同显示权重精确为 `0.10/0.10/0.05/0.10/0.10/0.15`。

- [ ] **Step 2: 写边界失败测试**

断言红线命中总分不高于 20 且 L5；高分硬伤压至 79；80→L2、81→L1；媒介关闭后 AI/实景得分一致。

- [ ] **Step 3: 运行定向测试确认 RED**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_inspiration_category_seed.py backend/tests/test_dimension_deduction_aggregator.py backend/tests/test_category_evaluation_aggregator.py backend/tests/test_media_penalty_toggle.py -q`

Expected: 当前旧红线 49、旧阈值 80→L1、旧媒介降权与占位规则导致断言失败。

### Task 2: 实现人工校准版合同与权重兼容

**Files:**
- Modify: `backend/app/inspiration_category_seed.py`
- Modify: `backend/app/dimension_deduction_bridge.py`
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/app/category_evaluation_aggregator.py`

- [ ] **Step 1: 替换红线、赛道和等级阈值**

设置红线 cap=20；三赛道维持 40+60/20+60/40+30；合同显式阈值为 81/61/41/21/0。

- [ ] **Step 2: 精确写入 6+5 维度规则**

逐条写入用户冻结的中文描述、扣分值与原始权重；删除通用占位规则对灵感图配置的使用。

- [ ] **Step 3: 兼容原始权重计分**

规则桥同时接受旧 `sum(weight)=1` 与新 `sum(weight)=dimension_max/100`：新口径每维 share=`weight×100`，旧口径 share=`weight×dimension_max`，两者都不得再改变既有配置结果。

- [ ] **Step 4: 实现可配置 10 条高分硬伤与媒介关闭**

`high_score_veto.enabled=true`、`rules` 固定 10 条，聚合器只在开启且命中配置硬伤时压 79；`media_type_penalty.enabled=false`。

- [ ] **Step 5: 运行定向测试确认 GREEN**

Run: 同 Task 1 Step 3。

Expected: 全部通过。

### Task 3: 发布并绑定新调用 B

**Files:**
- Modify: `prompts/inspiration_image_call_b.txt`
- Modify: `backend/app/seed.py`
- Modify: `backend/tests/test_prompt_loader.py`
- Modify: `backend/tests/test_migration.py`

- [ ] **Step 1: 写提示词版本与幂等升级失败测试**

断言新增 `inspiration-b-v2-human-calibrated-20260805` published 版本，旧 `inspiration-b-v1` 不覆盖；已有 active 灵感图 config 按 `spec_version` 升级且 revision 递增，四类目状态保持灵感图 active、其余 draft。

- [ ] **Step 2: 写完整中文调用 B 提示词**

包含权威评审角色、红线/赛道/11维规则/10条压分/等级/标签命名和严格 JSON 字段；合同写 `prompt_bindings.call_b_version`。

- [ ] **Step 3: 实现启动幂等替换**

种子发现旧灵感图配置时仅当 `spec_version` 落后才替换三个 JSON、规则镜像、hash、media flag、status 与 revision；不回写任何旧 `EvaluationResult`。

- [ ] **Step 4: 运行提示词/种子/迁移测试**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_prompt_loader.py backend/tests/test_migration.py backend/tests/test_category_evaluation_v3_config_api.py -q`

Expected: 全部通过。

### Task 4: 黄金集与测量工具

**Files:**
- Modify: `backend/app/inspiration_auto_correction.py`
- Modify: `scripts/inspiration_golden_workflow.py`
- Modify: `backend/tests/test_inspiration_auto_correction.py`

- [ ] **Step 1: 写文件名前缀解析与不改类目测试**

覆盖 `好图补充/好_*.jpeg`、目录/下划线组合、五档映射；断言黄金集 `category_key=inspiration_image`、asset id 引用、快照 `truth_updated_by=灵感图人工评级前缀`，原资产类目不变。

- [ ] **Step 2: 增加小批抽样与报告命令**

新增只读 `sample-report` 和 `metrics` 输出：每档确定性抽样、实际 score/level/track/规则扣分、整体命中率、混淆矩阵、按档准确率与净偏差。

- [ ] **Step 3: 运行黄金集测试**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_inspiration_auto_correction.py backend/tests/test_baseline_regression.py -q`

Expected: 全部通过。

### Task 5: 全量回归、三平台与部署验收

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create: `/Users/Shared/OpenClaw/119-验收-灵感图v2体系重配与基线测量-20260805/*`

- [ ] **Step 1: 本机全回归与构建**

Run: `python -m pytest tests -q`、`npm run build`、Docker compose 健康检查；保存完整日志。

- [ ] **Step 2: 生成 bundle 并通过固定命令发布**

Push hub/main；MacBook 可用时同步 Codeup/main。创建 git bundle，scp 到 `192.168.1.35`，执行 `sudo -n /usr/local/sbin/deploy-3d66-label-test <bundle> <commit>`；核对 `/health` 200、commit 一致、四类目状态。

- [ ] **Step 3: 建黄金集并先小批后全量**

在测试环境创建/复用 2285 条人工真值集；先每档抽样 4-8 张再启动全量 structured baseline run。真实模型 key 可用则记录真实模型版本与输出；不可用则只报告桩/无法完成，不计算伪准确率。

- [ ] **Step 4: 生成核心报告和截图**

写验收报告、基线准确率报告、小批样例、config 状态/截图、pytest/前端/Docker/部署日志与 commit 清单。

- [ ] **Step 5: 最终验证后提交**

Run: `git diff --check`、全 pytest、前端 build、Docker health、远端 health/commit/config、报告 JSON 交叉核对。

Expected: 所有可执行门禁通过；任何外部模型/节点阻塞在报告中明确标红，不伪造数字。
