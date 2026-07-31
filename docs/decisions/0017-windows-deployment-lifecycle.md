# ADR-0017：Windows 公司服务器受控部署生命周期

- 状态：Accepted
- 日期：2026-07-30

## 背景与范围

ADR-0013 已为 macOS 建立首次安装、只读诊断、前台启动、脱敏备份和补偿式
恢复合同，但 Windows 正式服务器仍依赖根目录旧 CMD 中各自维护的安装或启动
逻辑。旧入口缺少统一版本门禁、部署前 doctor、正式备份恢复和 Windows
junction/reparse point 防护，不能作为公司服务器的受控生命周期。

本决策只覆盖当前仓库内的 Windows 生命周期工具和 DPAPI 平台边界。不安装
系统软件，不注册 Windows 服务，不修改注册表、防火墙、计划任务、执行策略或
其他系统配置，也不触碰任何真实生产目录和生产数据。

## 决策

### 五脚本生命周期与兼容入口

`scripts/windows/` 是 Windows 的唯一受控实现，包含：

- `install.ps1`：版本门禁、仓库内 `.venv`、既有 requirements、`npm ci`
  和正式前端构建；`-Check`/`-DryRun` 不安装、不构建、不联网；永不写
  `DATA_DIR` 或启动服务。
- `doctor.ps1`：检查平台、依赖版本、仓库必要文件、`.venv`、前端构建、
  `DATA_DIR`、路径边界、SQLite 完整性/迁移版本及凭据引用格式；并按
  ADR-0023 使用随机内存哨兵执行一次 DPAPI 加密—解密回环，不读取或改写
  业务凭据。
- `start.ps1`：必须先通过 doctor，随后用仓库内 Python 和已构建前端以前台
  方式运行现有 launcher；不注册服务或后台任务。
- `backup.ps1`：调用仓库内标准库备份核心创建一致、脱敏、带清单和校验的
  备份。
- `restore.ps1`：实际恢复前自动执行同一备份的 `-DryRun` 校验；不自行启动
  服务。

所有脚本使用严格模式、`$ErrorActionPreference = 'Stop'`、明确检查原生子进程
退出码，并从 `$PSScriptRoot` 解析仓库，不依赖当前工作目录。根目录
`start-3d66.cmd`、`启动3d66标签系统.cmd` 和 `首次安装.cmd` 仅负责切换
UTF-8 code page、转发全部参数并返回 PowerShell 退出码，不再维护第二套逻辑。

### 运行时门禁

- Python 只允许 3.11 或 3.12；虚拟环境只能是仓库内
  `.venv\Scripts\python.exe`。
- Node.js 只允许 20.x～26.x。
- npm 只允许 10.x 或 11.x。
- 安装脚本不下载或安装 Python、Node、npm 或任何系统软件，不执行远程安装
  脚本，不请求管理员权限。

### DATA_DIR 优先级与路径边界

Windows 受控入口按以下顺序解析数据目录：

1. 当前命令显式 `-DataDir` / 后端 `--data-dir`；
2. 当前进程环境变量 `DATA_DIR`；
3. 仓库根目录普通 UTF-8 `.env` 中的 `DATA_DIR`；
4. `%LOCALAPPDATA%\3d66-label-system`。

前三种显式配置必须是绝对路径。缺少 `LOCALAPPDATA` 时 fail-closed，不回退到
仓库内 `data/`。路径经过绝对化、规范化和 Windows 大小写不敏感比较；
`DATA_DIR` 与备份目录均不得位于代码仓库内，备份目录与数据目录不得互相
包含。doctor 对不存在的数据目录只检查现有父目录可用性，不创建目录或业务
数据。

Windows 默认备份根为
`%USERPROFILE%\Documents\3d66-label-system-backups`。若 Documents 被重定向
为 junction/reparse point，默认路径会被拒绝，操作员必须显式指定经批准的
普通本地目录。

### UTF-8 与 CJK 路径

PowerShell 脚本文件保存为 UTF-8 BOM，兼容仍按 BOM 识别 UTF-8 的 Windows
PowerShell 5.1；入口设置 Console 输入/输出和 `$OutputEncoding` 为无 BOM
UTF-8，并设置进程级 `PYTHONUTF8=1`。所有 Python 调用都带 `-X utf8`，CMD
兼容壳先执行 `chcp 65001`。路径始终以参数数组和 `-LiteralPath` 传递，不
拼接命令行字符串，支持空格和中文仓库/数据/备份路径。

### SQLite 备份、恢复和重解析点防护

Windows 备份 manifest 使用独立 schema
`3d66-label-system-windows-backup` v1，目录名与 macOS 一致采用
`3d66-backup-v1-<UTC>`。清单保存 UTC 时间、数据库迁移版本、Windows 来源、
可得时的 Git commit，以及 database/images 每个相对文件的大小和 SHA-256。

数据库通过 Python `sqlite3.Connection.backup` 生成一致副本，不能直接复制
活跃 `app.db`。副本清空 `session_tokens`、`model_configs.encrypted_api_key`
和 `optimizer_configs.encrypted_api_key`，执行 `VACUUM` 后再生成哈希。只复制
images 普通文件；logs、`.env`、API Key、DPAPI/Keychain 内容或引用和登录
会话全部排除。备份失败时删除未完成的随机临时目录，不发布半成品目标。

校验拒绝绝对路径、`..`、反斜线、NTFS alternate data stream、Windows 设备
名、大小写重复路径、未声明文件、额外根入口、哈希/大小不符、未来迁移、
SQLite 损坏或未完成脱敏。源、备份、目标、仓库、数据库、图片树及所有现有
父级只要含 symlink、junction 或任意 reparse point 即 fail-closed。

实际 restore 要求配置端口未占用，在目标同一父目录建立 staging 和 rollback
snapshot。当前数据库用 SQLite backup API 保存逻辑一致快照，当前 images
复制普通文件；新 database/images 分入口使用 `os.replace`，并清理旧 SQLite
WAL/SHM/journal sidecar。任一步失败都用 snapshot 补偿；补偿成功即删除
snapshot，补偿失败则保留其精确路径并明确返回失败。Windows 和 macOS 都无法
对 database 与 images 两个入口提供单一文件系统事务，因此采用相同的可验证
补偿语义。

Windows 不采用 POSIX `700/600` 模式检查，也不由脚本重写 NTFS ACL。默认
备份位于当前用户 profile，显式目录沿用公司预先配置的 ACL；脚本只做路径、
重解析点、内容、清单和完整性门禁。这是 Windows 权限模型差异，不授权脚本
修改系统级 ACL。

### 启动监听和 Ctrl+C

`start.ps1` 在当前进程未显式提供 `APP_HOST` 时强制设置
`127.0.0.1`，因此仓库 `.env` 不能把默认启动静默改为对外监听。只有操作员在
本次调用前显式设置进程环境变量才能改变绑定地址。launcher 保持前台运行，
PowerShell 不创建子后台任务，Ctrl+C 和 launcher 退出码沿原生前台子进程
路径返回。

### DPAPI 平台边界

DPAPI 范围和启动门禁以 ADR-0023 为准。核心安全层保持当前用户范围默认；
公司 Windows Server 的 `doctor.ps1` 和 `start.ps1` 默认显式设置
`API_KEY_DPAPI_SCOPE=local-machine`，操作员可用 `-DpapiScope CurrentUser`
切回当前用户范围。两种范围分别使用 `dpapi:v1:` 和
`dpapi-machine:v1:` 引用，不得在失败后静默互相降级。

业务入口、DPAPI 工厂和底层构造器都必须先确认 `sys.platform == "win32"`；
非 Windows 路径在加载 `crypt32`/`kernel32` 前拒绝。doctor 必须在与正式服务
相同的进程身份和范围下使用随机内存哨兵执行真实 DPAPI 加密—解密回环；回环
失败即阻止服务启动。既有无前缀 DPAPI 密文继续只在 Windows 兼容读取。

机器范围允许同机且能够读取密文的其他主体调用 DPAPI 解密，因此只适用于受控
专用服务器，并依赖公司既有 NTFS ACL 保护数据目录。脚本不修改 ACL，也不把
密文写入日志、备份、响应或 Git。

备份会删除 DPAPI 密文和 Keychain 引用，restore 不复制或转换凭据。换用户、
换机器或跨平台恢复后必须重新填写 API Key；不实现凭据迁移。

## 与 macOS 生命周期的关系

同级项包括：五脚本入口、相同版本门禁、仓库内 venv、锁文件安装、生产构建、
doctor 前置、默认 loopback、前台运行、SQLite backup API、脱敏 manifest/hash、
恢复 dry-run、服务停止门禁、同卷 staging、原子分入口替换和失败回滚。

Windows 特有差异包括：PowerShell/CMD UTF-8 处理、`.venv\Scripts`、
`LOCALAPPDATA`/`USERPROFILE` 默认路径、大小写不敏感比较、NTFS 特殊文件名与
ADS 拒绝、所有 reparse point 拒绝、独立 Windows manifest schema，以及不把
POSIX mode 语义错误映射为 NTFS ACL。

## 后果

- Windows 公司服务器拥有可审查、可离线测试、失败关闭的生命周期入口；旧
  CMD 不再绕过 doctor 或版本门禁。
- 默认启动不会对局域网暴露。确需局域网访问时必须作为独立安全决策显式设置
  当前进程 `APP_HOST`，本 ADR 不修改防火墙。
- 正式备份不保留登录态或凭据，恢复后需要重新登录并由同机当前用户填写
  API Key。
- 更严格的 reparse point 策略会拒绝 OneDrive、重定向 Documents、junction
  数据盘等路径；这是防路径逃逸的有意 fail-closed 取舍。

## 验证状态与 Windows 实机回归清单

**本分支未在真实 Windows 或 Windows Server 上执行过。** 当前只在 macOS
以临时目录、明显假数据和平台注入完成 Python 单元测试与静态审查；因此不能
把本 ADR 视为公司服务器部署验收已通过。

正式服务器至少逐项执行并留存以下回归记录：

1. 普通公司账号、无管理员权限的全新安装；确认未出现提权、注册表、服务、
   防火墙或计划任务修改。
2. Python 3.11 和 3.12 分别通过；3.10/3.13 明确失败。
3. Node 20 和一个更高受支持版本分别通过；Node 19/27 失败；npm 10/11 通过，
   npm 9/12 失败。
4. 仓库、`DATA_DIR` 和备份路径分别覆盖中文、空格及二者组合。
5. 验证显式参数、进程 `DATA_DIR`、仓库 `.env`、`LOCALAPPDATA` 四级优先级，
   以及大小写/规范化等价的仓库内路径拒绝。
6. 未设置进程 `APP_HOST` 时只监听 `127.0.0.1`；显式对外绑定另行完成公司
   网络安全审批。
7. 前台 Ctrl+C 能清理 worker；doctor、launcher 和脚本原生失败码逐层保持
   非零。
8. 服务活跃写入 SQLite/WAL 时创建备份，验证副本包含已提交事务、
   `integrity_check` 通过且源库未被改写。
9. restore 成功、目标不存在、数据库/images 各阶段注入故障、自动回滚成功，
   以及回滚自身失败时保留 snapshot 的行为。
10. 对 DATA_DIR、备份根、数据库、图片子目录和目标分别建立 symlink、
    junction 和其他 reparse point，全部必须拒绝且不发生范围外读写。
11. 同一 Windows 当前用户 DPAPI 新写入、读取和旧无前缀兼容；不同用户、不同
    机器不可解密；并验证服务器默认机器范围的新写入、读取和
    `dpapi-machine:v1:` 引用。两种范围均须先通过 doctor 内存回环，备份/恢复
    后凭据为空且必须重填。
12. Windows PowerShell 5.1 与公司批准的更高 PowerShell 版本分别做五脚本
    parser 检查和完整生命周期演练。

## 待决项

- 公司服务器最终采用 Windows PowerShell 5.1 还是经批准的 PowerShell 7，
  以及脚本签名、AllSigned/RemoteSigned 策略，由基础设施负责人确定；当前
  CMD 不使用 `ExecutionPolicy Bypass` 绕过公司策略。
- 正式备份盘路径、容量、NTFS ACL 基线、离机复制和保留周期尚未确定；本任务
  不修改 ACL，也不实现自动清理或远端同步。
- 默认 `127.0.0.1` 之外的局域网开放方式、反向代理、TLS、身份边界与防火墙
  规则尚未批准；本任务不自行设置 `0.0.0.0` 或系统防火墙。
- 是否接受位于受管 reparse point 后的公司存储没有产品决策；当前统一拒绝，
  后续若放宽必须新增 ADR 和路径逃逸实机测试。

## 不可破坏约束

- 不得恢复旧 CMD 独立安装/启动逻辑，不得跳过 doctor 启动。
- 不得把默认监听改为非 loopback，不得静默注册服务或常驻任务。
- 不得把 SQLite 正式备份改为直接复制活跃数据库，不得取消脱敏、manifest、
  hash、迁移、完整性、reparse point 或回滚门禁。
- 不得把 DPAPI/Keychain/API Key/登录会话纳入备份或实现跨用户、跨机器、跨
  平台凭据迁移。
- 不得取消 Windows 启动前真实 DPAPI 回环，不得在回环或凭据写入失败时回退
  到明文、普通文件或另一个 DPAPI 范围。
- 不得改变 ADR-0016 的 `enabled=false / dry_run=true /
  daily_budget_micros=0` 默认值，也不得借部署脚本启动真实自动化或模型调用。
