# ADR-0033 Phase 4-preview 委派任务书（只读+dry-run 评测预览 API · 隔离路由）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 定位与安全边界

框架层七件已完成。本阶段做**只读 + dry-run 的隔离 API 路由**，把已装配的灵感图 v3 合同和端到端 `evaluate_one` 暴露给前端预览/联调。**绝不 touch 生产评分路径、worker、DB 写入、已发布标签、L 方向迁移**——这是无人值守夜间任务，只允许纯只读/纯计算。

## 边界（硬约束）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- **不改任何现有文件**（含 main.py）。只新建：
  1. `backend/app/category_evaluation_preview_api.py`
  2. `backend/tests/test_category_evaluation_preview_api.py`
- 路由只读合同 + 对请求体做纯计算，**不写 DB、不入队、不发布、不调用模型**。

## 背景（先读）

- `backend/app/inspiration_category_seed.py`：`build_inspiration_v3_contract` / `build_inspiration_classification_map` / `build_inspiration_subcategory_dimensions` / `evaluate_one`。
- `backend/app/p0e_canary_api.py`：隔离路由工厂范式 `def build_*_router(require_user): router = APIRouter(prefix=..., tags=...); @router.get/post ...; return router`。用 pydantic BaseModel 定义请求/响应。
- `backend/app/main.py`：`current_user` 依赖、`include_router(build_*_router(current_user))` 注册方式（本阶段**不改** main.py，仅提供可注册的工厂；注册由 OpenClaw 侧加一行）。

## 必做（`category_evaluation_preview_api.py`）

```python
def build_category_evaluation_preview_router(require_user):
    router = APIRouter(prefix="/api/category-evaluation/preview", tags=["category-evaluation-preview"])
    ...
    return router
```

三个端点（全部需要登录 `Depends(require_user)`）：

1. `GET /api/category-evaluation/preview/inspiration/contract`
   - 返回已装配的灵感图 v3 合同 + classification_map + subcategory_dimensions（只读，直接调 seed 的 build_* 函数）。
   - 响应体清晰分区：`{contract, classification_map, subcategory_dimensions, seed_version}`。

2. `POST /api/category-evaluation/preview/inspiration/evaluate`
   - 请求体（pydantic）：`precheck`(dict)、`common_grades_by_track`(dict)、`specific_grades_by_track`(dict)。
   - 调 `evaluate_one(contract=build_..., classification_map=build_..., subcategory_dimensions=build_..., precheck=..., common_grades_by_track=..., specific_grades_by_track=...)` 并返回结果。
   - 纯 dry-run：不写任何存储。捕获底层 ValueError/各模块 *Error → 返回 HTTP 400 带 `code` 和 message（不要 500）。

3. `POST /api/category-evaluation/preview/validate`
   - 请求体：`contract`(dict, 可选)、`classification_map`(dict, 可选)、`subcategory_dimensions`(dict, 可选，形如 {track_key: config})。
   - 分别调 `validate_category_evaluation_contract` / `validate_classification_map`(valid_track_keys 从传入 contract 的 tracks 提取，缺失则从灵感图 seed 合同提取) / 逐个 `validate_subcategory_dimensions`。
   - 返回 `{contract_valid, classification_map_valid, subcategory_dimensions_valid, errors: [{target, code, message}]}`；任何校验失败不抛 500，聚合到 errors 且对应 *_valid=false。

用 pydantic BaseModel 定义请求/响应；`response_model` 可用 dict 宽松结构（`ConfigDict(extra="allow")` 或直接 `dict[str, Any]` 返回）。

## 测试（`test_category_evaluation_preview_api.py`）

用 FastAPI TestClient + 一个最小 app（`app=FastAPI(); app.include_router(build_category_evaluation_preview_router(_fake_require_user))`，`_fake_require_user` 返回一个占位 user 对象或 None，绕过真实鉴权）覆盖：
- GET contract → 200，含 contract/classification_map/subcategory_dimensions/seed_version，且 contract.schema_version 正确。
- POST evaluate 红线命中（reason=["是截图"]）→ 200，result.hard_reject true、level L5、score 49。
- POST evaluate 建筑设计 grade5 实拍 → 200，result.track_key class_one、score 100、level L1。
- POST evaluate 非法输入（如 grade 越界）→ 400 带 code（非 500）。
- POST validate 合法灵感图三件 → 全 *_valid=true、errors 空。
- POST validate 故意破坏（如 contract 改 schema_version）→ contract_valid=false，errors 含对应 code。
- 确定性：同请求两次同响应。

参考 `p0e_canary_api` 测试如何用 TestClient 和 require_user 覆盖。

## 完成信号

写 `ADR33_PHASE4PREVIEW_DONE.md`（文件、端点、请求/响应结构、覆盖场景、确认零写入），写完即停，等 OpenClaw 验收。
