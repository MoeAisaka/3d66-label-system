# ADR-0027：多人账号、统一模型管理与 Docker 持久化

日期：2026-08-01

## 决策

1. 使用单一账号体系和服务端 RBAC：`admin`、`manager`、`reviewer`、`analyst`、`viewer`。权限由 API 强制校验，停用或密码重置立即删除该用户的会话；系统始终保留至少一个启用管理员。
2. `ModelConfig` 作为统一模型注册表，记录渠道、协议、能力、计价和安全凭据引用；`ModelNodeBinding` 将模型分配给主评测、PDF 总结、优化、横评和诊断节点。任务入队时冻结非密模型快照，运行中不随管理配置漂移。
3. 协议适配预设为 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages，以及受控 OpenAI-compatible JSON。认证头由服务端按协议生成，禁止用户注入任意认证头。
4. Docker 使用命名卷 `/data` 保存 SQLite 数据库、上传素材和 `/data/secrets/master.key`。Linux 容器用 AES-GCM 文件密钥保护 API Key；数据库只存密文引用。主密钥必须与数据库一起纳入备份，不能写入镜像、Git 或数据库。

## 边界

当前迁移和查询仍以 SQLite 为事实实现；`DATABASE_URL` 支持容器内 SQLite 持久化，不宣称 PostgreSQL 迁移兼容。真实 Docker 引擎验收需在安装 Docker 的环境执行。

