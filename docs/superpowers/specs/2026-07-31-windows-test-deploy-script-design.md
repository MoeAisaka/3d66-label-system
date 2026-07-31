# Windows 测试环境一键部署脚本设计

## 目标

让 Windows 同事从本机部署 Codeup `main` 最新提交到测试服务器，无需云效
Flow 或 Jenkins。脚本只更新测试容器 `3d66-label-system-test`，不修改持久化
数据目录或旧的 `9093` 容器。

## 固定环境

- 源仓库：`https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git`
- 源分支：`main`
- 服务器：`192.168.1.35`
- SSH 用户：`yuankangzhi`
- 服务器项目目录：`/opt/3d66-label-system`
- 持久化数据目录：`/opt/3d66-label-system-data`
- Compose 服务：`app`
- 测试容器：`3d66-label-system-test`
- 测试地址：`http://192.168.1.35:8081`
- 健康接口：`http://127.0.0.1:8081/api/health`

## 交付文件

### `部署测试环境.cmd`

提供可双击的 Windows 入口，定位仓库根目录并调用 PowerShell 脚本。保留窗口，
让同事能看到成功信息或错误原因。

### `scripts/deploy-test.ps1`

负责本机发布编排：

1. 确认当前目录是目标 Git 仓库。
2. 确认 `git`、`ssh` 和 `scp` 可用。
3. 确认 `origin` 指向指定 Codeup 仓库。
4. 从 Codeup 获取 `origin/main`，不使用当前工作区文件。
5. 读取精确提交号，创建只包含 `main` 可达对象的 Git bundle。
6. 上传 bundle 和远端辅助脚本到服务器临时目录。
7. 通过 SSH 调用远端辅助脚本并传入精确提交号。
8. 删除本机临时发布文件。
9. 输出已发布提交号、容器状态和测试地址。

脚本不得读取、保存或输出 SSH 密码。身份验证由系统 `ssh` / `scp` 交互完成；
服务器执行 Docker 时由 `sudo` 自行提示密码。

### `scripts/deploy-test-server.sh`

负责服务器端原子化发布：

1. 校验目标提交号格式和 bundle 文件存在。
2. 进入 `/opt/3d66-label-system`，记录部署前提交号。
3. 检查工作区；除已纳入仓库的部署文件外，存在未提交修改时拒绝发布。
4. 从 bundle 获取目标 `main`，验证提交对象存在。
5. 将工作区切换到指定提交。
6. 执行 `sudo docker compose up -d --build`。
7. 等待容器健康，并请求 `/api/health` 验证响应。
8. 成功后删除服务器临时 bundle，输出部署结果。

## 失败与回滚

- 获取、上传、校验在容器重建前失败：保持当前服务不变。
- 更新代码后构建或启动失败：恢复部署前提交，重新执行
  `sudo docker compose up -d --build`。
- 新容器未在规定时间内健康：按同样方式恢复部署前提交和容器。
- 回滚也失败：保留完整错误输出并返回非零状态，不删除诊断所需信息。
- 回滚不接触 `/opt/3d66-label-system-data`，因此数据库、图片和日志不随代码回滚。

## 安全边界

- 只允许发布 Codeup `main`，不接受任意分支参数。
- 不把密码、令牌、私钥或 `.env` 写入仓库、参数或日志。
- 不上传本机未提交文件，发布内容只来自 `origin/main`。
- 不执行 `git clean`，不删除服务器数据或其他容器。
- 不修改 `3d66-label-system` 旧容器及其 `9093` 端口。
- 使用唯一临时文件名，结束时清理本次产生的临时文件。

## 使用体验

同事先正常克隆 Codeup 仓库，之后双击 `部署测试环境.cmd`。脚本显示待发布的
短提交号并要求明确输入 `DEPLOY` 后才进入上传和部署阶段。发布过程中可能分别
出现 `scp`、`ssh` 和 `sudo` 的密码提示；脚本自身不缓存密码。

## 验证标准

- PowerShell 脚本可通过语法解析。
- Shell 脚本可通过 `bash -n`。
- 错误的仓库远端、缺少命令、非法提交号和服务器脏工作区会在构建前失败。
- dry-run 模式能完成本机检查、获取 `origin/main` 和 bundle 创建，但不连接服务器。
- 正式演练后服务器 HEAD 等于 Codeup `main`。
- `docker inspect` 返回容器 `running` 且 `healthy`。
- `/api/health` 返回 `status=ok`。
- `/opt/3d66-label-system-data` 仍挂载到容器 `/data`。

## 后续扩展

底层脚本稳定后，可以新增 Codex Skill 调用同一脚本完成发布前检查和结果汇报。
Skill 不复制部署逻辑，也不作为同事日常发布的必要依赖。
