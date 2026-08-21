# 候选发布门禁接受运营自选 A/B 部署回执

## 发布内容

- 修复提交：`a81a468`（候选发布门禁接受运营自选 A/B 的自洽覆盖快照）
- 部署时 Codeup `main`：`a81a46808ce82da6cf5acfc12057b0b7cd8fc825`
- 合并方式：快进推送到 `origin/main`，未改写历史，未合并其他分支
- 本次无 Codeup MR：按指示直接快进推送 `main`
- 代码层回退点：`8209b1f`（本次部署前的服务器 HEAD）
- 部署 bundle SHA-256：`226787336a14442bf9f4ebbce9a711f5bc7bd5c52c3f12272efd5ed9922873aa`（部署前当场捕获，大小 `52968295` bytes）

父提交 `8209b1f` 为另一并发会话的产出（规则计分模式下手选调用 B 接管正文），在本次部署前已由该会话自行推送并部署至测试服，故本次部署仅在其之上叠加发布门禁改动，未代为发布其未预期内容。

## 触发问题

运营用自选 A/B 版本跑完候选回归后，若要将该候选发布上线，`mechanism_release_gate.py` 会以 `candidate_snapshot_mismatch` 拒绝：该门禁要求冻结快照的 `prompt_bindings` 与候选合同出厂声明严格相等，而运营自选版本必然使两者不同。

此为 `5dee82f`（候选回归开放运营自选 A/B）的配套缺口：回归路径已放开，发布路径仍按旧约束拒绝，导致「自由组合验证完直接发布」的闭环走不通。

## 语义变更

发布门禁不再要求快照绑定与候选出厂声明严格相等，改为要求**差异自洽**：

- 快照绑定与候选声明相等时照常放行（历史行为不变）。
- 两者不同时，必须存在 `prompt_binding_override` 记录，且其 `declared` 对得上候选合同声明、`executed` 对得上快照实跑对，才放行。
- 缺少覆盖记录的任意绑定仍然拒绝；覆盖记录中 `declared` 或 `executed` 与事实不符也拒绝。

因此这是把「严格相等」降级为「防伪造」，不是无条件放行。门禁其余三项检查（候选归属 `candidate_revision_id`、类目 `category_key`、合同哈希 `contract_hash`）均未改动，仍防错配。

## 验证

- 后端全量 `1708 passed, 1 skipped`。新增 3 项定向测试：运营自选 A/B 的自洽覆盖快照应放行；无覆盖记录的任意绑定须拒绝；覆盖记录中 `declared` 谎报须拒绝。
- 前端 `tsc -b --force` rc=0，`npm run build` 通过。前端本次零改动。
- 真实数据验证（测试服 `run 48` + 候选 Revision 7，只读探针，未激活任何候选）：旧门禁判定拒绝，新门禁判定放行；两类伪造快照均仍被拒绝。验证用临时文件已从容器与主机 `/tmp` 清理。
- 部署前 `--dry-run` 通过，提交号一致；`git diff --check` 干净。

## 部署结果

- 测试服务器：`192.168.1.35:8081`
- 部署前服务器提交：`8209b1f`
- 部署后服务器提交：`a81a46808ce82da6cf5acfc12057b0b7cd8fc825`，worktree 无改动
- 容器：`3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`：`ok`；`/api/health/ready`：`ready`；8 个 worker 活跃
- 数据库：integrity `ok`，FK `[]`，`schema_migrations` `76`
- NAS：`/mnt/label-nas/maps` 保持 `cifs (ro)` 只读挂载
- 健康检查第 4 次轮询达到 healthy，`DEPLOY_RC=0`
- 磁盘：部署后可用 `12G`（88%）
- 部署日志：`2026-08-21/codex-threads-01a02397-ebd3-71d3-badc/work/deploy-a81a468.log`（位于 `~/Documents/Codex` 下的会话工作目录，不在本仓库内）

产物核验：容器内 `mechanism_release_gate.py` 含 `_snapshot_prompt_bindings_consistent` 2 处、`prompt_binding_override` 3 处。

## 本次实际回归结果（run 48）

配合本次改动，测试服上以候选 Revision 7 加运营自选 A/B（`model-3d-su-a-v4-20260821` / `model-3d-su-b-v4-20260820`）对基准集 10 跑了一轮真实全量回归：

- `run 48`，第 10 轮，50 张全部完成，失败 `0`，无绑定类错误
- 冻结快照如实记录实际执行的 v4 对，覆盖审计 `actor: sol`，候选修订本体未被改写
- 精确准确率 `0.24`（12/50），相邻准确率 `0.50`
- 判级分布：`L1` 43 张、`L2` 7 张，`L3`/`L4`/`L5` 均为 0；期望 `L3` 的 16 张全部被判为 `L1` 或 `L2`

管道链路完整（50 条均有真实 evaluation 记录），准确率偏低系该对 v4 草稿提示词自身输出塌缩，与本次门禁改动无关。

## 遗留阻塞（未改动，需 Owner 决定）

`run 48` 加候选 Revision 7 目前**仍然无法发布**，但拦截者已不是本次修复的 `candidate_snapshot_mismatch`，而是排在其之前的 `candidate_ancestry_conflict`。

原因是候选链已分叉：Revision 7（id 22）的父为 Revision 6（id 21），而现役投影为 Revision 8（id 23），其父为 Revision 5（id 15）。即 Revision 8 与 Revision 6 是兄弟分支，Revision 7 不在现役版本的候选链上。门禁要求候选必须是现役版本的直接子代，否则激活会静默丢弃 Revision 8 引入的改动。

该检查防护的是合同血缘正确性，与「运营自选 A/B 版本」无关，且激活动作直接替换线上现役合同，故本次未触碰。

另需注意：即便血缘检查放行，`run 48` 大概率会通过质量门禁——因为其对照运行 `run 43` 本身是坏的（`partial_failed`，42 条失败，exact `0.0`，denominator 仅 8）。相对这样的基准，24% 精确率算「提升」。质量门禁通过并不代表该候选可用。

可选路径：其一，将候选重建在现役 Revision 8 之上再跑回归后发布，不削弱任何保护；其二，放开血缘检查，代价是可能丢弃 Revision 8 的改动。

## 本次未做

未修改 `candidate_ancestry_conflict` 血缘检查，未修改质量门禁阈值，未激活任何候选版本，未改变现役发布指针，未改动 `worker.py` 执行期一致性校验，未改动前端源码，未部署 MacBook `8080`，未连接公司 Windows，未写业务生产数据，未重跑历史回归，未修改生产环境。
