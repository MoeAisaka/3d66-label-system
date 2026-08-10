# ADR-0040：类目等级档位与豆包 Thinking 控制

## 状态

Accepted，先在内网测试环境验证。生产默认保持不变。

## 背景

灵感图评测需要把固定字段、美感百分分和等级细则分开验收：等级不是模型自由生成的字段，而是由确定性等级计算器撮合分数与类目合同得到。现有 v3 合同同时存在固定等级阈值和新档位诉求；历史合同还可能只把 L5 用作红线结果。豆包火山方舟的思考参数也不能沿用 OpenAI 的 `reasoning_effort` 字段。

## 决策

1. 全系统继续采用 `doc-l5-worst-v1`：L1 最优，L 序号越大质量越差。
2. `CategoryEvaluationV3Config.contract.level_scale` 是类目档位的唯一新真值；版本为 `category-level-scale-v1`，显式声明 L1-L5，可启停档位、调整 0-100 整数切点和展示名。停用档不进入分数映射；启用档最差档必须承担 0 分兜底。
3. 历史 `level_thresholds` 只读兼容，不把旧列表中缺失的 L5 自动解释成停用，以保留红线专用 L5 的历史语义。启用新档位合同时，红线 `hit_level` 必须指向启用档；关闭 L5 与迁移红线到 L4 通过同一个专用 PUT 原子提交。
4. 新增 `GET/PUT /api/category-evaluation/v3-config/{category_key}/level-scale`。PUT 要求 `expected_revision`，可选校验 `expected_contract_hash`，使用条件更新避免并发覆盖，成功递增 revision、重算 hash 并写入 `AuditEvent`；校验失败 422，并发冲突 409。
5. `ModelConfig.thinking_mode` 取值为 `auto|enabled|disabled`，数据库迁移默认 `auto`。`auto` 不向豆包覆写 provider 默认；显式值仅在豆包 provider 边界映射为 `thinking: {type: ...}`。OpenAI 仍走 `reasoning_effort`，不受此字段影响。策略快照和 provider trace 记录实际模式，未知值 fail-closed。

## 验收边界

- 本次只改测试环境工件和代码，不调用真实评测 provider，不修改生产数据库。
- 前端复用现有 v3 合同页和模型页：等级行式编辑器支持启停、切点、展示名和红线联动校验；模型页显示自动/开启/关闭 segmented control，非豆包渠道禁用。
- 评测默认行为、历史策略快照和旧等级阈值兼容性保持不变；生产默认 thinking 不切换为 disabled。

## 回滚

回滚代码提交和测试服容器到部署前提交；数据库仅有加法迁移，旧版本忽略 `thinking_mode` 列即可。等级合同未写入生产，测试服可通过受保护部署脚本回到前一提交。
