# 3D/SU 最小权限与 RACI v1

## 权限申请清单

本文件是未来申请模板，不执行申请、不读取真实权限、不包含账号或凭据。

| 对象 | 允许动作 | 明确禁止 | 用途 | 有效期 | 当前状态 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| `aliyun_3d66_dw.dim_res_info_union` | SELECT, DESCRIBE | DOWNLOAD, UPDATE, ALTER, DROP, INSERT, DELETE | 国内 `(res_type,ll_id)`、`is_single` 与快照探查 | 待 Data Owner 确认，必须限时 | not_requested | 未生成 |
| `aliyun_3d66_dw.ods_ll_relebook_res` | SELECT, DESCRIBE | DOWNLOAD, UPDATE, ALTER, DROP, INSERT, DELETE | 海外 `(res_type,res_id)`、主表快照与字段探查 | 待 Data Owner 确认，必须限时 | not_requested | 未生成 |
| `aliyun_3d66_dw.ods_ll_relebook_res_su_extra` | SELECT, DESCRIBE | DOWNLOAD, UPDATE, ALTER, DROP, INSERT, DELETE | 海外 `is_single`、关联覆盖和重复匹配探查 | 关联键签认后限时申请 | not_requested | 未生成 |
| 统一大维表与职责小表 | 无 | 全部 DML/DDL | 未来真实投影 | 本批不适用 | out_of_scope | 必须另行冻结 |
| 模型提供方 | 无 | 批量调用、Token 读取 | 未来真实评测 | 本批不适用 | out_of_scope | 必须另行冻结 |

来源探查权限必须单独申请 `SELECT` 和 `DESCRIBE`，限定申请人、目标表、目的、数据窗口、到期时间和审批单。任何 Download 或 DML/DDL 权限都使本 readiness 失效并触发停止。

## RACI

| 角色 | R | A | C | I | 必需签认证据 | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- |
| Product Owner | 冻结范围、字段需求、双人工门 | 真实闭环最终验收 | 数据、算法、平台、下游 | 项目相关方 | 合同版本、验收清单、批准记录 | UNASSIGNED |
| Data Owner | 表与窗口、双站点身份探查、海外多表关联和数据质量 | 来源身份签认 | 平台、审核 | 产品、算法 | 表/窗口、两站独立 probe hash、关联键、聚合结果、审批单 | UNASSIGNED |
| Algorithm Owner | 模型/机制版本、回归方案、P/R 证据 | 算法效果签认 | 产品、审核、数据 | 平台、下游 | 模型版本、机制版本、黄金集 revision、指标报告 | UNASSIGNED |
| Platform Owner | 接入、幂等、队列、投影、对账、回退 | 技术上线验收 | 数据、算法、下游 | 产品、审核 | 发布 SHA、迁移、演练、回退点、对账报告 | UNASSIGNED |
| Reviewer Owner | 真值、抽查、纠偏、争议裁决 | 黄金集与人工结果签认 | 产品、算法 | 数据、下游 | 双人标注、裁决、revision、抽查记录 | UNASSIGNED |
| Consumer Owner | 字段合同、读取、对账、Badcase 回流 | 下游消费验收 | 产品、平台、数据 | 算法、审核 | 消费表、读取结果、主键/数量/哈希、回流记录 | UNASSIGNED |

`UNASSIGNED` 是显式阻塞，不允许由开发者猜测或自动填充。每项签认必须包含人员、角色、时间、有效期、合同/版本哈希和可复核证据位置。人员或权限到期、Owner 变更、合同版本变化后需重新签认。

## 九月真实闭环验收依赖

在后续单独冻结的真实接入批次中，至少需要 3D 和 SU 各一批真实素材、100 个锁定黄金/挑战样本、机制候选与自动回归证据、机制启用人工批准、标签事实发布人工批准、真实投影和下游读取、字段级 P/R 与五档矩阵、主键/数量/版本/哈希对账、幂等/断点/失败/漂移/回退演练，以及至少一次下游 Badcase 回流。本文件只列依赖，不授权执行。
