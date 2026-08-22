# 运营自助能力补齐部署回执

## 发布内容

- 修复提交：`97cbe27`（补齐候选变基、门禁拒绝理由与规则命中诊断三项运营自助能力）
- 部署时 Codeup `main`：`97cbe27304d1338d9f96c7afd633162340c760bb`
- 合并方式：快进推送到 `origin/main`，未改写历史，未合并其他分支
- 本次无 Codeup MR：按指示直接快进推送 `main`
- 代码层回退点：`b715e64`（本次部署前的服务器 HEAD）
- 部署 bundle SHA-256：`c73403f7ab5b93e6ab35c46f7e57bb92e2c9c9f9b06e5c3ce3ba48c93ae57d05`（部署前当场捕获，大小 `52969174` bytes）

## 触发问题

前两次部署打通了「运营自选 A/B 跑候选回归、并以自洽覆盖快照通过发布门禁」的主干，但运营在界面上遇阻时无法自行看懂与修复，三处缺口都需回落工程：

1. **分叉候选无法自助修复**：界面仅提示「可正常回归，结果不代表现役机制表现」，未告知启用会被直接拒绝；`candidate_ancestry_conflict` 错误码前端无引用；前后端均无变基能力。
2. **门禁拒绝理由不可读**：`candidate_quality_gate_failed`、`golden_set_failure`、`key_field_regressed` 前端均无引用，`regressions` 仅有类型定义未渲染，激活失败只弹一句 `toast.error(error.message)`，结构化阻塞原因全部丢弃。
3. **评分诊断不可见**：`dimension_deduction_output` 前端无引用，`cap_reasons` 仅有类型无渲染。运营看不到规则是否命中，只能凭猜调阈值。

缺口 3 的实际代价已在 `run 48`/`run 49` 上显现：两轮都把绝大多数样本判为 L1，而候选 Revision 7 的改动全部集中在阈值（红线 `hit_level` L4→L3、`hit_score_cap` 40→60、扣分 20/50/80→30/60/100）。规则不命中时这些阈值全部空转，继续调整不会改变判级。

## 变更内容

### 后端

新增 `backend/app/candidate_rebase.py`：以候选与现役的最近共同祖先为基做三方合并，把候选自身改动重放到现役版本之上。列表按整体取舍，不做逐元素合并（避免把两套规则顺序悄悄交织）。无法判定的分歧一律报冲突，不猜。仅计算产物，不落库——新建候选仍由 `create_candidate_revision` 统一负责，父链规则、规范化与幂等保持单一归属。

新增 `backend/app/baseline_rule_diagnostics.py`：从已落库快照聚合每条声明规则的实际命中次数、零命中样本数、无扣分时的落档等级（取中位数，避免单个离群分数带偏）、分数分桶与红线触发次数。全部读取既有快照，不重算、不调模型。

新增端点：`GET /{category_key}/revisions/{revision}/rebase-preview`（只读，返回将带过来的改动与冲突）、`POST /{category_key}/revisions/{revision}/rebase`（有冲突则 409 拒绝，附冲突清单）、`GET /api/baseline-regressions/{run_id}/rule-diagnostics`。变基成功写入 `candidate_rebased` 审计事件，记录源候选、目标现役、共同祖先与采纳改动。

### 前端

新增 `candidate-release-panels.tsx`：`CandidateGateRejection` 摊开门禁每条阻塞原因（含准确率/召回率变化与失败样本数），并对 `candidate_ancestry_conflict` 给出变基指引；`CandidateRebasePanel` 提供先预览后执行的变基入口。

新增 `rule-diagnostics-evidence.tsx`：规则命中诊断抽屉。零命中样本占比达 50% 以上时置顶警告，明确说明此时提高扣分值或下调红线阈值都不会改变判级，应先检查调用 B 是否报出缺陷。

分叉提示文案改为明确告知「启用会被拒绝」，并挂载变基面板。激活按钮容器由横向 `justify-between` 改为上下两段，以容纳拒绝理由区块而不挤坏布局。

## 设计约束

- 变基只**新建**候选，原分叉候选保持不变：已记录在原候选上的回归仍准确指向它当时实际执行的内容。
- 有冲突拒绝而非猜测取舍；冲突时合并结果保留现役取值，绝不擅自选边。
- 同一 `rule_id` 可合法出现在多个维度下，故按 `(dimension_key, rule_id)` 联合键计数。真实 `run 49` 中 `minor_defect` 分属两个维度（命中 8 次与 3 次），未被错误合并为一条。
- 模型报出合同未声明的 `rule_id` 时明确暴露，不静默忽略——这通常意味着提示词与合同的规则 id 不一致。

## 验证

- 后端全量 `1729 passed, 1 skipped`（原 `1708`，新增 21 项：变基 11 项、诊断 10 项）。
- 前端 `tsc -b --force` rc=0，`npm run build` rc=0。
- 变基测试覆盖：候选改动被采纳、现役独有新增不丢且不误报冲突、双方改同一值报冲突、双方各自新增不同内容报冲突、内容相同的独立新增不报冲突、候选删除被采纳但现役改过则冲突、列表原子取舍、兄弟分支共同祖先定位、直接子代报无需变基、非候选状态与跨类目拒绝、无共同祖先拒绝。
- 诊断测试覆盖：规则扁平化、跨赛道同规则只计一次、规则层空转标记、部分命中逐条计数、加分命中与未声明命中暴露、分数分桶与红线摘要、空 run 不误报空转、未评分条目不计入规则统计但等级计数如实、中位数不被离群值带偏、禁用等级不参与落档映射。
- 部署前 `--dry-run` 通过，提交号一致；`git diff --check` 干净；新增文件无调试残留。
- 三端点部署后均返回 `401`（注册成功且鉴权生效），非 `404`。

真实数据验证（测试服，只读，未落库、未激活任何候选）：

- 变基 `Revision 7` → 现役 `Revision 8`：正确定位共同祖先 `Revision 5`，0 冲突，带过 7 处改动；现役独有的 `b_aesthetic_foundation` 完整保留；候选红线策略（`hit_level: L3`、`hit_score_cap: 60`）如实带入。
- `Revision 9`（已在现役链上）正确报告无需变基。
- `run 49` 诊断：声明 15 条规则，12 条从未命中，39/50 样本零命中，无扣分时落档 `L1`，红线触发 0 次，等级分布 `L1:41 L2:8 L3:1`。

## 部署结果

- 测试服务器：`192.168.1.35:8081`
- 部署前服务器提交：`b715e64`
- 部署后服务器提交：`97cbe27304d1338d9f96c7afd633162340c760bb`，worktree 无改动
- 容器：`3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`：`ok`；`/api/health/ready`：`ready`；8 个 worker 活跃
- 数据库：integrity `ok`，FK `[]`，`schema_migrations` `76`
- NAS：`/mnt/label-nas/maps` 保持 `cifs (ro)` 只读挂载
- 健康检查第 4 次轮询达到 healthy，`DEPLOY_RC=0`
- 磁盘：部署后可用 `13G`（87%）
- 部署日志：`2026-08-21/codex-threads-01a02397-ebd3-71d3-badc/work/deploy-97cbe27.log`（位于 `~/Documents/Codex` 下的会话工作目录，不在本仓库内）

产物核验：容器内存在 `candidate_rebase.py` 与 `baseline_rule_diagnostics.py`，`rebase-preview` 与 `rule-diagnostics` 路由各 1 处。

## 过程中的自我纠正

- 曾依据 20 张抽样称「模型一条扣分规则都没命中」。诊断模块在全量数据上给出更准确的结论：15 条规则中有 3 条命中过（`run 48` 共 11 次，`run 49` 共 12 次），故 `rule_layer_inert` 为 `False`。准确表述是「12 条从未命中、约 8 成样本零命中」，方向不变但「一条都没有」的绝对说法有误。
- 手写三方合并脚本时曾把「祖先无、现役有、候选无」误判为双方各自新增，虚报 4 处 `b_aesthetic_foundation` 冲突。生产代码为此分支单独建立测试锁定行为。
- `diverged_candidates` 与前端 `listDivergedCandidates` 一度实现但无消费方（前端已用 `v3CandidateLineage` 从既有接口本地推导分叉状态），随后连同类型定义与对应测试一并删除，避免留下无调用方代码。
- 首次核查路由注册时使用了错误的探针方式，误判为「变基路由未注册」；改用 `TestClient` 实际请求确认三端点均返回 `401` 而非 `404`。

## 遗留（未做，需 Owner 决定）

`run 49` 加候选 `Revision 9` 仍不建议发布，但阻塞原因已不在机制层：

- 精确准确率 `0.26`（`run 48` 为 `0.24`），相邻 `0.58`（原 `0.50`）；`L4`/`L5` 判级仍为 0。
- 门禁报告 8 项关键字段回退与 `golden_set_failure`（33 个锁定黄金真值样本存在字段失败）。两轮使用同一对提示词，故字段回退只能归因于合同改动或模型自身不确定性；50 张样本规模下 3%~9% 的波动很可能是噪声，无证据断定由合同造成。
- 更关键的错配：`Revision 9` 合同声明的 A/B 为 id `84`/`85`（均 `published`，rubric `model-3d-su-rubric-v4`），类目 profile 配置的也是这一对，即**激活后生产实际使用 84/85**；而 `run 49` 验证用的是 id `83`/`78`（均 `draft`，rubric `rubric-v2.1`）。作为实验有效且留痕，但作为发布资格验证不成立。若要发布 `Revision 9`，应改用 84/85 再跑一轮——该对与合同声明完全一致，无需覆盖记录，改动前的严格门禁亦可通过，且验证对象即生产实际使用的一对。

诊断已定位根因不在合同：`authoritative_score` 普遍为 88，`level_scale` 中 `L1` 门槛为 80，多数样本因零规则命中而直接落 `L1`。应优先检查调用 B 为何不报出缺陷，而非继续调整阈值。

## 本次未做

未修改 `candidate_ancestry_conflict` 血缘检查语义（变基是绕开分叉的正向路径，而非放开检查），未修改质量门禁阈值，未激活任何候选版本，未改变现役发布指针，未改动 `worker.py` 执行期一致性校验，未改动提示词内容，未新增回归运行（本次零模型调用），未部署 MacBook `8080`，未连接公司 Windows，未写业务生产数据，未修改生产环境。
