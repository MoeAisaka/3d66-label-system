# 合同自有红线值与调用 A 前置 L5 过滤部署回执

- Codeup MR: `#34`
- 修复提交: `70a7dd5`
- 部署时 Codeup `main`: `242cd98c2f05d43f535d70d730be96f0b6f5e586`
- 测试服务器: `192.168.1.35:8081`
- 部署前服务器提交: `820771d4f5ef52cf6e6f186ba3888b33a1e97af9`
- 容器: `3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`: `ok`；`/api/health/ready`: `ready`；8 个 worker 活跃
- 自动化门禁: `policy_disabled` 且 `dry_run`，未触发真实模型调用
- 数据库: integrity `ok`，FK `[]`，migration `76`
- NAS: `/mnt/label-nas/maps` 验证通过并保持只读挂载
- 前端产物: production build 通过，静态 bundle 包含构建标识 `242cd98`；仅保留既有主 chunk `529.89 kB` warning
- 部署前 SQLite 快照: `/data/database/predeploy-snapshots/app-predeploy-820771d4-before-242cd98c-20260820T102909Z.db`
- 快照大小: `594124800` bytes
- 快照 SHA-256: `6108ec6b39737d6442c0fa048987c83745f7bf19752c93ba4d2f9c26c6ed02dc`
- 部署 bundle SHA-256: `b9017253494b0d684e53755e5ce4ad317ee76f2e5aba571c566fdbccd99780f2`

本次部署仅发布合同驱动的 reason/redline 值校验与调用 A 后、调用 B 前的证据红线过滤能力；未部署 MacBook `8080`，未连接公司 Windows，未写业务生产数据，未调用真实模型，也未重跑历史回归。
