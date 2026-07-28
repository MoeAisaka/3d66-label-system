# ADR-0010：P0-E E2 金丝雀运行计划与状态机编排层

- 状态：Accepted
- 日期：2026-07-28

## 背景

P0-E E0/E1（ADR-0009）建立了安全 XLSX 预检、受控图片冻结和确定性候选包三个底层原语。这些原语各自独立，缺少一个将它们串联起来、显式追踪进度、强制门控顺序并防止跳跃和回退的编排层。

E2 的目标是在 E0/E1 产物上加一个确定性、fail-closed 的运行计划与状态机，使以下合同得到机器可验证的保障：
- 所有门控必须按顺序通过，不允许跳跃或回退；
- 所有证据必须显式提供，不允许推断；
- 整个阶段不产生真实下载、模型调用、业务数据库写入、Gold 形成或发布。

## 决策

### 模块位置

新增 `backend/app/p0e_canary_run.py`，作为纯函数编排模块。所有对外转换函数均为纯函数（无 I/O、无全局状态），调用方提供所有证据。

### 状态机

状态（单调推进，不允许跳跃或回退）：

```
draft
→ preflight_ready
→ approvals_ready
→ freeze_ready
→ candidate_ready
→ human_review_ready   （终止成功态）
→ failed               （任意非终止态可触发，终止失败态）
→ cancelled            （任意非终止态可触发，终止取消态）
```

`human_review_ready`、`failed`、`cancelled` 均为终止态；E2 不从终止态恢复。

### 门控证据合同

| 转换 | 必要证据 |
|---|---|
| draft → preflight_ready | E1 XLSX 预检输出：识别的 schema_version + 以 `p0e:` 开头的 batch_key |
| preflight_ready → approvals_ready | 人工审批件：`human_approved=True`（显式布尔）、非空 `approved_by`、与预检 batch_key 一致的 `batch_key`；`applied_mappings` 为列表（可空）；静默映射（`human_approved` 非 True）无条件拒绝 |
| approvals_ready → freeze_ready | 获取配置：非空已规范化主机白名单 + `pinned_https_attested=True`（显式布尔）；通用 HTTP 不足以通过门控 |
| freeze_ready → candidate_ready | E1 manifest：识别的 `manifest_version`、`complete=True`、`expected_source_count == frozen_source_count`、无错误、至少一个资产 |
| candidate_ready → human_review_ready | E1 候选包：识别的 `schema_version`、`complete_for_requested_preview=True`、`selected_count` 与计划 `target_size` 严格相等、`forms_gold=False`、`downloads_performed=False`、`model_runs_performed=False`；人工审核交接件：`all_items_require_review=True`、`no_truth_or_gold_granted=True`、`item_count` 与 `selected_count` 严格相等 |

### URL 安全扫描

所有证据在被接受进入快照前，递归扫描其中所有 HTTP/HTTPS URL 字符串：含 query、fragment 或 userinfo 的 URL 直接拒绝，返回 `EVIDENCE_URL_CONTAINS_QUERY`、`EVIDENCE_URL_CONTAINS_FRAGMENT` 或 `EVIDENCE_URL_CONTAINS_USERINFO`。

### 幂等性

- `run_id` 由计划内容的 SHA-256 指纹稳定推导；相同计划参数产生相同 `run_id`。
- `snapshot_fingerprint` 由计划 + 累积证据的规范 JSON 推导；相同输入序列产生相同指纹。
- 所有转换函数为纯函数，相同输入严格产生相同输出快照。

### 机器可读拒绝

`CanaryRunError` 携带 `CanaryRunIssue`，包含稳定字段：`code`（稳定机器可读代码）、`message`（人类可读原因）、`current_state`、`attempted_transition`、`retryable`。所有 `CanaryRunIssue` 均可通过 `as_dict()` 序列化为 JSON。

### 快照不变量

每个 `RunSnapshot` 显式记录以下五项不变量，E2 全程为 False：
- `writes_business_database=False`
- `downloads_performed=False`
- `model_runs_performed=False`
- `forms_gold=False`
- `publishes_release=False`

## 后果

- E2 编排层与 E0/E1 原语解耦；调用方可独立构造证据并逐步推进状态机。
- 跳跃门控、回退、静默映射、不完整 manifest、不足候选预览、未签名审批等均产生清晰机器可读拒绝，不进入静默失败路径。
- 候选预览仍不是 Gold；状态机终止于 `human_review_ready` 并显式记录"每张图片均需人工审核、未授予任何真值"。
- 未引入通用 `Pipeline`、`Candidate` 或业务评估实体；未改动数据库、API 或前端；未改变既有 `Asset 1:N EvaluationResult`、`evaluation_id` 审核或任何 P0-A/B/C.1/D 合同。

## 不可破坏约束

- 不得从终止态恢复运行；`failed` 和 `cancelled` 不得被报告为 `human_review_ready`。
- 不得推断或静默应用 `farmat → format` 等映射；映射只能通过显式带 `human_approved=True` 的人工审批件引入。
- 候选预览不得声明 `forms_gold=True`、`downloads_performed=True` 或 `model_runs_performed=True`；任何违反均使转换被拒绝，而非静默接受。
- 证据中含有不安全 URL（带 query/fragment/userinfo）时必须拒绝，不得记录到快照。
