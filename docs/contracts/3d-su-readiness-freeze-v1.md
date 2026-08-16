# 3D/SU 真实闭环接入前置冻结 v1

## 状态与产品边界

- 产品主体：TPENG 标签实验台（LabelLab），即标签体系重构的统一事实产品。
- 首个纵向切片：`model_3d_su`；类目只提供字段、规则、机制和视图扩展，不复制平台运行、纠偏、发布、投影或回流能力。
- readiness 状态：`pending_external_signoff`。该状态表示合同已冻结，**不表示已具备真实接入条件**。
- 双人工门保持不变：机制候选启用和标签事实发布必须分别人工批准。

本批只形成本地清单、模板和确定性哈希。未连接真实源、未执行 SQL、未请求权限、未调用模型、未写外部数据库、未发布标签、未部署。

## 来源身份合同

候选表固定为 `aliyun_3d66_dw.dim_res_info_union`，候选业务键为 `res_type + ll_id`：

- `res_type=1`：3D；
- `res_type=6`：SU；
- `res_id` 只作为冲突证据，不自动拼入主键；
- 重复键或同键对应多个 `res_id` 时 fail-closed，不生成随机后缀。

对一个经 Data Owner 签认的数据窗口，仅允许另行审批后执行以下四类只读 `SELECT`：

1. 3D/SU 分类型记录数；
2. `ll_id`、`res_id` 空值或空字符串计数；
3. `res_type + ll_id` 重复键；
4. 同键对应多个 `res_id` 的冲突。

机器清单只保存这四条查询文本的 `probe_hash`，不保存查询结果、凭据或源表明细。签认包必须绑定数据窗口、表名、查询哈希、聚合计数、审阅人和证据哈希。重复键和多 `res_id` 冲突均为 0 才可签为 verified。

## 字段分层

平台 Canonical 字段为 `space`、`object`、`style`、`material`、`structural_features`、`architectural_element`、`soft_decoration`、`hard_decoration`、`color`、`title`。它们服务于全平台类目，不能复制为 3D/SU 私有事实。

3D/SU 扩展只允许使用 `category.model_3d_su.*`。每个字段必须签认：Owner、whole/single 适用性、`not_applicable`/`not_detected`/`needs_review` 语义、基数、词表版本、生产方式、Precision/Recall 门槛、发布与回退版本。默认质量门槛为 Precision ≥ 0.80、Recall ≥ 0.70；降低门槛需 Owner 另行批准。具体逐字段清单见 `3d-su-field-signoff-template-v1.csv`。

## 黄金集与验收

首批至少 100 个锁定黄金/挑战样本，覆盖 3D、SU、whole、single、空间建筑、软装家具、功能性模型、语义 hardcase 与 L1-L5。分层最低数量、真值证据和复审规则见 `3d-su-golden-set-plan-v1.md`。

锁定后的真值只允许新增 revision 或新 SampleSet，不允许原地覆盖。回归必须读取冻结 revision，并输出字段级 Precision/Recall、五档矩阵、失败样本和版本证据。

## 权限与责任

来源探查仅允许 `SELECT`、`DESCRIBE`；明确禁止 `DOWNLOAD`、`UPDATE`、`ALTER`、`DROP`、`INSERT`、`DELETE`。权限未申请，真实权限也未读取。统一大维表和各职责小表的 DML 是未来独立冻结、独立审批、可回退的依赖，本合同不授权。

产品、数据、算法、平台、人工审核和下游消费 Owner 的 RACI、有效期及证据字段见 `3d-su-permission-raci-v1.md`。任何角色未签认都维持 `pending_external_signoff`。

## 进入真实接入前的证据

- 来源表、数据窗口、四项聚合结果与 `probe_hash` 已由 Data Owner 签认；
- 字段逐项完成 Owner、whole/single、空值语义、基数、词表、P/R 门槛和回退版本签认；
- 至少 100 个样本的黄金集 revision 已锁定，覆盖矩阵和复审记录完整；
- `SELECT`/`DESCRIBE` 权限经审批且有到期时间，拒绝项保持未授权；
- 六类 RACI 责任人、验收人和下游读取方已签认；
- 真实接入、真实模型、目标表 DML、灰度与回退已另行冻结并获得精确授权。

## 停止条件

一旦需要连接真实源、执行 SQL、索取凭据、调用模型、写外部数据库、启用候选、发布标签、覆盖存量或部署，立即停止并重新冻结执行合同。证据不完整、主键冲突、质量门槛不达标、版本漂移或无法完成对账时同样 fail-closed。
