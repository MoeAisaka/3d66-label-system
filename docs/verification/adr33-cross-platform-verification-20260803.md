# ADR-0033 框架 + 并发调整 · 跨平台验证报告（2026-08-03）

分支：`fix/baseline-fields-throughput-20260803`
范围：本次交付 = 并发默认 2→8（migration 51）+ ADR-0033 Phase 1/2 确定性底座（红线策略、v3 合同、评测聚合器）。全部为**新增纯函数模块 + 一条只抬旧默认值的迁移**，未改动生产执行路径、未做 L 方向全局翻转。

## 一、Mac（开发/生产宿主，Apple Silicon, macOS 26）

| 操作 | 结果 |
|---|---|
| 后端全量 pytest | **799 passed, 1 skipped** |
| 新模块单测（红线40+合同+聚合器） | redline+contract 41 passed；aggregator 32 passed |
| `compileall app` | 通过 |
| 前端 `npm run build` | 通过（仅 chunk>500KB 提示，无错误） |
| Windows/macOS/security 相关测试子集 | 83 passed, 1 skipped |
| 新模块平台耦合扫描 | 无 `sys.platform`/`ctypes`/Keychain/DPAPI/subprocess/文件 IO —— 完全可移植 |

## 二、Docker / Linux（python:3.12-slim + node:22-alpine 多阶段）

| 操作 | 结果 |
|---|---|
| `docker build`（前端构建 + Python 依赖 + 拷贝） | 成功，镜像 `3d66-label-system:adr33-test` |
| 容器启动 + `/api/health` | `{"status":"ok"}` |
| **migration 51 在 Linux 应用** | 容器内 `max(schema_migrations.version)=51` |
| **并发默认生效** | 容器内 `model_configs.max_concurrency=8`；日志实际 spawn **8 个 Worker** |
| 新模块 Linux 运行时导入 + 执行 | `LINUX_RUNTIME_OK`：AI图 85→L1、红线截图→L5 且 score 封顶 49、hard_reject |
| 密钥后端 | Linux/Docker 走 `file-aead`（`API_KEY_MASTER_KEY_FILE`，compose 已配），与 macOS Keychain / Windows DPAPI 三路隔离，互不串读 |
| `.dockerignore` | 正确排除 `backend/tests`、`.venv`、`node_modules`、`*.db`（生产镜像不带测试与本地态） |

## 三、Windows（DPAPI 密钥 + LOCALAPPDATA 数据目录 + 部署生命周期）

无 Windows 物理机，按代码与测试覆盖确认（新代码不触碰 Windows 专属路径）：

| 面 | 覆盖 |
|---|---|
| 密钥后端 DPAPI（current-user / local-machine 两 scope） | `security.py` 分支 + probe，测试覆盖；`unprotect_secret` 跨平台密文互斥校验（macOS 引用不能在 Win 读，反之亦然） |
| 数据目录 | `windows_deploy.default_windows_data_dir` 读 `LOCALAPPDATA`，缺失 fail-closed |
| 部署生命周期 | `doctor / create_backup / validate_backup / restore_backup / main` 均有测试；reparse-point（junction/symlink）拒绝、仓库外备份、data 外备份等安全校验通过 |
| 新模块对 Windows 的影响 | 无（纯函数、无平台调用），Windows 部署路径不受本次改动影响 |
| 部署/deploy + security 测试子集 | 83 passed, 1 skipped（含 Windows 分支的可跨平台部分） |

> Windows 专属的 DPAPI 加解密真机往返（`probe_windows_dpapi`）只能在 Windows 上跑；本次未改该路径，历史公司 Windows 生产（windows-deploy 分支）已部署验证过。建议合并前在公司 Windows 机跑一次 `python -m app.windows_deploy doctor` 做最终确认。

## 四、操作与可能性枚举（本次交付面）

### 并发（migration 51）
- 全新库：seed 出的主评测模型 `max_concurrency=8`；launcher clamp `1..10` → spawn 8 workers。✔ Linux 实测
- 旧库仍处默认 2：迁移抬到 8。✔ 单测 `test_v51_*`
- 旧库操作员调过（如 4/1/10）：**保留不动**。✔ 单测
- 前端新建模型表单默认 8；范围 `min=1 max=10` 不变。✔
- 边界：迁移幂等（重复运行不重复抬值）。✔ 单测

### 红线策略（redline-policy-v1）
- 命中（截图/随手拍/大面积文字/二维码）→ `hard_reject` + `hit_level` + score 封顶。✔
- 豁免命中（如大面积文字豁免专业海报证据）→ 该规则不计。✔
- `enabled=false` → 直通不淘汰。✔
- 非法（未知 signal / 空 rules / 重复 key / 非法 reason 枚举 / 非法 hit_level / cap 越界）→ fail-closed。✔
- 复用调用A的 `PRODUCTION_REASON_VALUES`，不新造枚举。✔

### v3 合同（evaluation-category-profile-v3）
- 三块（redline / track_classification / common_modifiers）齐全校验；缺块 fail-closed。✔
- track：key 唯一/命名、`base+dim<=cap<=100`、default_track 存在。✔
- common_modifiers：媒介四键齐全且<=0、基准键必须0、veto `cap_to<threshold`。✔
- canonical hash 键顺序无关、同结构同值。✔

### 评测聚合器（doc-l5-worst-v1，L5=最差）
- 红线命中直出 L5 + 封顶 49 + 终止，不进入后续步骤。✔
- 赛道上限：一类≤100、二类≤80、三类≤70。✔
- 媒介降权：实拍0 / 效果图-5 / AI-15；trait 缺失/未知 → other 并记不确定性。✔
- 维度扣分累计 clamp 到 dimension_max（净贡献不为负）。✔
- 高分一票压分：score≥80 且有 hard_defects → 79；raw_level 保留压分前。✔
- 分数→L 边界 80/60/40/20；<20→L5（非红线）。✔
- 默认赛道回退、level_thresholds 可被合同覆盖、非法 fail-closed、确定性、JSON 可序列化。✔

## 五、尚未做（明确边界，后续独立阶段）

- **未接生产 worker 执行路径**：聚合器是独立可回归引擎，尚未替换 `scoring.py`/worker 的现有评分。
- **未做 L 方向全局翻转迁移**：`scoring.py` 仍 L5=最优；已发布标签/消费接口未动（ADR-0033 第五节要求独立分支 + `level_semantics_version` + 历史发布不原地改写 + 独立回归门禁）。
- **未做前端类目配置 UI + A/B/维度边界说明**。
- Windows 真机 DPAPI 往返与公司 Windows 部署 doctor 建议合并前跑一次。

## 结论

本次交付（并发 8 + Phase 1/2 确定性底座）在 **Mac 与 Docker/Linux 实测通过**，Windows 路径按测试覆盖与代码隔离确认不受影响。全部改动向后兼容、可回归、未触碰生产评分与已发布数据。可推 codeup 特性分支。
