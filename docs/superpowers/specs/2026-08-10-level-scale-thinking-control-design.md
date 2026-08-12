# LabelLab 等级档位与豆包 Thinking 控制设计

## 目标

让测试环境可以通过现有类目配置合同配置启用档位、分数切点和展示名，并能显式控制豆包是否开启深度思考，支持后续在真实样本上做 `auto/enabled/disabled` 对照。默认行为保持向后兼容：未配置时仍为 L1 最优、L5 最差，模型调用仍使用 `auto`。

## 边界

- `CategoryEvaluationV3Config.contract` 是等级档位唯一真值；不新增等级专用表。
- API 只提供合同的版本化读写视图，复用现有 v3 配置权限、revision、hash、校验和审计。
- `ModelConfig` 增加 `thinking_mode`（`auto|enabled|disabled`），默认 `auto`；配置快照同步记录该值。
- 豆包请求使用火山方舟 `extra_body.thinking.type`，不再把豆包专属控制误写成 OpenAI `reasoning_effort`。
- 生产调用默认仍 `auto`，只有实验配置显式设为 `enabled` 或 `disabled` 才改变请求。
- 前端复用现有 v3 配置页和模型配置页，使用现有表单组件与状态色，不增加第三方依赖。

## 数据流

1. v3 配置页读取合同；后端 `resolve_level_scale` 规范化档位，校验顺序、0 分兜底、启用档引用和展示名。
2. 保存时在一个事务中校验合同、递增 revision、计算 hash、写审计事件；失败不写半成品。
3. worker 读取冻结的模型配置快照，创建 Doubao client；`thinking_mode` 映射为 `auto`（不发覆盖参数）、`enabled` 或 `disabled`。
4. provider trace 保存请求侧的有效 thinking 模式和模型配置版本，便于实验回放。

## 失败与回退

- 非法等级合同返回稳定错误码，HTTP 422，不改变数据库。
- 并发保存沿用现有 revision/hash 乐观校验；重复提交同一内容不制造新业务结果。
- 未知 `thinking_mode` fail-closed，不调用模型。
- 豆包不支持 thinking 参数时，`auto` 继续可用；显式 `enabled/disabled` 调用失败并记录可诊断错误，不静默回退到另一模式。
- 本轮只部署测试环境，不修改生产配置或生产数据库。

## 验收

- 后端：等级合同新增能力、旧 `level_thresholds` 兼容、红线档启用校验、thinking payload 映射、错误路径均有测试。
- 前端：能查看/编辑 L1-L5 启用状态、切点、展示名和 thinking 模式；保存成功、校验失败、并发冲突和禁用档提示可见。
- 构建：后端定向/全量回归、`compileall`、前端 `tsc`/`build`、`git diff --check`。
- 部署：仅测试服，健康检查 200、HEAD 与工件一致、数据库完整性不变；默认 thinking 仍为 `auto`。
