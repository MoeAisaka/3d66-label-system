# NAS Source Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让标签实验台以只读方式引用 `//192.168.1.51/maps` 上的原图，避免测试服继续复制 NAS 素材到本地磁盘。

**Architecture:** 在 `Asset` 和 `AssetVersion` 中保存规范化 `nas://maps/...` 来源引用；统一路径解析器在本地素材和 NAS 素材之间选择实际文件，并对 NAS 根目录、路径穿越、符号链接和 SHA-256 做 fail-closed 校验。新增 NAS 导入接口和素材页入口，导入只读元数据并创建素材包，不复制原图。测试服通过只读 SMB 主机挂载和容器只读卷暴露该根目录。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、SQLite migration runner、React/TypeScript、Docker Compose、SMB/CIFS。

**Spec:** `docs/superpowers/specs/2026-08-19-nas-source-reference.md`

## Global Constraints

- 只允许 `//192.168.1.51/maps`，应用保存 `nas://maps/<相对路径>`。
- 不保存凭据，不把 UNC 绝对路径写入数据库，不删除现有本地图片。
- NAS 挂载与容器映射必须只读；挂载缺失时部署 fail-closed。
- 既有本地素材、评测、模型、提示词和并行工作树行为不变。
- 仅在历史素材逐条通过来源可读、SHA-256 一致、API/评测路径可用且数据库无现行引用后，清理 `/data/images` 中对应的本地原图；异常文件保留。

### Task 1: NAS URI 与文件解析器

**Files:**
- Create: `backend/app/nas_storage.py`
- Test: `backend/tests/test_nas_storage.py`

- [ ] 写测试：规范化 UNC/URI、拒绝其他主机/共享/穿越、解析根目录内文件、拒绝符号链接逃逸、读取元数据和哈希。
- [ ] 运行 `cd backend && python -m pytest tests/test_nas_storage.py -q`，确认在缺少实现时失败。
- [ ] 实现 `normalize_nas_uri`、`resolve_nas_uri`、`inspect_nas_file` 和 `resolve_asset_path` 的最小行为。
- [ ] 重跑该测试并通过。

### Task 2: 数据模型与迁移

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/tests/test_migration.py`
- Test: `backend/tests/test_nas_storage.py`

- [ ] 先增加迁移测试，验证旧 `assets` 表升级后出现 `storage_backend`、`source_uri`，旧行默认为本地。
- [ ] 添加第 76 版迁移和 SQLAlchemy 字段；为 `AssetVersion` 保存来源引用。
- [ ] 运行迁移测试和 NAS 单元测试。

### Task 3: 后端路径消费与 NAS 导入接口

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/optimizer.py`
- Modify: `backend/app/inspiration_aesthetic_foundation.py`
- Modify: `backend/app/field_demand_contracts.py`
- Create: `backend/tests/test_nas_assets_api.py`

- [ ] 先写 API 测试：NAS 导入创建引用素材包且不写本地图片；列表返回来源信息；文件接口读取 NAS 文件；缺失挂载或哈希变化返回明确错误。
- [ ] 增加 `NAS_MAPS_ROOT` 配置和统一路径解析调用，替换散落的 `upload_dir / stored_name`。
- [ ] 增加受权限保护的 `POST /api/assets/import-nas`，支持单文件/目录递归、类别校验、去重、跳过清单和素材包创建。
- [ ] 同步 `Asset`/`AssetVersion` 载荷、审计和来源版本记录。
- [ ] 运行 API 测试及受影响的后端回归测试。

### Task 4: 前端素材页入口与类型

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/pages/assets-page.tsx`
- Create/Modify: `frontend/scripts/check-nas-source-contract.ts`

- [ ] 先写合同断言，要求素材页存在 NAS 相对路径输入、只读说明和导入结果刷新。
- [ ] 增加 NAS 导入表单，示例使用四条用户给出的目录格式；不展示或收集凭据。
- [ ] 增加来源标识和不可用状态展示，保留现有上传流程。
- [ ] 运行合同断言、类型检查和生产构建。

### Task 5: 测试服只读挂载与部署门禁

**Files:**
- Modify: `docker-compose.yml`
- Modify: `scripts/deploy-test-server.sh`
- Create: `scripts/configure-nas-test-server.sh`
- Create: `scripts/verify-nas-test-server.sh`
- Create: `docs/runbooks/nas-source-reference-test-server.md`

- [ ] 脚本只接受受保护凭据文件，不接受命令行密码；安装 `cifs-utils`、创建只读挂载和验证 `ro` 选项。
- [ ] Compose 以只读方式映射 `/mnt/label-nas/maps`，设置 `NAS_MAPS_ROOT`。
- [ ] 部署门禁在 NAS 功能存在但挂载缺失时停止，失败时保留现版本。
- [ ] 先在测试服只读检查网络和磁盘，再配置挂载；不删除文件。
- [ ] 运行脚本静态检查和部署 dry-run。

### Task 6: 集成验证与交接

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/decisions/README.md` only if ADR index requires it.

- [ ] 后端全量测试、前端合同测试、类型检查、构建和 `git diff --check`。
- [ ] 测试服验收：挂载只读、容器健康、`/api/health/ready`、NAS 文件接口和 SHA-256 一致。
- [ ] 记录回退命令、未删除本地文件和剩余风险。

### Task 7: 验收后的本地原图清理

**Files:**
- Create: `backend/app/nas_history_migration.py`
- Create: `backend/tests/test_nas_history_migration.py`
- Create: `scripts/migrate-nas-history.py`
- Modify: `docs/runbooks/nas-source-reference-test-server.md`

- [ ] 生成待清理清单：只包含已规范化为 `nas://maps/...`、NAS 文件存在且 SHA-256 与数据库记录一致、并且数据库没有任何现行本地路径引用的 `/data/images` 文件。
- [ ] 在删除前保存清单、计数、总字节数和 SHA-256 汇总到服务器受保护目录；清单为空或校验失败时停止。
- [ ] 按清单逐个删除并立即复核文件不存在；任何不满足条件的文件都保留，不使用递归清空目录。
- [ ] 记录清理结果和回退限制：数据库记录不删除，NAS 文件不修改，异常文件留在本地等待人工处理。
