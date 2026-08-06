# PDF 方案文本 v3 二期正式接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `proposal_text_pdf` 作为 `text-proposal-additive-v1` 型材接入 v3 权威主链并部署测试环境。

**Architecture:** 保留图像扣分型材的全部行为，通过 `profile_type` 在合同校验和权威执行器处分派。PDF 复用既有内容寻址预处理，确定性页数闸门优先；模型 A/B 只产出事实与三个分项，纯函数聚合器固化原始美感和、计算总分与等级，非法输出重试一次后进入人工复核。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Pydantic、pypdf/PyMuPDF、pytest、SQLite、Docker Compose

---

### Task 1: 合同与加法聚合纯函数

**Files:**
- Create: `backend/app/proposal_text_contract.py`
- Create: `backend/app/proposal_text_aggregator.py`
- Test: `backend/tests/test_proposal_text_contract.py`
- Test: `backend/tests/test_proposal_text_aggregator.py`

- [ ] 写失败测试：冻结 profile_type、A/B/C/balanced 上限、五档闭区间、六个红线枚举与非法额外字段。
- [ ] 运行专项测试，确认因模块不存在/行为缺失而 RED。
- [ ] 实现无 IO 的严格合同校验、A/B 输出校验、红线提取、加法聚合和等级映射。
- [ ] 运行专项测试，确认 GREEN；验证 `proposal_aesthetic_score` 在规则前固化且不可被红线/等级映射回写。
- [ ] 提交合同与纯函数。

### Task 2: PDF 确定性预检与一次重试闸门

**Files:**
- Create: `backend/app/proposal_text_pipeline.py`
- Modify: `backend/app/media.py`
- Test: `backend/tests/test_proposal_text_pipeline.py`
- Test: `backend/tests/test_media.py`

- [ ] 写失败测试：页数小于 15 不调用模型；A/B 非法各重试一次；第二次仍非法返回人工复核；不截断、不改写模型分数。
- [ ] 运行专项测试并保存 RED 证据。
- [ ] 实现确定性页数闸门、严格响应重试器和人工复核合成载荷。
- [ ] 保留原 PDF 不变；页图与文本继续内容寻址缓存，并记录总页数、渲染覆盖与文本上限。
- [ ] 运行专项测试并提交。

### Task 3: v3 权威 Worker 最小侵入式接线

**Files:**
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_proposal_text_worker_pipeline.py`
- Test: `backend/tests/test_worker_v3_authoritative.py`

- [ ] 写失败集成测试：PDF → 页数闸门 → A → 红线/人工闸门 → B → 引擎定级。
- [ ] 证明 RED 后，按 profile_type 分派新型材；图像类仍走原验证器、红线信号与扣分聚合器。
- [ ] 将提案型材结果映射到既有 `EvaluationResult`，保存原始 A/B 响应、冻结合同、`proposal_aesthetic_score`、score、level、状态与复核原因。
- [ ] 运行提案专项及灵感图权威专项并提交。

### Task 4: 类目、合同与提示词幂等入库

**Files:**
- Create: `prompts/call_a_proposal_text_v1.txt`
- Create: `prompts/call_b_proposal_text_v1.txt`
- Create: `backend/app/proposal_text_category_seed.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/category_pipeline.py`
- Modify: `backend/app/seed.py`
- Test: `backend/tests/test_proposal_text_seed.py`

- [ ] 写失败测试：新类目 active、PDF MIME、A/B 模式、指定版本、提示词全文 SHA-256、v3 spec/profile_type 可查询。
- [ ] 复制交付件提示词逐字入仓；任何哈希不一致均 fail-closed。
- [ ] 实现幂等 seed：只创建/更新本类目的系统种子，不改写任何灵感图配置、提示词或 revision。
- [ ] 运行 seed/API/上传专项并提交。

### Task 5: 全量验证、部署与交付

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create outside repo: `/Users/Shared/OpenClaw/142-实现-标签实验台PDF方案文本二期接入-20260806/README.md`

- [ ] 运行新增专项、全量后端 pytest、compileall、前端 lint/合同检查/build、`git diff --check`。
- [ ] 创建小于 200KB 的增量 git bundle，验证 bundle 可读且包含本分支全部提交。
- [ ] 每次部署尝试前检查 `ABORT-NOTICE.txt`；按受保护脚本两参数部署。
- [ ] 验收 health 200、容器 healthy、灵感图 active spec/revision/行为未变、新类目与两条提示词可查询。
- [ ] 若有不占阶段 1c 预算的 PDF 样本与可用测试凭据，跑一次完整冒烟；否则明确记录未执行原因。
- [ ] 将 pytest、部署、查询与健康证据写入 142 报告目录，最终复核无凭据泄漏。
