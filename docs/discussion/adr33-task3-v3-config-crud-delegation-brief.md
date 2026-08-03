# ADR-0033 Task 3 委派任务书（v3 类目合同 持久化 + CRUD + 前端编辑器）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。测试/构建/git 由 OpenClaw 侧做。

## 定位与安全边界（先读，硬约束）

背景：ADR-0033 框架层七件已完成，v3 合同（红线 + 子类目赛道 + 共性/特有维度组 + 分类映射）目前**只有 preview/dry-run API，零持久化、零 CRUD**。本任务给 v3 合同做「存 → 读 → 改 → 校验」的隔离 CRUD + 前端可视化编辑器。

**绝对不许碰**（这些是 Task 1 / Task 2 的范围，本任务越界即失败）：
- ❌ `worker.py`、`scoring.py`、`category_pipeline.py` 的线上算分路径 —— 一行都不动。
- ❌ 旧的 `EvaluationCategoryProfile` / `category-pipeline-v1` / `validate_pipeline_config` / `/api/evaluation-categories` 那套 —— 那是 v1 流水线，与 v3 合同**完全隔离**，不复用它的表、不改它的端点。
- ❌ `PublishedLabel`、L 方向语义、任何已发布数据 —— 不读不写。
- ❌ 不做数据库真实迁移执行（不跑 migration）。只**新增** migration 定义文件，由 OpenClaw 侧审后执行。

**边界**：只用 Read / Edit / Write / Glob / Grep。禁 Bash / git / 网络 / 安装 / 运行测试 / 起服务。

## 必读（理解现有范式，照抄风格）

后端：
- `backend/app/inspiration_category_seed.py`：v3 合同装配 `build_inspiration_v3_contract` / `build_inspiration_classification_map` / `build_inspiration_subcategory_dimensions`。**这是 v3 合同的 canonical 结构，你存的就是这个形状。**
- `backend/app/redline_policy.py` / `subcategory_resolver.py` / `dimension_composition.py` / `category_evaluation_contract.py`：**已有的确定性校验器**。CRUD 的写入校验必须复用这些，不要重写校验逻辑。
- `backend/app/category_evaluation_preview_api.py`：隔离路由工厂范式 `def build_*_router(require_user): ...; return router`，以及 `_coded_400` 把框架异常转成带 code 的 HTTP 400（禁 500）。照抄。
- `backend/app/models.py`：SQLAlchemy 模型风格（`Mapped`/`mapped_column`/`CheckConstraint`/`UniqueConstraint`/`canonical_json`/`utcnow`）。看 `EvaluationCategoryProfile`(301 行附近) 学风格——**但你要建的是独立新表，不是复用它**。
- `backend/app/migrations/`：migration 定义文件的写法（看最新一个 migration 文件的结构，version 号顺延）。

前端：
- `frontend/src/pages/category-evaluation-preview-page.tsx`：我上一阶段写的预览页，读 `/api/category-evaluation/preview/*`。你的编辑器页风格与它一致（PageHeader / EvaluationBoundaryNote / api() / ApiError）。
- `frontend/src/lib/api.ts`：`api<T>(path, init)` + `ApiError`。
- `frontend/src/App.tsx` + `frontend/src/components/app-shell.tsx`：路由与「高级设置」tab 注册方式（看预览页是怎么挂进去的，照做）。

## 必做

### 后端 1：新表（`backend/app/models.py` 追加，不改现有类）
新增 `class CategoryEvaluationV3Config(Base)`，独立表 `category_evaluation_v3_configs`：
- `id` PK；`category_key` String(40) 唯一索引（`uq_...`）；`display_name` String(120)；
- `status` String(20) CHECK `IN ('draft','active','retired')` 默认 'draft'；
- `contract_json` Text（存 build_inspiration_v3_contract 形状的完整合同）；
- `classification_map_json` Text；`subcategory_dimensions_json` Text；
- `revision` Integer 默认 1（每次更新 +1）；
- `contract_hash` String(64)（用 `category_evaluation_contract` 里现成的 `canonical_hash` 算）；
- `created_by`/`created_at`/`updated_at`（照 EvaluationCategoryProfile 的写法，`onupdate=utcnow`）。

### 后端 2：migration 定义（`backend/app/migrations/` 新增一个文件）
- version 号 = 当前最大 +1（先 Glob 看现有最大号）。
- 只做 `CREATE TABLE category_evaluation_v3_configs`（含约束/索引）。**不要**改动或迁移任何现有表数据。
- 若 runner 需要在某个清单里登记新 migration，按现有 migration 的登记方式登记（看别的 migration 怎么被 runner 收录的）。

### 后端 3：隔离 CRUD 路由（新建 `backend/app/category_evaluation_v3_config_api.py`）
工厂 `def build_category_evaluation_v3_config_router(require_user):`，prefix `/api/category-evaluation/v3-config`，全部 `Depends(require_user)`：
- `GET  /` 列出所有 v3 config（返回 id/category_key/display_name/status/revision/contract_hash/updated_at）。
- `GET  /{category_key}` 取单个完整 config（contract + classification_map + subcategory_dimensions）。404 用 coded error。
- `POST /` 新建：请求体 pydantic（category_key/display_name/contract/classification_map/subcategory_dimensions）。**写入前必须**用现有校验器校验：`evaluate_redlines` 能吃这份红线策略、`subcategory_resolver` 的合同校验通过、`dimension_composition` 的共性/特有组配置合法、`category_evaluation_contract` 的合同校验通过。任一校验失败 → `_coded_400`（禁 500）。校验通过再落库，算 contract_hash。category_key 重复 → coded 409/400。
- `PUT  /{category_key}` 更新：同样先校验再落库，revision +1，重算 hash。
- `POST /{category_key}/validate` 干校验不落库（复用 POST 的校验逻辑），返回 ok + 聚合的 coded 错误列表。
- **不做 DELETE**（retired 用 PUT 改 status 即可，避免误删已引用配置）。
- 所有端点纯 CRUD + 校验，**不入队、不发布、不调用模型、不 touch worker**。

### 后端 4：注册钩子（**不改 main.py**）
- 你只提供可注册的工厂函数。像 preview API 一样，main.py 里加一行 `include_router(...)` 由 OpenClaw 侧做。在任务书末尾的 DONE 文件里注明「需 OpenClaw 在 main.py 注册 build_category_evaluation_v3_config_router」。

### 后端 5：测试（新建 `backend/tests/test_category_evaluation_v3_config_api.py`）
- TestClient + `require_user` override（照 test_category_evaluation_preview_api.py）。
- 覆盖：建→查→改→revision递增→hash变化；非法合同（坏红线/坏子类目/坏维度组）被 coded 400 挡下且不落库；重复 key 被拒；validate 端点不落库；未登录 401。
- 用**独立临时 DB**（照现有 API 测试的 DATA_DIR/DATABASE_URL 隔离方式），不污染真实库。

### 前端：v3 配置编辑器页（新建 `frontend/src/pages/category-evaluation-v3-config-page.tsx`）
- 路由 `/workflow/optimization/category-evaluation-v3-config`，挂「高级设置」tab（照预览页注册方式改 App.tsx + app-shell.tsx）。
- 功能：列出 v3 config；选中后可编辑红线规则（增删/开关/match 词）、子类目赛道（key/label/base_score/dimension_max/track_cap/default）、每子类目共性+特有维度组（增删维度、权重）、分类映射；「校验」按钮调 `/validate`、「保存」调 PUT/POST；错误用 ApiError 的 coded 展示。
- 嵌入 `EvaluationBoundaryNote`。顶部标注「本页编辑的是 v3 合同配置，保存后需经金丝雀/回归验证方可用于线上（当前 worker 尚未接入 v3）」。
- 只用 tsc 认识的 API；图标从 @phosphor-icons/react 里挑**确实导出**的（如 Plus/Trash/FloppyDisk/Check/WarningCircle，参考预览页用过的）。**不要**用 FlaskframeworkLogo（该版本未导出）。

## 完成信号
- 全部完成后写 `ADR33_TASK3_DONE.md`：列出新建文件清单、需 OpenClaw 在 main.py 注册的那行、migration version 号、任何你不确定的点。
- 结尾输出 `DONE`。

## 提醒
- 你**不能**跑测试/build/git。写完就停，OpenClaw 会 py_compile + pytest + tsc + build + 双推 codeup。
- 有疑问或发现现有代码与本书冲突，**停下写进 DONE 文件问**，不要自作主张改现有生产文件。
