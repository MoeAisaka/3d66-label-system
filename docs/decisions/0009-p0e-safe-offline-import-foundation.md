# ADR-0009：P0-E 安全离线导入、图片冻结与候选包基础

- 状态：Accepted
- 日期：2026-07-28

## 背景

标签实验台需要从历史 XLSX 中筛选 3D 样本，并为后续人工确认准备可追溯的本地图片副本。来源表包含重复表头、`farmat` 拼写、缺失人工等级/分类、重复 URL 和冲突记录；图片 URL 还引入 SSRF、DNS rebinding、重定向、内容欺骗与半成品落盘风险。

P0-E 的 E0/E1 只建立安全工程基础。本阶段不连接真实来源、不下载真实图片、不调用模型、不写业务数据库，也不把候选预览宣称为 Gold。

## 决策

### XLSX 只读预检（`backend/app/p0e_safe_import.py`）

- 仅接收 `.xlsx`，使用受限 ZIP/XML 只读解析，拒绝公式、宏、ActiveX、嵌入对象、外部关系、异常路径、重复 ZIP 条目、加密条目、可疑压缩比、超限文件与不安全 XML（DOCTYPE/ENTITY）。
- 重复表头的每个位置生成稳定内部名，例如 `status__col_3`、`status__col_19`；RAW 表头与 RAW 单元格值原样保留在预览中，超出最后一个命名表头的数据也不丢弃。
- 字段映射只是候选。`farmat → format` 会显示为需人工确认的已知拼写候选（`reason=known_typo_alias`、`applied=False`）；与真实 `format` 同时存在时显式报告冲突，`applied_mapping` 始终保持为空。
- 预检按文件字节 SHA-256、Schema、目标域和列计划生成稳定批次键，保证幂等。预检不创建 Asset、SampleSet、真值或其他业务数据（`writes_business_database=False`）。
- Gold 目标必须显式提供锁状态；状态缺失或已锁定均 fail-closed，不提供覆盖锁的旁路。

### 受控图片获取与冻结（`backend/app/p0e_image_freeze.py`）

- 默认没有域名白名单，因而拒绝任意 URL。启用时只允许显式精确域名、HTTPS、443 端口且禁止 userinfo；URL 中的控制字符直接拒绝。
- 每一跳解析全部 A/AAAA；任一地址属于 loopback、private、link-local、multicast、reserved、unspecified 或其他非公网范围时整跳拒绝。每次重定向重新执行 URL、白名单和 DNS 校验，并限制跳数与循环。
- 防 DNS rebinding 的必要条件是传输层把实际连接固定到本次已验证 IP，同时以原域名完成 Host/SNI。当前通用 HTTP 客户端未证明满足该条件，因此默认传输显式返回 `DNS_PINNING_UNAVAILABLE`；后续只有实现该固定 IP 契约的受控适配器才能执行真实获取。
- 限制连接/读取超时、Content-Length 和实际流大小；只接受 JPEG、PNG、WebP，并同时校验 Content-Type、文件头魔数与 Pillow 完整解码结果，三者不一致即拒绝。
- 错误与持久化来源 URL 只保留 scheme、host、port、path，不保留 userinfo、query 或 fragment，避免日志泄漏令牌。
- 图片先流式写入同文件系统临时目录；完整验证后按字节 SHA-256 生成稳定 `asset_id` 并原子落盘。相同字节复用同一冻结文件；中断时临时文件被清理，不会留下半成品。

### Manifest 与候选包（`backend/app/p0e_image_freeze.py`、`backend/app/p0e_candidate_package.py`）

- Manifest 使用版本号、确定性排序、SHA 去重和同目录原子替换。只有成功来源数等于预期且没有错误时才可标记 `complete`；失败或缺件必须为 `incomplete`。
- 冻结记录至少保存 `domain`、`source_file`、`source_row`、`source_business_id`、安全 `source_url`、`content_sha256`、真实 MIME、宽高、`imported_at`、`historical_grade`、`historical_category`、`truth_status` 和 `sample_role`。
- 30～50 张候选包仅做离线、固定 seed 的确定性分层预览，按类目、等级和风险尽量覆盖，返回 `downloads_performed=false`、`model_runs_performed=false`。
- 非 3D、缺人工等级/分类、3Dreason 缺人工真值、重复 URL 和冲突样本默认排除并保留可机读原因。候选包始终返回 `forms_gold=false`。

## 后果

- P0-E 可以在不触碰真实数据和模型的条件下验证导入计划、拒绝路径、冻结一致性和候选抽样方法。
- 真实图片下载仍被安全门禁阻断，直到有经过测试和审查的固定 IP HTTPS 传输适配器。
- 表头映射和历史真值仍需人工确认；候选预览不是 Gold，也不能进入发布回归。
- 本阶段没有引入通用 `Pipeline` 或 `Candidate` 实体，没有修改数据库 Schema、API、前端，也没有改变既有 `Asset 1:N EvaluationResult`、`evaluation_id` 审核、StrategyBundle、五类队列或 P0-A/B/C.1/D 合同。
- 测试导入根：新增 `backend/tests/conftest.py`，仅把 `backend/` 加入 `sys.path`，使测试在仓库根与 `backend/` 两种工作目录下都可导入 `app`；从 `backend/` 运行时为无操作，不改变任何测试行为。

## 不可破坏约束

- 不得为了“可下载”而在 DNS 校验后让普通客户端再次自行解析域名。
- 不得静默应用 `farmat → format` 或覆盖重复列。
- 不得把下载失败、解码失败、缺件 manifest 或不足 30 张的预览标记为完整 Gold。
- 不得用本导入基础绕过 Gold 锁、人工真值、StrategyBundle、五类队列、`Asset 1:N EvaluationResult` 或 `evaluation_id` 审核合同。
