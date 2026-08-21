# ADR-0046：3D & SU 模型美感评测机制独立类目

- 状态：Accepted
- 日期：2026-08-14

## 背景

运营需要在标签实验台上传 3D/SU 模型样本并持续跑基准回归。另一条并行任务已经以 `category_key=three_d` 承载生产接入、只读来源和影子投影；复用该 profile 会把生产消费合同与实验机制混在一起，也会造成重复实现和版本边界不清。

## 决策

1. 新增独立组合类目 `model_3d_su`，包含 `space_building`、`soft_furnishing`、`functional_model` 三条赛道。
2. 调用 A 只负责分类、平台通用字段和 3D/SU 标记；调用 B 只输出五维等级与可见证据。服务端 v3 合同负责权重、扣分、总分和 L1-L4 映射，L5 在首版关闭。
3. 白背景与二维码只记录、不触发红线；水印、品牌/IP、中文字样、地标、宗教和高风险内容首版只进入结构化标记。
4. 五维初版扣分代表值固定为 20/50/80。功能性模型原始比例按 35:25:20:15:10 保留相对优先级，但落库前归一化为严格总和 1.0，避免 105% 权重超配。
5. 仅在共享动态类目目录、startup seed 和通用 pipeline 注册表中并列增加 `model_3d_su`；不得覆盖或复用 `three_d` profile、编辑器、只读来源或影子投影。

## 后果

- 运营可以使用现有上传、样本集和基准回归链路，不需要新建一套任务系统。
- A/B prompt、rubric、v3 contract 和三赛道维度配置都能独立版本化、幂等 seed，并保留运营手工修改。
- 功能性模型的展示比例与实际评分比例存在“原始点数/归一化合同值”两种表达，文档和测试必须同时说明。
- 本 ADR 不授权导入 NAS/Excel、真实模型调用、生产部署或批量重跑；这些动作需另行冻结范围和授权。

## 不可破坏约束

- 类目键固定为 `model_3d_su`，不得改成 `three_d`。
- 等级固定为 L1 80–100、L2 61–79、L3 41–60、L4 0–40，L5 disabled。
- seed 冲突时 fail-closed，不覆盖运营拥有的非本版本合同或 prompt。
- 历史运行继续读取其冻结快照；后续运营校准只能追加新版本，不回写旧结果。

## 2026-08-17 修订：v2 恢复五维 grade 权威评分

Run #27 的 50 条 `model_3d_su` 回归全部输出 L1。运行证据显示，43 条没有命中任何扣分规则，其余 7 条扣分后仍不低于 90；实际调用身份全部是动态 `dimension-deduction-prompt-v1`，而不是运行配置冻结展示的 `model-3d-su-b-v1-20260814`。根因是 v1 同时声明了静态五维 grade B prompt 和 `deduction_rules`，权威 worker 看到规则后绕过静态 B，改为从 100 分起仅扣命中的少量缺陷。

本修订追加 `model-3d-su-v2-grade-scoring-20260817`，并取代上文第 4 条作为现役机制：

1. 五个维度只保留线性 `grade_points`：`1=0`、`2=25`、`3=50`、`4=75`、`5=100`；不再声明 `deduction_rules`。
2. 调用 B 必须使用冻结的静态 v2 prompt，且只能返回合同中的五个维度。每个维度必须包含 1-5 整数 grade 和至少一条非空可见证据；缺失、多余、越界或无证据都 fail-closed 为人工复核，不能默认满分。
3. worker 仅对显式声明 `dimension-grade-output-v1` 的赛道绕过旧八维预评分和旧风险复核；最终分数仍由现有 v3 grade bridge 按赛道权重确定性聚合。
4. spec、rubric、A/B prompt 和 system owner 全部提升为 v2 身份。startup seed 只升级已知 system-owned v1/v2 行，保留运营编辑的 profile 描述；未知 owner 继续拒绝覆盖。
5. v1 prompts、v1 projected revision、Run #27 和所有历史冻结任务/结果都保持不变。v1 revision 退役后仍可由历史 snapshot 回放；本修订不授权重跑、部署或生产写入。

## 2026-08-19 修订：v3 恢复详细扣分合同并保持静态 grade 权威路径

本修订修复自动纠偏候选重试丢失前一轮有效 `system_prompt` 的问题，并恢复五维 20/50/80 扣分描述。扣分描述作为合同、审计和前端展示镜像保留；每条 3D/SU 赛道继续显式声明 `dimension-grade-output-v1`，服务端 `rule_scoring_mode` 对该合同固定返回 `grade_fallback`，不会调用动态 `dimension-deduction-prompt-v1`，从而避免再次出现全部 L1。

1. 现役身份提升为 `model-3d-su-v4-aesthetic-foundation-20260821`、`model-3d-su-rubric-v4`、v4 B prompt 和 `system:model-3d-su-v4`；startup seed 追加新 projected revision，不改写 v1/v2/v3 历史。
2. 五个维度均恢复 `minor_defect=20`、`obvious_defect=50`、`severe_defect=80` 及具体中文可见缺陷描述，同时保留线性 `grade_points` 和静态 B 的严格等级/证据校验。
3. 自动纠偏重试只继承上一轮类型正确且非空的字段；无效值不会污染下一轮，当前修复输出优先，最新摘要和变更说明优先。
