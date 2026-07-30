# 架构决策记录（ADR）

本目录保存会影响后续开发方向的长期决策。聊天记录不是架构事实来源；重要取舍必须落在这里。

## 状态含义

- `Proposed`：已提出，尚未正式采用。
- `Accepted`：当前必须遵守。
- `Superseded`：已被新的 ADR 替代，保留历史原因。
- `Deprecated`：不再推荐，但尚可能存在历史实现。

## 当前索引

| ADR | 状态 | 主题 |
|---|---|---|
| [0001](0001-separate-assets-and-evaluations.md) | Accepted | 素材与评测运行分离 |
| [0002](0002-two-stage-evaluation-and-server-scoring.md) | Accepted | A/B 两阶段评测与服务端评分 |
| [0003](0003-immutable-versioned-model-prompt-combinations.md) | Accepted | 模型与提示词独立版本化、结果固定组合快照 |
| [0004](0004-human-truth-and-golden-regression.md) | Accepted | 人工真值与黄金样本回归 |
| [0005](0005-local-first-data-and-secret-security.md) | Accepted | 本地优先数据与密钥安全 |
| [0006](0006-risk-based-stable-review-sampling.md) | Accepted | 风险优先、稳定可解释的审核抽样 |
| [0007](0007-optional-single-prompt-and-server-scoring.md) | Accepted | 可选单提示词评测与服务端权威评分 |
| [0008](0008-cap-casual-and-damaged-images-at-l2.md) | Accepted | 随拍图与画质受损图片最高 L2 |
| [0009](0009-p0e-safe-offline-import-foundation.md) | Accepted | P0-E 安全离线导入、图片冻结与候选包基础（仅 E0/E1） |
| [0010](0010-p0e-e2-canary-run-state-machine.md) | Accepted | P0-E E2 金丝雀运行计划与状态机编排层 |
| [0011](0011-p0e-e3-canary-persistence-api.md) | Accepted | P0-E E3 金丝雀持久化、认证 API、幂等与乐观锁 |
| [0012](0012-macos-keychain-credentials.md) | Accepted | macOS Keychain 与跨平台版本化凭据引用 |
| [0013](0013-macbook-deployment-lifecycle.md) | Accepted | MacBook 首次安装、诊断、前台启动与脱敏灾备生命周期 |
| [0014](0014-staged-human-review-and-prompt-release-gate.md) | Superseded | 分阶段人工审核、历史纠偏预览与提示词发布前门禁 |
| [0015](0015-material-packages-panel-consensus-and-dual-pipeline.md) | Accepted | 素材包、初审组共识与实验台—生产平台双流水线 |
| [0016](0016-safe-automation-feedback-and-model-benchmark.md) | Accepted | 安全自动优化、不可变生产回流与三模型横评 |
| [0017](0017-windows-deployment-lifecycle.md) | Accepted | Windows 公司服务器安装、诊断、前台启动与脱敏灾备生命周期 |
| [0018](0018-adr16-real-executors-and-rollout.md) | Accepted | ADR-0016 真实执行器、预算结算与分阶段启用 |
| [0019](0019-session-derived-reviewer-identity.md) | Accepted | 审核身份由服务端登录会话派生 |

## 新增 ADR 的规则

文件名格式：

```text
NNNN-short-kebab-case-title.md
```

每条至少包含：状态、日期、背景、决策、后果和不可破坏约束。已经接受的决策若要改变，应新增 ADR 并把旧记录标记为 `Superseded by ADR-NNNN`，不要直接改写历史理由。
