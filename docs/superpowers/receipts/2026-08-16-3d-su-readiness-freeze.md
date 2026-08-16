# 3D/SU 真实闭环接入前置冻结回执

## 本地执行信息

- 工作树：`/Volumes/WorkSSD/Codex/2026-08-11/labellab/work/3d-shadow-dry-run-prep-20260816`
- 分支：`codex/3d-shadow-dry-run-prep-20260816`
- readiness 提交：`175fe9c5eb51cca6773704da2d5022e56852b440`
- manifest：`3d-su-readiness-v1` / `model_3d_su`
- 当前 manifest SHA-256：`f9d5d03c1e02a029fc95205605deda3b1f65eb689ef8407a02d3947547a9b9de`
- 状态：`pending_external_signoff`

## 已完成

- 机器校验 manifest 固定候选表 `aliyun_3d66_dw.dim_res_info_union`、`res_type + ll_id`、`res_type=1/6`、四项只读探查哈希与重复冲突 fail-closed。
- 平台字段、`category.model_3d_su.*` 扩展、whole/single、`not_applicable`/`not_detected`/`needs_review`、P/R 门槛和锁定 revision 规则已写入合同。
- 黄金集计划定义至少 100 条并覆盖 3D/SU、whole/single、三赛道、L1-L5 与 challenge hardcase。
- 权限/RACI 模板明确只允许 `SELECT`/`DESCRIBE`，拒绝 Download、DML/DDL 和真实目标表投影授权。
- 前端 `npm run contract:three-d-readiness` 通过；字段 CSV 列宽检查通过；后端 readiness + source probe focused tests 为 `14 passed`；`git diff --check` 通过。

## 未决签认

- Data Owner 尚未提供经签认的数据窗口、四项聚合结果、重复/冲突为零的证据和 probe hash 绑定。
- 字段 Owner、whole/single 适用性、词表版本、空值语义、回退版本和证据哈希仍为 `UNASSIGNED`/`pending`。
- 黄金集尚未导入或锁定 revision；只完成样本规范，未读取真实素材。
- `SELECT`/`DESCRIBE` 限时权限尚未申请；统一大维表/小表 DML、模型调用和下游消费验收均未冻结。

## 外部效果与回退

本批没有连接真实上游、DataWorks/ODPS、模型或业务数据库；没有执行 SQL、请求权限、写数据库、发布标签、覆盖存量、推送 Codeup、创建/合并 MR 或部署测试服/生产。回退点为本分支上一提交 `6e5ed66`；删除本地 readiness 文件即可回退，不触碰既有数据库或远端状态。

任何下一步真实接入、权限申请、模型调用、DML、发布、推送、合并或部署都必须由 Owner 重新冻结范围并单独授权。
