# 纠偏分析并发恢复部署回执

- Codeup `main`: `7575e6415ce89ece4a66a155672146a44de6b8ff`
- 测试服务器: `192.168.1.35:8081`
- 容器: `3d66-label-system-test`，`running/healthy`，restart count `0`
- `/api/health`: HTTP 200；`/api/health/ready`: ready，8 个 worker 活跃
- 数据库: integrity `ok`，FK `[]`，migration `65`，active jobs/runs/corrections 均为 `0`
- 部署前 SQLite 快照: `/data/database/predeploy-snapshots/app-predeploy-7575e641-20260813T110224Z.db`
- 快照 SHA-256: `bbd91409ecd997ccb4eb72224d37a0f75cced3a8ae9808d740c7d9c8339e8395`
- 前端构建: Docker 内 production build 通过；仅保留既有主 chunk 大于 500 kB warning
- Edge 验收: 未启动真实纠偏、未重试真实失败任务、未提交人工启用/拒绝；现有 Edge 页面由其他会话占用，使用接口与静态资源只读证据核验部署结果。
