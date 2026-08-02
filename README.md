# 3d66 标签系统

面向 3D66 空间与建筑图片的本地评测 Demo。系统把图片分类、形态识别、画质预检、美感维度、外部评分、人工审核、提示词版本和模型迁移放进同一条可追溯流程。

## 当前能力

面向一线审核员与二审管理员的完整操作说明见 [操作手册](docs/user-guide.md)。

- 生产目标仍是 Windows 主机运行；当前已增加与 macOS 同级的 Windows
  安装、诊断、前台启动、脱敏备份和恢复工具；macOS 部署链已用于 MacBook
  功能验收，但尚未在真实 Windows 公司服务器执行，默认也不对局域网开放。
- 批量图片、整个文件夹或 ZIP 上传会自动汇总为不可变素材包；也可从现有
  素材手工整理新包。相同内容按 SHA-256 复用素材，但保留每次导入来源。
- 素材删除采用可恢复的逻辑删除：默认列表和新任务不再显示，历史评测、真值、
  素材包来源与本地文件继续保留；再次上传相同内容时恢复。
- “素材选择”已并入“素材包”页面，可按包筛选后创建任务。
- 豆包与提示词优化模型配置全部在后台管理。Windows 使用当前用户的
  DPAPI；macOS 使用当前登录用户的 Keychain。数据库只保存版本化密文或
  Keychain 引用，前端不会再次读回完整密钥。
- 使用用户提供的 Doubao-Seed-2.0-Lite V2.1 提示词，按 A（分类/形态/画质）和 B（美感维度）两次调用。
- 兼容只有一版完整提示词的单次调用模式；任务和结果明确记录为“单提示词”，不会伪装成 A/B 两个版本。
- 总分和 L1～L5 由服务端固定评分引擎计算，模型不直接决定最终总分。
- 随拍图或画质受损（`slight` 及以上）时最终等级最高为 L2；严重或不可用画质满足证据阈值时最高为 L1。
- 人工纠正只修改错误维度，最终人工分数和等级由同一服务端评分引擎自动重算，不能手工指定。
- 保存模型原始响应、解析结果、模型 ID、A/B 提示词版本、规则版本和评分引擎版本。
- 提示词修改采用“AI 提议草案 → 人工编辑 → 另存新版本 → 评测 → 人工发布”，不会覆盖已发布版本。
- 模型迁移使用旧模型历史结果做基线；新模型只重跑分层样本，人工只处理差异、低置信度和约 5% 的一致样本抽检。
- 智能抽样策略可配置常规抽样比例、置信度阈值、冷启动必审数和高等级必审范围；每次保存生成可追溯的策略修订号。
- 独立样本集可长期保存人工确认图片、基准等级和判断备注；迁移时可选择固定样本集，确保不同模型版本评测同一批图片。
- 基准回归可按素材包整包声明 L1～L5 真值，也可逐张设置；报告提供精确命中率、
  相邻命中率、混淆矩阵、逐张偏差和冻结评测理由，偏差样本可加入提示词找补
  队列。图片名中的 `L1`～`L5` 或“好/中等/中差/极差/过滤”只用于预填等级，
  创建前可逐张修改；当前素材主流程不从 Excel 或图片 URL 获取原图。
- 优化与回归按“案例池 → 组批与安全试跑 → 候选与配对回归 → 人工发布”连续
  展示；安全试跑不调用模型、不计费，也不会自动发布提示词。
- 明亮白底审图界面，无暗色模式；品牌色 `#CCED46`。

## 在当前电脑启动

Windows 依赖已安装且前端已构建时，双击：

`启动3d66标签系统.cmd`

两个 CMD 都只是 `scripts/windows/start.ps1` 的兼容壳；正式启动一定先执行
doctor。若中文文件名入口在某台电脑上被安全软件拦截，也可以双击纯英文备用
入口 `start-3d66.cmd`。

看到“3d66 标签系统已启动”但浏览器没有自动出现时，手动打开：

`http://127.0.0.1:8080`

管理员账号为 `sol`，密码使用项目最初约定的 Demo 密码。请勿把密码或 API Key 写进 Git。

## 到公司电脑重新配置

1. 从 Git 克隆项目到非 OneDrive、非 junction/reparse point 目录，建议
   `D:\3d66-label-system`。
2. 由公司软件分发准备 Python 3.11/3.12、Node.js 20～26 和 npm 10/11；
   仓库脚本不会安装系统软件。
3. 以普通用户运行 `scripts\windows\install.ps1`；`首次安装.cmd` 只是同一
   脚本的兼容壳。
4. 运行 `scripts\windows\doctor.ps1`，通过后再运行
   `scripts\windows\start.ps1` 或双击启动 CMD。
5. 登录后进入“模型配置”，填写公司电脑上的豆包 API Key，保存并测试连接。
6. 重新上传图片、创建评测任务。Demo 阶段不需要迁移家里电脑的数据。

Git 只保存代码、提示词和配置结构；`.venv`、`node_modules`、构建产物、数据库、图片、日志和 `.env` 都已排除。

## Windows 受控部署生命周期

Windows 唯一受控实现位于 `scripts/windows/`，要求普通用户权限，不修改
注册表、Windows 服务、防火墙、计划任务或 PowerShell 执行策略：

```powershell
.\scripts\windows\install.ps1 -Check
.\scripts\windows\install.ps1 -DryRun
.\scripts\windows\install.ps1
.\scripts\windows\doctor.ps1
.\scripts\windows\start.ps1
```

安装门禁固定为 Python 3.11/3.12、Node.js 20.x～26.x 和 npm 10.x/11.x。
实际安装只创建仓库内 `.venv`，按既有 requirements 安装依赖，再执行
`npm ci` 和正式前端构建；不创建或修改业务数据，也不启动服务。

`DATA_DIR` 优先级是显式 `-DataDir`、进程环境变量、仓库 `.env`、最后
`%LOCALAPPDATA%\3d66-label-system`。前三者必须是绝对路径，且任何解析结果
都不能位于代码仓库内。doctor 只读检查现有父目录、SQLite 完整性/迁移版本和
Windows 凭据引用，不创建数据目录、不调用 DPAPI 解密。

`start.ps1` 默认强制 `127.0.0.1`。只有本次调用前显式设置进程环境变量才会
改变监听地址，例如：

```powershell
$env:APP_HOST = '0.0.0.0'
.\scripts\windows\start.ps1
```

对外监听前必须单独完成公司网络、TLS、身份和防火墙审批；脚本不会替操作员
修改系统配置。

创建脱敏备份和只读验证/实际恢复：

```powershell
.\scripts\windows\backup.ps1
.\scripts\windows\backup.ps1 -BackupDir 'E:\3d66 backups'
.\scripts\windows\restore.ps1 -Backup 'E:\3d66 backups\3d66-backup-v1-YYYYMMDDTHHMMSSZ' -DryRun
.\scripts\windows\restore.ps1 -Backup 'E:\3d66 backups\3d66-backup-v1-YYYYMMDDTHHMMSSZ'
```

Windows 正式备份使用 SQLite backup API，不直接复制活跃数据库；会清空登录
会话和主/优化模型凭据字段，排除 logs、`.env` 和 DPAPI/Keychain 内容，并用
Windows v1 manifest 保存迁移版本、文件大小和 SHA-256。恢复拒绝路径穿越、
NTFS 特殊路径、symlink/junction/reparse point、篡改、未来迁移和仍在使用的
服务端口；实际替换前创建同卷 rollback snapshot，失败时自动补偿。

以上能力只在 macOS 上以临时假数据做过自动测试和静态审查，尚未完成真实
Windows/Windows Server、PowerShell parser、Ctrl+C、junction 和 DPAPI
当前用户范围实机验收。完整清单见 ADR-0017。

## macOS 凭据安全层状态

macOS Keychain 工程接线已完成：

- 通过 `ctypes` 直接调用 Security.framework 的通用密码 API，不经过
  `security` CLI、shell、命令行参数或临时文件；
- 主模型与提示词优化模型使用不同的稳定 account；同一 account 再次保存时
  原位覆盖；
- SQLite 的 `encrypted_api_key` 只保存
  `keychain:v1:model-config` 或 `keychain:v1:optimizer-config`，真实密钥只
  存在当前登录用户的 Keychain；
- Windows 新写入使用 `dpapi:v1:` 前缀，并继续兼容既有未加前缀的 DPAPI
  密文；Keychain 与 DPAPI 引用不能跨平台读取。

这只代表安全层及隔离 Keychain 测试已经完成，不代表 MacBook 安装部署或
真实模型联调已经完成。换系统或换用户后应在目标电脑重新填写 API Key。

## macOS 首次安装与启动

macOS 受控入口位于 `scripts/macos/`。所有脚本都可从任意工作目录运行，
路径支持空格；不会使用 `sudo`、Homebrew、远程安装脚本、shell rc、
launchd、系统配置或防火墙修改。

版本门禁：

- Python 3.11 或 3.12；
- Node.js 20.x～26.x；
- npm 10.x 或 11.x。

先做完全离线的只读检查或演练：

```bash
./scripts/macos/install.sh --check
./scripts/macos/install.sh --dry-run
```

首次安装：

```bash
./scripts/macos/install.sh
```

安装脚本只会在仓库内创建 `.venv`、按已有
`backend/requirements.txt` 安装依赖、执行 `frontend/npm ci` 和生产构建；
不会创建、删除或覆盖 `DATA_DIR`。

启动前诊断与前台启动：

```bash
./scripts/macos/doctor.sh
./scripts/macos/start.sh
```

`start.sh` 必须先通过 doctor，随后复用现有 Python launcher 并保持前台
运行；按 `Ctrl-C` 由 launcher 清理 worker。macOS 脚本默认只监听
`127.0.0.1`。只有用户在调用脚本时显式设置 `APP_HOST` 才会改变监听地址，
例如 `APP_HOST=0.0.0.0 ./scripts/macos/start.sh`；暴露到局域网前应另行完成
目标环境安全评估。脚本不安装 daemon，也不创建 launchd 服务。

以上说明的是代码能力和离线测试结果，不代表目标 MacBook 已完成安装、登录、
页面保存 Keychain 凭据或真实模型联调。

## macOS 备份与恢复

创建脱敏备份：

```bash
./scripts/macos/backup.sh
```

默认输出到 `~/Documents/3d66-label-system-backups`；也可显式指定仓库和数据
目录之外的位置：

```bash
./scripts/macos/backup.sh --backup-dir "/Volumes/Safe Disk/3d66 backups"
```

备份使用 Python `sqlite3` backup API 生成一致数据库副本，复制 `images/`，
并生成版本化 `manifest.json`（时间、数据库迁移版本、可用时的 Git commit、
相对文件路径、大小与 SHA-256）。目录权限收紧为 `700`，文件为 `600`。

正式备份不会包含 `logs/`、`.env`、API Key、Keychain/DPAPI 内容或登录会话。
数据库副本会清空 `session_tokens`，同时清空主模型和优化模型的
`encrypted_api_key` 字段，再执行 `VACUUM` 后计算哈希。因此恢复后会话不会
恢复，API Key 必须在目标机重新填写；禁止跨平台复制 Keychain 或 DPAPI。

恢复前可单独做只读校验：

```bash
./scripts/macos/restore.sh \
  --backup "/path/to/3d66-backup-v1-YYYYMMDDTHHMMSSZ" \
  --dry-run
```

实际恢复使用同一命令去掉 `--dry-run`。脚本仍会自动先完成一次 dry-run，
校验 manifest schema、相对路径、SHA-256、SQLite `integrity_check` 和迁移
版本，再检查服务端口必须停止。通过后先为当前 database/images 创建权限
收紧的本地临时回滚快照，再做原子替换；失败时自动补偿恢复，成功或成功
回滚后删除临时快照。

## 局域网访问

launcher 可能显示本机和局域网地址，但是否可达由实际绑定地址决定：

- 当前电脑：`http://127.0.0.1:8080`
- 同一局域网：例如 `http://192.168.1.20:8080`

受控脚本默认绑定 `127.0.0.1`，因此局域网地址默认不可达。只有显式完成安全
审批并设置进程 `APP_HOST` 后，其他审核员才可使用局域网地址；本仓库脚本不
修改防火墙。主机需要保持开机，启动窗口不能关闭。

## 日常操作

1. 在“素材”批量上传 JPG、PNG 或 WebP。
2. 选择图片并创建任务。
3. 后台处理器先调用 A；只有 `in_scope` 或 `boundary` 才继续调用 B。
4. 服务端按固定权重和等级限制计算最终分数。
5. 在“结果审核”查看原图、八维证据、缺陷、限制和版本快照；审核账号自动取当前登录账号。
6. 需要调整提示词时进入“提示词”；AI 只生成草案，保存后仍是新版本草稿。

没有配置 API Key 时，任务会保持排队，不会被标记为失败。

## 发布共享测试环境

项目主仓库为云效 Codeup：
`https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git`。

发布脚本会读取 Codeup `main` 的最新提交，制作临时发布包，通过 SSH 上传到
测试服务器 `192.168.1.35`，再执行服务器上的受保护发布脚本。服务器项目目录、
测试容器和业务数据目录彼此独立，发布失败会自动回滚到上一个提交。

当前 Windows 发布机已配置专用 SSH 密钥，正常发布不再要求输入服务器密码；
密钥路径为 `~/.ssh/3d66_label_test_ed25519`，不会进入项目仓库。更换电脑时，
需要先把新电脑的公钥加入测试服务器，并保留服务器上的固定免密发布规则。

仓库根目录不放置双击部署入口。Windows 和 macOS 的双击入口由仓库外的独立
部署工具目录提供；仓库内统一使用下面的命令，确认摘要无误后输入 `DEPLOY`：

```bash
python3 scripts/deploy-test.py
```

首次使用或只想检查发布包时，可执行：

```bash
python3 scripts/deploy-test.py --dry-run
```

发布完成后访问 `http://192.168.1.35:8081`，健康检查地址为
`http://192.168.1.35:8081/api/health`。这个流程不依赖 Jenkins；后续需要提交
审批、自动触发、构建记录或多人权限管理时，再把同一个脚本接入云效流水线即可。

## 从豆包 1.8 迁移到 2.0

旧模型停止服务后不需要重开 1.8，只要历史结果仍保存在本系统：

1. 先确认 1.8 结果包含模型、提示词、规则和引擎版本快照，并完成核心图片的人工确认。
2. 在“样本集”创建黄金样本，将人工确认图片收录进去；可修改基准等级并记录判断依据。
3. 在“模型配置”把候选模型改为 2.0，填写或更新 API Key，并测试连接。
4. 进入“模型迁移”，选择 1.8 历史结果作为旧模型基线，并选择固定样本集。也可以不选样本集，让系统自动分层抽样。
5. 固定样本集会全部重跑；自动抽样正式首轮建议 200 张，不足 200 时使用全部可用图片。
6. 系统使用当前 2.0 配置重跑同一批图片，并以样本集的人工等级为质量基准。
7. 高置信度且等级/分类一致的样本自动通过；等级或分类变化、低置信度、模型主动请求复核，以及约 5% 的一致样本进入人工队列。
8. 审核员只需判断“旧模型更好 / 效果相当 / 新模型更好”。出现人工确认的“旧模型更好”时，批次标记为发现回退。

“样本验收通过”表示该分层样本未发现人工确认的回退，不是对全部未来图片的绝对保证。上线后应持续抽检少量新图，监控数据漂移。

## 什么是“保存完整模型响应”

系统同时保存：

- 豆包接口返回的原始 JSON；
- 从文本中解析出的结构化 JSON；
- 服务端计算的分数、等级和限制；
- 模型、提示词、规则和评分引擎版本。

这样可以在模型升级、提示词调整或异常排查时还原“当时模型实际返回了什么”。原始响应可能增大数据库体积，也可能包含图片分析内容，因此只在当前电脑保存，不通过 Git 同步。

## 数据位置

Windows 默认数据位于：

`%LOCALAPPDATA%\3d66-label-system`

macOS 默认数据位于：

`~/Library/Application Support/3d66-label-system`

其中包含 SQLite 数据库、图片和日志。Windows 的 DPAPI 密文绑定当前用户；
macOS 数据库只持有当前用户 Keychain 条目的稳定引用。复制数据库到另一台
电脑、另一系统或另一用户后都不能直接取得原 API Key，必须重新填写。

如需改变数据目录，把 `.env.example` 复制为 `.env`，取消 `DATA_DIR` 注释并
填写仓库之外的绝对路径；显式 `DATA_DIR` 优先。`.env` 不会进入 Git。

## 开发验证

前端生产构建：

```powershell
cd frontend
npm run build
```

后端测试：

```powershell
cd backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

当前 Windows 生命周期专项：`29 passed`；安全层专项：
`15 passed, 2 skipped`；全后端：`420 passed, 2 skipped, 1 warning`。
Python 3.12 编译、脚本严格模式/UTF-8/参数/退出码/危险命令静态回归和
`git diff --check` 通过。当前 MacBook 没有 `pwsh`，未做 PowerShell parser
机检；本阶段未修改前端源码，未重新执行前端构建或浏览器验收。

以上 macOS 侧自动化测试全部只使用临时目录和明显假数据，未部署、未访问真实
Windows 目录或生产数据、未读取 DPAPI、未调用真实模型。Windows-only DPAPI
实机用例在 macOS 跳过；当前执行沙箱不允许访问登录 Keychain（OSStatus -50），
该真实 Keychain 用例也明确跳过，其他错误仍会失败。

### 原生 Windows 实机验收（2026-07-31）

上面跳过的 Windows-only 部分已在一台原生 Windows 11 验证机（PowerShell
5.1.26100.8115 Desktop、Python 3.11.4、Node v24.15.0）上单独跑过：
`doctor.ps1` 全量门禁 9/9 通过 ×3 轮（`CurrentUser`、`LocalMachine`、默认
`DATA_DIR`），含真实 DPAPI 内存回环；非空 API Key 保存后落库为 `dpapi:v1:` /
`dpapi-machine:v1:` 引用，明文不进数据库、不进接口响应、不进日志；解密结果
经字节级比对与原文一致；`current-user` 写入的引用在 `local-machine` 运行时下
仍能正常解密，即**切换 DPAPI 范围不会锁死既有凭据**。

两点必须注意：

- 该验收**绕过了 `install.ps1` 与 `start.ps1`**（手工复制其安装与启动步骤），
  因为这两个脚本在 PowerShell 5.1 上实测不可用——`start.ps1` 无论怎么调都起
  不了服务。仓库根三个 `.cmd` 入口都调 `powershell.exe`（即 5.1），所以在修掉
  缺陷或统一要求 PowerShell 7 之前，操作员双击 `.cmd` 的路径走不通。
- PowerShell 脚本层 `-DpapiScope` 默认 `LocalMachine`，而 Python 层
  `API_KEY_DPAPI_SCOPE` 默认 `current-user`；两层默认值相反，靠启动脚本注入
  环境变量才对齐。直接运行 `python -m app.launcher` 得到的是 `current-user`。
