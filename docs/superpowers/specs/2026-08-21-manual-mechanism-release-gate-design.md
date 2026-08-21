# 人工机制候选回归与启用门设计

**状态：** Proposed
**日期：** 2026-08-21
**范围：** 类目评测 v3 机制配置、候选基准回归、人工启用；不改变标签事实发布流程。

## 背景

`/workflow/optimization/category-evaluation-v3-config` 目前可以校验并追加不可变候选 revision，但运行时投影的旧写入口已经关闭，页面和 API 没有承载“候选回归通过后人工启用”的通用入口。自动纠偏链路拥有自己的候选回归与管理员决策路径，人工直接编辑机制却在保存候选后断链。

## 统一评分语义

所有图片类目（`inspiration_image`、`space_image`、`material_image`、`pdf_text`、
`model_3d_su` 以及后续接入等级撮合器的图片类目）统一要求调用 B 输出 0-100
的 `aesthetic_score` 和可见证据。等级撮合器以该分数作为初始分，再执行当前冻结
合同声明的命中扣分、媒介修正、硬伤封顶、赛道封顶和等级映射。`base_score` 与
`grade_points` 只保留用于历史合同读取和旧快照回放，不再参与新图片评测的运行时
初始分，也不作为调用 B 异常时的兜底。

调用 B 缺少合法 `aesthetic_score`、证据不完整或调用失败时，权威路径必须
fail-closed，保存 `score=null`、`level=null` 并进入人工复核。调用 A 已确认且有
完整证据的红线仍在调用 B 前直接短路为 L5；红线结果缺证据时继续调用 B。

`proposal_text_pdf` 是唯一保留文档专用评分语义的例外：调用 B 的视觉、叙事、创新
分项由文档合同相加为初始分，再执行其文档合同规则。

## 决策

补齐一个与自动纠偏共用约束的人工机制发布门：

1. 编辑保存永远只创建 `candidate` revision，不改变运行时投影。
2. 基准回归创建时继续冻结候选 revision、候选合同哈希、候选合同中的 A/B prompt binding，以及基准运行上下文。
3. 新增候选启用接口。接口仅允许管理员调用，要求提交候选回归 run、预期现役 revision/hash 和人工备注。服务端原子校验：
   - 候选属于路径类目、状态为 `candidate`，并且是当前现役投影的候选后代；
   - 回归 run 属于同一类目和同一基准集，已结束且 `status=completed`；
   - 回归 run 的冻结快照明确引用该候选 revision，提示词绑定与候选合同一致；
   - 回归无失败条目，候选与对照运行可比较，exact accuracy、adjacent accuracy 不低于对照，失败数不增加；
   - 当前运行时投影 revision/hash 与请求中的 CAS 值一致；
   - 候选提示词仍存在、阶段和类目正确，且当前类目提示词仍是候选创建时的父提示词。
4. 校验通过后，在同一事务内将旧 revision 置为 `retired`、候选置为 `active`、更新 v3 runtime projection，并发布候选 A/B prompt；任何失败都不改变状态。
5. 标签事实发布、存量重跑和下游投影继续是独立流程；启用机制不自动发布标签事实或覆盖存量。
6. 自动纠偏的现有管理员决策路径继续保留，并复用同一个质量门 helper，避免两条启用规则漂移。
7. 新建但尚无可执行现役合同的类目不允许通过该接口 bootstrap 激活；服务端返回可操作的 `runtime_projection_required` 错误，要求先完成类目运行时初始化。

8. 新图片候选发布前必须验证其评分语义为 `b_aesthetic_foundation_v1`；旧 grade
   fallback 候选不能绕过统一评分合同进入运行时。

## 数据流

```text
人工编辑
  -> POST /v3-config/{category}/revisions
  -> candidate revision
  -> POST /baseline-sets/{set}/runs (candidate_revision_id)
  -> frozen candidate regression
  -> completed metrics
  -> POST /v3-config/{category}/revisions/{revision}/activate
       (admin + CAS + regression gate + note)
  -> active v3 projection + published prompt binding
```

自动纠偏路径仍为：

```text
人工纠偏证据
  -> candidate prompt/revision
  -> automatic candidate baseline run
  -> existing admin decision
  -> same release-gate helper
```

## API 合同

新增请求体：

```json
{
  "regression_run_id": 123,
  "expected_projected_revision": 7,
  "expected_projected_contract_hash": "<64 hex chars>",
  "note": "人工确认候选回归证据"
}
```

成功响应返回候选 revision、当前 projected revision、回归 run、机制刷新信息和审计事件键。失败响应使用稳定错误码，不返回内部堆栈；校验失败时不写入任何运行时字段。

## 前端行为

- 候选 revision 详情显示“创建候选回归”入口，跳转到基准回归并预填类目、基准集和候选 revision。
- 已完成且通过门禁的候选回归显示“启用候选”按钮；按钮只对管理员可见，并要求确认备注。
- 现役、历史、非当前候选链和回归未完成状态不显示启用动作；错误码原样转为可操作提示。
- 成功启用后刷新类目列表、revision 历史、提示词和基准回归查询；不触发标签事实发布。

## 失败与回退

- 所有写操作在 CAS、回归质量门和合同绑定校验之后才提交事务。
- 任何候选/提示词/回归快照漂移均 fail closed，要求刷新并重新创建候选。
- 启用失败不会退休旧 revision，不会改变候选状态，不会改变类目提示词绑定。
- 已启用候选不能重复启用；已拒绝或已退役候选不能启用。
- 回退通过创建新的候选 revision 指向旧合同并重新回归，不提供覆盖式回退写入口。

## 验收与非目标

验收必须覆盖：人工编辑候选、候选回归冻结、通过门启用、质量失败拒绝、CAS 冲突拒绝、合同/提示词漂移拒绝、自动纠偏复用质量门、前端入口与刷新。

本设计不包含：自动跳过管理员确认、自动发布标签事实、自动存量重跑、真实模型调用、测试服写入或部署。
