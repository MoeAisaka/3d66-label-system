# ADR-0012：macOS Keychain 与跨平台版本化凭据引用

- 状态：Accepted
- 日期：2026-07-28

## 背景

系统原先只支持 Windows DPAPI，并把 DPAPI 密文字节的 Base64 文本直接存入
`encrypted_api_key`。P0-E 后续需要先在 macOS 工程环境验收，再交研发部署到
Windows。真实 API Key 不能进入 SQLite、日志、异常、命令行、临时文件或
Git；主模型与提示词优化模型也不能共享同一个凭据槽位。

当前数据模型已经有 `ModelConfig.encrypted_api_key` 和
`OptimizerConfig.encrypted_api_key`，因此本决策不增加表、不迁移数据，也不
改变前端契约。

## 决策

### macOS

- 使用 Security.framework 的 generic password 条目作为真实密钥源。
- 后端只用 Python 标准库 `ctypes` 直接调用
  `SecItemAdd`、`SecItemCopyMatching`、`SecItemUpdate` 和
  `SecItemDelete`；不使用 `security` CLI、shell、命令行参数、临时文件或
  第三方 Keychain 依赖。
- 固定 service 为 `com.3d66.label-system.api-keys`。
- 主模型固定 account 为 `model-config`，提示词优化模型固定 account 为
  `optimizer-config`。
- SQLite 只保存 `keychain:v1:<account>`。该引用不包含密钥，也不能反推出
  密钥。
- 保存时先按 service + account 调用更新；不存在才新增。并发新增返回
  `errSecDuplicateItem` 时重新更新，从而保持每个 account 只有一个有效
  条目。
- 读取结果按 UTF-8 严格解码；CoreFoundation 创建规则为
  `Create`/`Copy` 的对象全部在所有成功或失败路径释放。

### Windows

- 继续使用当前 Windows 用户的 DPAPI。
- 新写入保存为 `dpapi:v1:<base64 ciphertext>`。
- 为兼容既有数据库，Windows 仍可读取没有前缀的旧 DPAPI Base64 密文。
- DPAPI 调用设置明确的 `argtypes`/`restype`，并使用
  `CRYPTPROTECT_UI_FORBIDDEN`，不允许凭据路径退化为交互式明文处理。

### 共同边界

- `model-config` 与 `optimizer-config` 是仅有的两个业务 account；未知
  account、未知引用格式、引用与当前平台不匹配、Keychain 条目不存在或系统
  框架不可用时全部 fail-closed。
- 空白密钥拒绝。配置请求使用秘密类型承载 API Key，DTO 表示不显示明文；
  安全存储失败只返回固定错误，不把密钥或底层敏感输入写入响应和异常。
- `api_key=None` 表示保留现有凭据；本决策不增加通过 API 删除生产凭据的
  能力。
- 跨系统、跨用户或换电脑不迁移可解密凭据。用户必须在目标电脑重新填写。

## 后果

- macOS 数据库泄漏只会暴露稳定 account 引用，真实 API Key 仍受当前登录
  Keychain 保护。
- Windows 旧数据库无需数据迁移；下一次保存后自然升级为带版本前缀格式。
- SQLite 与 Keychain/DPAPI 无法组成同一原子事务。由于 macOS 引用固定，
  更新 Keychain 后数据库提交失败时，已有引用仍指向最新条目；新建配置提交
  失败可能留下不可达条目，需要通过受控维护流程清理。
- macOS 登录 Keychain 必须可用且已解锁；无 GUI、无登录会话或 Keychain
  策略限制时会关闭失败，不回退到文件或明文。

## 验证

- 平台无关单测使用模拟 Security.framework/平台覆盖稳定引用、双 account
  隔离、UTF-8、更新优先、新增、重复竞态、删除、OSStatus、未知格式、错
  平台、旧 DPAPI 兼容、空密钥及异常脱敏。
- macOS 真实集成测试使用随机隔离 service 和明显假密钥，执行新增、读取、
  原位覆盖、读取新值，并在 `finally` 删除；删除后再次读取必须返回
  `errSecItemNotFound` 对应错误。
- 2026-07-28 验证：安全专项 `15 passed, 1 skipped`；全后端
  `328 passed, 1 skipped`。macOS 真实 Keychain 用例已执行；Windows 真实
  DPAPI 用例在 macOS 由测试自身跳过；`compileall` 与
  `git diff --check` 通过。

## 未完成

- 尚未在目标 MacBook 完成安装、启动、登录与页面保存凭据的部署验收。
- 尚未使用真实 API Key 或真实模型执行连接测试、评测或提示词优化。
- Windows 研发机的真实 DPAPI 回归与最终部署仍待目标环境执行。

## 不可破坏约束

- 不得把 macOS Keychain 改为 `security` CLI 或任何会让密钥进入 shell、
  命令行参数、环境输出、临时文件或日志的实现。
- 不得把真实密钥、可逆的 macOS 密钥副本或跨平台通用明文写入数据库。
- 不得复用同一个 account 保存主模型与提示词优化模型密钥。
- 不得静默接受未知引用、跨平台引用或在不受支持平台回退到弱存储。
