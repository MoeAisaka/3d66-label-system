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
| [0020](0020-dimension-manager-and-variable-dimensions.md) | Accepted | 维度管理器、固定核心维与可变维度路由 |
| [0021](0021-material-package-lifecycle-and-baseline-package-selection.md) | Accepted | 素材包生命周期、素材入口合并与基准回归整包选择 |
| [0022](0022-baseline-prompt-version-selection.md) | Accepted | 基准回归手选提示词版本与维度版本预留 |
| [0023](0023-windows-server-dpapi-scope-and-health-gate.md) | Accepted | Windows Server DPAPI 显式存储范围、真实回环门禁与脱敏诊断 |
| [0024](0024-filename-level-explanation-and-guided-optimization.md) | Accepted | 文件名建议等级、可解释基准回归与连续安全优化工作流 |
| [0025](0025-category-isolated-pipelines-pdf-preprocess-and-generic-models.md) | Accepted | 多类目隔离流水线、PDF 前处理与通用模型渠道 |
| [0026](0026-automatic-category-isolated-optimization-and-asset-management.md) | Accepted | 类目隔离自动优化与素材管理 |
| [0027](0027-account-rbac-model-registry-and-docker-persistence.md) | Accepted | 多人账号、统一模型管理与 Docker 持久化 |
| [0028](0028-admin-composable-category-pipelines.md) | Accepted | 管理员可组合的模块化类目流水线 |
| [0029](0029-unified-label-production-and-consumption-contract.md) | Accepted | 统一标签生产、版本化发布与下游消费合同 |
| [0030](0030-package-review-and-category-baseline-promotion.md) | Accepted | 评测包二审、显式发布与类目基线原子提升 |
| [0031](0031-category-dimension-management-and-prompt-only-mode.md) | Accepted | 类目维度管理、不可变版本与仅提示词评测模式 |
| [0032](0032-production-fields-and-large-baseline-throughput.md) | Accepted | 生产消费字段闭环与 10000 张基准回归吞吐合同 |
| [0033](0033-category-custom-evaluation-base-and-redline.md) | Accepted | 按类目自定义评测底座（红线+分类+维度）与 L 等级方向校正 |
| [0036](0036-inspiration-aesthetic-foundation-before-rules.md) | Accepted | 灵感图美感基础事实前置固化，规则层只读定级 |
| [0037](0037-inspiration-quality-gates-and-brand-wordmark-exemption.md) | Accepted | 灵感图锚点校准、随手拍质量闸门与品牌字样窄豁免 |

## 新增 ADR 的规则

| [0034](0034-v3-rule-deduction-and-node-corrections.md) | Accepted | v3 规则扣分、节点纠偏与媒介开关 |
| [0035](0035-proposal-pdf-paged-input-channel.md) | Superseded | PDF 方案文本全页分批扫描与确定性代表页通道 |
| [0038](0038-proposal-pdf-document-level-grading.md) | Accepted | PDF 源文档级聚合、恢复与评分合同 |
| [0039](0039-unified-level-semantics-and-category-scale.md) | Accepted | 统一 L1 最优语义与类目级启停档位 |
| [0040](0040-level-scale-and-thinking-control.md) | Accepted | 类目等级档位与模型 thinking 控制 |
| [0041](0041-label-mechanism-v1-execution-contract.md) | Accepted | 标签机制双发布轴、存量重跑与统一模型管理 |
| [0042](0042-unified-labellab-product-carrier.md) | Accepted | TPENG 标签实验台作为标签/内容中台重构统一产品载体 |
| [0043](0043-canonical-facts-and-semantic-projection-boundaries.md) | Accepted | TPENG 中台 Canonical 事实与资产语义投影边界 |
| [0044](0044-frontend-workspaces-and-mechanism-profile-plugins.md) | Accepted | TPENG 标签实验台工作区与机制 profile 插件边界 |
| [0045](0045-dual-workspaces-and-table-projection-contract.md) | Accepted | TPENG 标签实验台双工作区与数据库表投影合同 |
| [0046](0046-model-3d-su-evaluation-mechanism.md) | Accepted | 3D & SU 模型美感评测机制独立类目与三赛道合同 |
| [0047](0047-platform-semantic-tag-demand-contract.md) | Accepted | 平台级语义标签需求合同与 3D/SU 首验证切片 |

文件名格式：

```text
NNNN-short-kebab-case-title.md
```

每条至少包含：状态、日期、背景、决策、后果和不可破坏约束。已经接受的决策若要改变，应新增 ADR 并把旧记录标记为 `Superseded by ADR-NNNN`，不要直接改写历史理由。
