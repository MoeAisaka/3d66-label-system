# 3D/SU 真实闭环接入前置冻结 v1

## 状态与产品边界

- 产品主体：TPENG 标签实验台（LabelLab），即标签体系重构的统一事实产品。
- 首个纵向切片：`model_3d_su`；类目只提供字段、规则、机制和视图扩展，不复制平台运行、纠偏、发布、投影或回流能力。
- readiness 状态：`pending_external_signoff`。该状态表示合同已冻结，**不表示已具备真实接入条件**。
- 双人工门保持不变：机制候选启用和标签事实发布必须分别人工批准。

本批只形成本地清单、模板和确定性哈希。未连接真实源、未执行 SQL、未请求权限、未调用模型、未写外部数据库、未发布标签、未部署。

## 来源身份合同

来源身份按站点分别冻结，不把国内和海外压成同一个主键合同：

- 国内：`aliyun_3d66_dw.dim_res_info_union`，素材 ID 为 `ll_id`，
  `res_type in (1,6)`，`is_single` 同表提供，候选键为 `res_type + ll_id`；
- 海外：`aliyun_3d66_dw.ods_ll_relebook_res`，素材 ID 为 `res_id`，
  `res_type=6`，`is_single` 来自 `aliyun_3d66_dw.ods_ll_relebook_res_su_extra`，
  候选键为 `res_type + res_id`；两表关联键必须另行签认。

对每个经 Data Owner 签认的数据窗口，仅允许另行审批后执行站点对应的只读探查：

1. 分类型记录数和快照分区一致性；
2. 权威素材 ID 空值或空字符串计数；
3. 候选键重复；
4. `is_single` 空值、非法值和整体/单体分布；
5. 海外主表与 `su_extra` 的匹配覆盖率、重复匹配和快照一致性。

国内和海外探查必须分别生成证据哈希，不得复用同一份 `probe_hash`。签认包必须绑定站点、数据窗口、表名、查询哈希、聚合计数、审阅人和证据哈希；重复、空主键、非法 `is_single` 或海外多表匹配冲突均为 0 才可签为 verified。

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

- 国内/海外来源表、各自数据窗口、站点专项聚合结果与独立 `probe_hash` 已由 Data Owner 签认；
- 海外主表与 `ods_ll_relebook_res_su_extra` 的关联键已签认，匹配覆盖与重复匹配均通过；
- 字段逐项完成 Owner、whole/single、空值语义、基数、词表、P/R 门槛和回退版本签认；
- 至少 100 个样本的黄金集 revision 已锁定，覆盖矩阵和复审记录完整；
- `SELECT`/`DESCRIBE` 权限经审批且有到期时间，拒绝项保持未授权；
- 六类 RACI 责任人、验收人和下游读取方已签认；
- 真实接入、真实模型、目标表 DML、灰度与回退已另行冻结并获得精确授权。

## 停止条件

一旦需要连接真实源、执行 SQL、索取凭据、调用模型、写外部数据库、启用候选、发布标签、覆盖存量或部署，立即停止并重新冻结执行合同。证据不完整、主键冲突、质量门槛不达标、版本漂移或无法完成对账时同样 fail-closed。

当前来源身份运行时仍以单一 `source_identity` 和国内 `(res_type,ll_id)` 候选键为主；海外 `(res_type,res_id) + su_extra.is_single` 的多表绑定尚未接入，只记录为下一阶段 Gap，不因本次来源同步扩大冻结执行合同。
