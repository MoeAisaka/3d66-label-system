# PDF 方案文本正式送审通道修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已部署的首 20 页长图式最小通道修正为 Owner 冻结的文本优先、A 全本分批、B 代表页采样通道。

**Architecture:** 保留既有 `prepare_pdf_model_input` 供通用 PDF 类目使用，新增 proposal 专用逐页预处理器，避免回归。A/B 编排拆进 `proposal_text_pdf_channel.py` 的小函数与 worker 薄接线；所有选择、合并、停止和 token 汇总均确定性且可单测。

**Tech Stack:** Python 3.12、pypdf、PyMuPDF、Pillow、pytest、现有 DoubaoClient

---

### Task 1: 冻结合同与 RED 测试

**Files:**
- Modify: `backend/app/proposal_text_assets/v3_contract_proposal_text_v1.json`
- Modify: `backend/tests/fixtures/proposal_text_contract_v1.json`
- Modify: `backend/tests/test_proposal_text_contract.py`
- Create: `backend/tests/test_proposal_text_pdf_channel.py`

- [ ] 在合同和 fixture 添加同一 `pdf_input_channel` 块：禁止长图、A batch=16/max_side=1024、B sample=16/high_fidelity、文本层优先、冲突人工复核。
- [ ] 增加合同变更拒绝测试。
- [ ] 增加 A 32 页分两批、首批红线停止、跨批冲突人工复核、B 代表页上限与确定性测试。
- [ ] 运行专项测试，确认因实现缺失而 RED。

### Task 2: 专用逐页预处理器

**Files:**
- Modify: `backend/app/media.py`
- Test: `backend/tests/test_media.py`
- Test: `backend/tests/test_proposal_text_pdf_channel.py`

- [ ] 新增不可变逐页输入结构，记录页码、文本来源、A 低清图路径和目录。
- [ ] 逐页原生文本全量抽取；仅空文本页 OCR。
- [ ] A 图片按长边 1024 独立渲染并内容寻址缓存，禁止生成接触表。
- [ ] 新增按选定页码高保真渲染 B 图片的函数。
- [ ] 运行专项测试确认 GREEN。

### Task 3: 多图客户端与 A/B 编排

**Files:**
- Modify: `backend/app/doubao.py`
- Create: `backend/app/proposal_text_pdf_channel.py`
- Modify: `backend/tests/test_doubao.py`
- Test: `backend/tests/test_proposal_text_pdf_channel.py`

- [ ] 为文本模式新增多图调用，保留 usage 与原始响应；不启用 provider 结构化改写。
- [ ] 实现 16 页顺序分批、每批一次校验失败重试、红线立即停止。
- [ ] 实现红线并集、普通字段先见优先/冲突人工复核、批次图像计数求和。
- [ ] 实现基于目录、逐页文本和 A 图像统计的确定性代表页选择与文本摘要。
- [ ] 汇总 A/B input/output/total token；无 usage 时字段保持 null 并显式标记不可测。
- [ ] 运行专项测试确认 GREEN。

### Task 4: Worker 薄接线与回归

**Files:**
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/test_proposal_text_integration.py`
- Modify: `PROJECT_STATUS.md`

- [ ] proposal 型材改走专用通道；其他 PDF 与灵感图继续走原路径。
- [ ] 保存批次审计、代表页、停止原因和 token 到预处理/执行快照。
- [ ] 运行 proposal 专项、灵感图专项、全量后端测试、compileall、前端 lint/build 和 `git diff --check`。
- [ ] 提交代码，生成并验证小于 200KB 的新 bundle。

### Task 5: 部署与报告

**Files:**
- Update outside repo: `/Users/Shared/OpenClaw/142-实现-标签实验台PDF方案文本二期接入-20260806/README.md`
- Update outside repo: pytest/deployment/query logs and bundle

- [ ] 每次部署前检查 worktree 根目录 `ABORT-NOTICE.txt`。
- [ ] 使用受保护脚本部署，验收 health 200、容器 healthy、proposal 合同/提示词可查、inspiration spec 不变。
- [ ] 在不占阶段 1c 预算的独立凭据/窗口下跑一份真实 PDF，记录 A/B token；无法满足隔离条件时 fail-closed 报告缺口。
- [ ] 复核报告、日志和 bundle 不含凭据。

