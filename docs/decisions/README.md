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

## 新增 ADR 的规则

文件名格式：

```text
NNNN-short-kebab-case-title.md
```

每条至少包含：状态、日期、背景、决策、后果和不可破坏约束。已经接受的决策若要改变，应新增 ADR 并把旧记录标记为 `Superseded by ADR-NNNN`，不要直接改写历史理由。
