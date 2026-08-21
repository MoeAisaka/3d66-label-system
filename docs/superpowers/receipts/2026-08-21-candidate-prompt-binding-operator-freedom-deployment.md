# 候选回归开放运营自选 A/B 版本部署回执

## 发布内容

- 修复提交：`5dee82f`（候选回归允许运营自选 A/B 并如实冻结实际执行版本）
- 部署时 Codeup `main`：`5dee82fbf06a38d2aeed67e55b0624199c0f120e`
- 合并方式：快进推送到 `origin/main`，未改写历史，未合并其他分支
- 本次无 Codeup MR：按指示直接快进推送 `main`
- 代码层回退点：`a3747e6c7515b58ee93dea70b98647d999ef1235`（本次部署前的服务器 HEAD）
- 部署 bundle SHA-256：`a137f1b24f06cbc558e179d034ad98b6b34917da667dbc878b148d9df70cc78e`

本项 bundle SHA-256 为**部署前当场捕获**，对 `origin/main` = `5dee82f` 生成，大小 `52949790` bytes。上一份回执（`2026-08-21-candidate-lineage-b-foundation-deployment.md`）的同名字段只能事后复现，本次已改为部署前留证，两者来源不同。

## 触发问题

运营在「运行配置」抽屉里选择候选合同 Revision 7 并手动改用较新的 A/B 版本后，点击「运行全量回归」持续失败：`POST /api/baseline-sets/10/runs` 返回 409，提示「候选合同声明的 A/B Prompt 版本与执行策略不一致」，服务端日志显示同一请求连续 4 次被拒。

根因是前后端对同一规则的两套理解。后端在 `candidate_revision_id` 非空时，要求候选合同 `prompt_bindings` 声明的版本与本轮实际执行的 A/B 严格相等；而前端提示明写「可以按当前选择直接启动」，启动按钮的禁用条件里也没有该项。数据库实测：候选 Revision 7 声明 A `model-3d-su-a-v3-20260819`、B `model-3d-su-b-v3-20260819`，运营选择的是 v4 版本，必然不等。

该 409 属既有行为，两个上游提交 `7420506`、`a3747e6` 均未触碰此校验器。但血缘检查在代码顺序上先于绑定检查，血缘不再硬阻断后请求才得以走到这一道闸门，问题因此暴露。

## 语义变更

候选合同声明的 A/B 自此只作为生成时的出厂建议，不再作为启动闸门。评测机制实验需要运营自由组合任意候选合同与任意 A/B 版本，该自由由 Owner 明确授权。

- 创建 run 时改为把冻结快照的合同副本绑定到本轮真实执行的 A/B，复用既有 `bind_category_evaluation_prompt_versions()`，快照不会谎报实际执行版本。
- 实际执行与出厂声明不同时，冻结快照额外记录 `prompt_binding_override`，含 `declared`、`executed` 与操作人，保留审计链。
- 绑定后仍自校验一次。该校验失败意味着绑定函数与校验器不再自洽，属内部缺陷，故返回 500 `candidate_prompt_binding_rebind_failed`，不再是面向运营输入的 409。
- 候选修订本体与现役运行时投影都不因一次实验被改写。
- `worker.py` 的执行期一致性校验未改动：它比对冻结快照与 StrategyBundle，用于发现快照被篡改或漂移，仍然有效。选择绑定而非删除创建期校验，正是为避免失败从「点击即报错」退化为「任务运行后才崩」。

## 遗留边界（未改动，需 Owner 决定）

`mechanism_release_gate.py` 在**激活候选**时仍比对冻结快照绑定与候选合同声明。已实测确认：用覆盖的 A/B 跑完回归后去发布该候选，会被 `candidate_snapshot_mismatch` 拒绝。

该门禁管的是「候选提升为现役」而非「跑回归」，超出本次授权范围，故未触碰。若运营流程为「自由组合验证完直接发布」，此处需另行放开。

## 部署结果

- 测试服务器：`192.168.1.35:8081`
- 部署前服务器提交：`a3747e6c7515b58ee93dea70b98647d999ef1235`
- 部署后服务器提交：`5dee82fbf06a38d2aeed67e55b0624199c0f120e`，worktree 无改动
- 容器：`3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`：`ok`；`/api/health/ready`：`ready`；8 个 worker 活跃
- 数据库：integrity `ok`，FK `[]`，`schema_migrations` `76`
- NAS：`/mnt/label-nas/maps` 保持 `cifs (ro)` 只读挂载
- 健康检查在第 4 次轮询达到 healthy，`DEPLOY_RC=0`
- 磁盘：部署前可用 `14G`（87%），部署后 `13G`（87%）
- 部署日志：`2026-08-21/codex-threads-01a02397-ebd3-71d3-badc/work/deploy-5dee82f.log`（位于 `~/Documents/Codex` 下的会话工作目录，不在本仓库内）

## 产物核验

容器内 `main.py` 已确认包含本次改动：`prompt_binding_override` 出现 1 处，`bind_category_evaluation_prompt_versions` 出现 2 处（导入与调用）。

## 发布前验证

- 后端从仓库根 `1695 passed, 1 skipped`。较上一次的 1694 增加 1 项，即本次新增的正面测试：运营用不同 A/B 跑候选合同应当成功，冻结快照须记录实际执行对并留下覆盖审计，候选修订与现役投影不得被改写。
- 删除了断言 409 的旧测试用例，其描述的契约已被本次变更取代。
- 前端 `tsc -b --force` rc=0；`npm run build` 通过。前端源码本次零改动，那条「可以按当前选择直接启动」的提示现已与后端行为一致。
- 契约检查 21 项中 20 项通过。`contract:information-architecture` 由两段脚本串联，其中信息架构断言部分实测 rc=0 通过，第二段 `check-workspace-components.ts` 需拉起 Vite 与无头 Chrome，本地执行超时，**未能取得其结论**。前端相对部署前 HEAD 无改动，判断与本次变更无关，但不宣称其为通过状态。测试期间起用的 Vite 进程与 `4175` 端口已清理。
- 部署前 `python3 scripts/deploy-test.py --dry-run` 通过，提交号与推送一致。
- `git diff --check` 干净，推送前工作区干净。

## 回退能力

本次部署**未生成新的数据库快照**：`scripts/deploy-test-server.sh` 本身不含快照逻辑，历史快照均为人工创建。已核对快照目录，最新仍为上一次部署前创建的两个。

- 代码层回退：`a3747e6c7515b58ee93dea70b98647d999ef1235`
- 数据库层回退：`app-predeploy-2d76ed93-20260821T070002Z.db`（`696844288` bytes，SHA-256 `733c0f01c90d823ea2484ced70bb6f1fdecda3575b30d55844194ce9d2edd0c3`，对应更早的 `2d76ed9` 状态）
- 另保留 `app-predeploy-820771d4-before-242cd98c-20260820T102909Z.db`（`594124800` bytes）

本次变更不含数据库迁移，部署前后 `schema_migrations` 均为 `76`，故代码回退即可恢复行为。

## 本次未做

未修改 `mechanism_release_gate.py` 的候选激活门禁，未改动 `worker.py` 执行期一致性校验，未改动任何前端源码，未部署 MacBook `8080`，未连接公司 Windows，未启用候选版本，未改变现役发布指针，未写业务生产数据，未调用真实模型，未重跑历史回归，未修改生产环境。
