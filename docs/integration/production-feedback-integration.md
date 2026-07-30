# 生产纠偏事件回流对接说明

## 适用范围

本接口只接收生产系统已经完成并落地的最终人工纠偏事件，将其映射为 LabelLab
实验台的优化案例。接收端不会连接、修改或反向写入生产数据库，也不会自动
运行优化模型、发布提示词或切换生产模型。

## 端点与鉴权

- 方法：`POST /api/production-feedback-events`
- 内容类型：`application/json`
- 认证：`Authorization: Bearer <专用机器 token>`
- 服务端配置：环境变量 `PRODUCTION_FEEDBACK_TOKEN`
- 浏览器 Cookie、管理员会话和管理员密码均不能代替机器 token。
- 服务端未配置 token 时返回 `503`；请求缺少 token 或 token 错误时返回
  `401`。比较使用恒定时间比较。
- 部署间通信应使用 HTTPS。当前合同不定义额外的 payload HMAC 签名；Bearer
  token 是当前唯一受支持的认证方式，不应发送后端不会验证的自定义签名。

Token 只存在于发送端秘密配置和 LabelLab 服务端环境变量中。不得写入事件
JSON、URL、日志、截图、文档、数据库业务字段或前端配置。

## 幂等键

`event_id` 是幂等键，建议格式为生产系统稳定命名空间与不可变事件标识的组合，
例如 `production-review:<不可变事件ID>`。同一个最终人工纠偏只能生成一个
`event_id`，不得使用发送时间或随机数重建。

- 同一 `event_id`、同一完整事件载荷：返回原事件，`duplicate=true`。
- 同一 `event_id`、不同 schema、事件类型、来源、发生时间或 payload：返回
  `409`，不会覆盖原事件。
- 网络超时且无法确认响应时，可以使用完全相同的 JSON 安全重放。
- 发送方不得在重试时更新 `occurred_at`、调整字段顺序以外的内容或重新生成
  `event_id`。

## 事件 Schema

当前只接受 `production-feedback-v1`：

```json
{
  "event_id": "production-review:<不可变事件ID>",
  "schema_version": "production-feedback-v1",
  "event_type": "human_correction_finalized",
  "source_system": "<生产系统稳定标识>",
  "occurred_at": "2026-01-01T00:00:00Z",
  "payload": {
    "production_case_id": "<生产案例稳定ID>",
    "prompt_version": "<实际使用的提示词版本>",
    "severity": "P0",
    "model_output": {},
    "human_truth": {},
    "reason_codes": [],
    "production_applied": true
  }
}
```

必填约束：

- `event_type` 只能是 `human_correction_finalized`。
- `severity` 只能是 `P0`、`P1`、`P2`、`P3`。
- `model_output` 与 `human_truth` 必须是 JSON 对象。
- `prompt_version` 必须是生产案例真实使用的版本，不得用“最新版本”代替。
- `occurred_at` 是最终人工纠偏事件发生时间，不是发送或重试时间。
- `reason_codes` 与 `production_applied` 当前可选，但建议生产端提供。

## 响应与错误码

| 状态码 | 含义 | 发送端处理 |
|---|---|---|
| `200` | 首次接受或完全相同的幂等重放 | 记录 `event_id` 和 `duplicate`，停止重试 |
| `401` | 缺少或错误的专用 token | 停止重试，检查秘密挂载与轮换状态 |
| `409` | 同一幂等键对应不同载荷 | 停止重试，人工核对原事件与本次事件 |
| `422` | schema 或业务字段非法 | 停止重试，修正发送端映射 |
| `503` | LabelLab 未配置接收 token | 停止扩大重试，联系管理员完成显式配置 |
| `5xx` | 接收端临时故障 | 保持原 JSON 和 `event_id`，按退避策略重放 |

## 发送示例

仓库提供 `scripts/integration/production_feedback_sender_example.py`。默认模式只
读取并校验 JSON，然后打印脱敏摘要，不访问网络：

```text
python scripts/integration/production_feedback_sender_example.py event.json
```

只有显式 `--send` 且以下环境变量均存在时才发送：

```text
LABELLAB_FEEDBACK_URL=<完整接收端 URL>
LABELLAB_FEEDBACK_TOKEN=<专用机器 token>
```

```text
python scripts/integration/production_feedback_sender_example.py event.json --send
```

示例脚本仅使用 Python 标准库，不包含默认厂商 URL、真实 token 或凭据样例。

## 运维注意事项

- 上线前先在隔离实例使用假事件验证 `401/409/422/503` 和相同载荷重放。
- 发送端应保存 Outbox 事件原文与投递状态，重试必须重用相同字节语义。
- 对 `401`、`409`、`422`、`503` 设置告警，禁止无上限快速重试。
- Token 轮换应先在接收端维护窗口更新，再更新发送端秘密；轮换窗口内暂停
  Outbox 投递，避免把认证失败误判为数据失败。
- 接收端事件与审计表只追加；禁止手工 UPDATE/DELETE 修复，冲突事件应通过
  新的业务事件和新 `event_id` 纠正。
- LabelLab 自动优化默认关闭且 dry-run，接收到 P0/P1 事件不等于已经运行模型。

## 脱敏边界

允许记录：`event_id`、schema、事件类型、来源系统、发生时间、payload 字段名、
规范化载荷 SHA-256、HTTP 状态码和 LabelLab 返回的安全错误类别。

禁止记录：Bearer token、Authorization 请求头、Cookie、管理员口令、模型 API
Key、系统凭据引用、完整 `model_output`、完整 `human_truth`、图片内容、原始上游
异常、带凭据的 URL 或请求转储。
