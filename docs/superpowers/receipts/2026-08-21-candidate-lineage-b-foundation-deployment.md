# 候选回归血缘与调用 B 审美基线兼容部署回执

## 发布内容

- 修复提交：`7420506`（候选回归血缘改为记录而非硬阻断）、`a3747e6`（调用 B 审美基线改为引擎默认严格且可显式退出）
- 部署时 Codeup `main`：`a3747e6c7515b58ee93dea70b98647d999ef1235`
- 合并方式：快进推送到 `origin/main`，未改写历史，未合并其他分支
- 代码层回退点：`2d76ed9364c633fe709bfc610e1792b167a40bf8`
- 本次无 Codeup MR：按指示直接快进推送 `main`，与上一次回执的 `MR #34` 流程不同

## 部署结果

- 测试服务器：`192.168.1.35:8081`
- 部署前服务器提交：`2d76ed9364c633fe709bfc610e1792b167a40bf8`
- 部署后服务器提交：`a3747e6c7515b58ee93dea70b98647d999ef1235`，worktree 无改动
- 容器：`3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`：`ok`；`/api/health/ready`：`ready`；8 个 worker 活跃
- 数据库：integrity `ok`，FK `[]`，`schema_migrations` `76`
- NAS：`/mnt/label-nas/maps` 保持 `cifs (ro)` 只读挂载
- 健康检查在第 4 次轮询达到 healthy
- 部署 bundle SHA-256：`984df5a7f5f15b9d11ddc311c02b39b9443ddced63d8b290b770efbf9e554cb7`

关于上面这条 bundle SHA-256：该值为**事后复现值，非部署当时捕获**。`scripts/deploy-test.py` 在 `tempfile.mkdtemp()` 中生成 bundle，成功后本地 `rmtree`、远端 `rm -f` 清理，部署日志亦未记录其哈希，故原始产物已不可取回。本项由 `git bundle create` 对同一 `origin/main` = `a3747e6` 重新生成得到，连续三次输出字节一致，且对象库最后一次 pack 变动（`17:36:56`）早于本次推送（`17:45:20`），可认为与部署时输入一致。与上一次回执中部署当场记录的该字段不同源，追溯时需注意这一区别。

## 产物核验

容器内已确认部署产物包含本次两处改动：`main.py` 含 `candidate_lineage`，`dimension_deduction_bridge.py` 含 `is_call_b_failure_fallback`，`worker_v3_authoritative.py` 含 `foundation_required`，前端 `dist` 含构建标识 `a3747e6`。`BUILD_SHA` 环境变量为空属预期，该标识在构建期注入前端产物，不保留在运行时环境。

## 发布前验证

后端从仓库根 `1694 passed, 1 skipped`；前端 `npm run build` 通过；`package.json` 注册的 23 项契约检查全部通过；`tsc -b --force` 全量类型检查通过（确认非增量缓存假通过）；`git diff --check` 干净。

未接入 `package.json` 的 `check-baseline-v3-run-config.ts` 失败为既有孤儿脚本：其断言字符串在 `2d76ed9` 同样不存在，已被 `baseline-v3-run-config-contract.ts` 取代，非本次回归。

## 首次部署失败与回滚

首次部署在 9.8 秒内失败于 `compose_up`，根因为测试服磁盘 100% 占满（`/dev/sda3` 96G，可用仅 101M），新镜像层无法写入。守护脚本 fail-closed 并自动回滚成功，服务恢复至 `2d76ed9`，容器 `running/healthy`，integrity `ok`。按停止条件未重试部署，先定位并消除根因。

回滚期间 `compose_up` 能成功，是因旧镜像层全部命中缓存无需写盘；inode 占用 57%、内存正常，确认为纯空间问题。

## 磁盘清理范围

清理后可用空间由 `101M` 增至 `8.4G`（92%），释放约 8.8G：

- 删除 `images/.derived` 派生缓存 477M。该目录由 `worker.py` 以 `cache_dir` 传入（proposal-pdf、pdf、evaluation 等），会自动重建，目录结构保留。
- 删除 18 个历史部署前快照，保留最新两个。删除前已校验保留项 integrity `ok`、migrations `76`，确保不出现「保留项已损坏」。

未触碰：活库 `app.db`（716M）、范围外 6 个 `app-before-*.db`（2.3G）、其他项目镜像、26G Docker volumes、4.4G 全局构建缓存。先前删除的 28 个 `3d66-label-system` 悬空镜像因层共享（每 358MB 镜像中 304.8MB 为 SHARED）仅释放约 40MB，不足以解除阻塞。

## 回退能力

本次回退点为部署前快照 `app-predeploy-2d76ed93-20260821T070002Z.db`，对应部署前 `2d76ed9` 状态：

- 大小：`696844288` bytes
- SHA-256：`733c0f01c90d823ea2484ced70bb6f1fdecda3575b30d55844194ce9d2edd0c3`
- 校验：integrity `ok`，`schema_migrations` `76`

另保留上一代 `app-predeploy-820771d4-before-242cd98c-20260820T102909Z.db`（567M）。

注意：`scripts/deploy-test-server.sh` 本身不含快照逻辑，本次部署未生成新快照，历史快照均为人工创建。后续若需每次部署自动留快照，需单独改造守护脚本。

## 语义变更提示

本次两处改动均放松了原有 fail-closed 边界，需在后续回归中关注：

- 候选回归不再因 `candidate_revision_projection_drift` 硬 409 阻断，分叉状态以 `candidate_lineage` 记录并在前端提示；`isSelectableV3Candidate` 仅按分类与生命周期状态判定可选性，不再校验血缘。放松理由：revision 链为子指父，已激活版本位于链尖，永不会是候选的祖先，原向上遍历在每次发版后对所有候选必然失败。
- `b_aesthetic_foundation` 由「声明才严格」改为引擎默认严格，合同可用 `{"enabled": false}` 显式退出。调用 B 请求失败时未声明该块的合同按历史行为降级；调用 B 已应答但缺失分数仍 fail-closed，避免各维度静默满分。该区分由新增哨兵 `is_call_b_failure_fallback` 承担。
- `validate_category_evaluation_prompt_bindings()` 未改动，仍为执行期安全边界。

## 本次未做

未部署 MacBook `8080`，未连接公司 Windows，未启用候选版本，未改变现役发布指针，未写业务生产数据，未调用真实模型，未批量执行，未重跑历史回归，未修改生产环境。
