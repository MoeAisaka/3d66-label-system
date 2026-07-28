# ADR-0013：MacBook 首次安装、诊断、启动与脱敏灾备生命周期

- 状态：Accepted
- 日期：2026-07-28

## 背景

ADR-0012 已完成 macOS Keychain 凭据安全层，但“代码能在 macOS 保护凭据”
不等于目标 MacBook 已具备可控部署与灾备能力。首个体验部署点预计依赖公司
MacBook 和公司内网，因此必须在真实部署前先形成可离线测试、失败关闭的安装、
诊断、前台启动、备份和恢复合同。

SQLite、图片与运行日志是本地数据；Keychain/DPAPI 是机器和用户绑定的凭据
系统。备份若保留会话或凭据引用，恢复到另一台电脑后可能产生错误授权语义；
恢复若只覆盖文件而没有版本、完整性、运行状态和回滚门禁，也可能把可用本地
数据变成不可启动状态。

## 决策

### 数据目录

- macOS 默认 `DATA_DIR` 为
  `~/Library/Application Support/3d66-label-system`。
- Windows 继续优先使用
  `%LOCALAPPDATA%\3d66-label-system`，行为不变。
- 显式 `DATA_DIR` 始终优先，但 macOS 部署入口拒绝把数据目录放入代码仓库。
- 数据库、图片和日志都不得写入代码仓库。

### 安装、诊断与启动

- `scripts/macos/install.sh`、`doctor.sh`、`start.sh`、`backup.sh` 和
  `restore.sh` 是唯一受控 macOS 生命周期入口；全部使用
  `set -euo pipefail`，从脚本位置解析仓库，不依赖当前工作目录。
- 安装门禁固定为 Python 3.11/3.12、Node.js 20～26、npm 10/11。
- `install.sh` 只允许创建仓库内 `.venv`、按已有 requirements 安装 Python
  依赖、执行前端 `npm ci` 和生产构建。`--check`/`--dry-run` 不安装、
  不构建、不访问网络。
- 脚本不得调用远程安装脚本、`sudo`、`brew install`，不得修改 shell rc、
  launchd、系统配置或防火墙。
- `start.sh` 必须先通过 doctor，再复用现有 launcher，保持前台运行和
  `Ctrl-C` 清理。默认 `APP_HOST=127.0.0.1`；只有调用时用户显式环境覆盖
  才改变监听地址。不创建 daemon 或 launchd 服务。

### 正式备份

- 使用 Python 标准库 `sqlite3.Connection.backup` 创建一致 SQLite 副本；
  图片只复制普通文件，拒绝符号链接和复制期间变化的文件。
- 默认备份根目录为
  `~/Documents/3d66-label-system-backups`，并拒绝与数据目录重叠或位于代码
  仓库。
- manifest schema 为 `3d66-label-system-macos-backup` v1，保存 UTC 时间、
  数据库迁移版本、可得时的 Git commit，以及每个相对文件路径、大小和
  SHA-256。
- 备份不复制 `logs/`、`.env` 或其他环境文件。数据库副本必须清空
  `session_tokens`、`model_configs.encrypted_api_key` 和
  `optimizer_configs.encrypted_api_key`，再执行 `VACUUM` 后计算哈希。
- 因此正式备份不含 API Key、Keychain/DPAPI 内容或引用，也不恢复登录
  会话。恢复后必须在目标机重新填写凭据。
- 备份目录权限固定 `700`，普通文件固定 `600`。

### 恢复

- `restore.sh` 实际恢复前自动先执行同一备份的 `--dry-run`。校验顺序为：
  manifest schema/exclusions → 相对路径无穿越与文件清单一致 →
  SHA-256/大小 → SQLite `integrity_check` → 最高 `schema_migrations` 不高于
  当前代码支持版本 → 会话与凭据字段确已清空。
- `--dry-run` 不创建或修改目标数据，不依赖服务状态。
- 实际恢复时，配置端口被占用即按服务可能仍运行处理并拒绝恢复，无绕过参数。
- 替换前在目标数据目录同一父目录创建临时、权限收紧的 rollback snapshot，
  保留当前 database/images 的精确状态；成功或成功回滚后立即删除。
- 备份 database/images 先复制到同文件系统 staging，再用 `os.replace` 原子
  替换各自入口。任一步失败都自动用 rollback snapshot 补偿；若补偿本身
  失败，必须保留快照路径并返回稳定错误，不声称成功。
- Keychain、DPAPI 和 API Key 不参与恢复事务，禁止跨平台复制。

## 后果

- MacBook 首次体验部署有了可审查、可测试的本地生命周期，且不会因为备份
  迁移而复制登录态或凭据。
- 数据库与 images 是两个文件系统入口，无法形成单个跨目录原子操作；通过
  同文件系统 staging、逐入口 `os.replace` 和完整 rollback snapshot 提供
  可验证补偿。
- 实际备份恢复后，用户必须重新登录并在目标机重新填写主模型和优化模型
  API Key。这是安全边界，不是缺陷。
- 端口占用门禁可能把非本服务的进程也视为风险并拒绝恢复；这是有意的
  fail-closed 取舍。

## 验证

- `backend/tests/test_macos_deploy.py` 使用 `tmp_path` 和明显假数据覆盖：
  macOS/Windows 默认目录、显式 `DATA_DIR`、doctor 平台与版本门禁、备份
  脱敏与排除、manifest/hash、路径穿越、篡改、未来迁移、dry-run、成功
  恢复、服务运行拒绝、失败自动回滚和权限。
- 2026-07-28：专项 `20 passed`；全后端
  `348 passed, 1 skipped, 1 warning`；五个脚本 `bash -n`、Python
  `compileall`、`install.sh --check/--dry-run` 和 `git diff --check` 通过。
- 验证未部署、未联网、未读取真实 Keychain 凭据、未调用真实模型，所有
  数据测试均在临时目录执行。

## 未完成

- 目标 MacBook 的真实安装、启动、登录、页面保存 Keychain 凭据、备份恢复
  演练和公司内网可达性验收仍未执行。
- 真实 API Key、真实模型连接、真实评测、提示词优化和 XLSX 执行器未验证。
- Windows 研发机部署与真实 DPAPI 回归仍未执行。

## 不可破坏约束

- 不得把正式备份改成数据库文件直接复制，或取消 session/credential 清理与
  `VACUUM` 后哈希。
- 不得从 manifest 接受绝对路径、`..`、符号链接、未声明文件、哈希不一致或
  未来迁移版本。
- 不得在服务可能仍运行时恢复，不得取消恢复前 rollback snapshot 和失败
  补偿。
- 不得把 Keychain、DPAPI、API Key、`.env`、日志或登录会话纳入正式备份。
- 不得把 macOS 启动默认监听地址改为对外网卡，或静默安装 daemon/launchd。
