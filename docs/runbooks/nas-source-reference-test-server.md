# 测试服 NAS 原图只读引用

## 目标

测试服只读挂载 `//192.168.1.51/maps`，应用在数据库中保存
`nas://maps/<相对路径>`，容器只读映射 `/mnt/label-nas/maps`。系统不会把 NAS
原图复制到 `/data/images`，也不会保存 NAS 用户名、密码或完整 UNC 路径。

## 配置

1. 在测试服安全目录创建仅 root 可读的 SMB 凭据文件，权限必须为 `600`，内容由运维按公司凭据规范提供；不要把内容写入命令行、仓库、数据库、日志或聊天。
2. 确认服务器已安装 `cifs-utils`，执行：

   ```bash
   sudo scripts/configure-nas-test-server.sh /root/.config/label-nas/maps.credentials
   sudo scripts/verify-nas-test-server.sh
   ```

3. 验证脚本必须确认来源为 `//192.168.1.51/maps`、挂载选项含 `ro`，并且四个运营目录可读。

## 部署门禁

`scripts/deploy-test-server.sh` 在切换代码和启动 Compose 前执行上述验证。挂载不存在、来源不匹配、可写或目录缺失时立即停止，保留当前版本，不创建本地替代目录。

Compose 将主机挂载以 `:ro` 映射到容器，并设置 `NAS_MAPS_ROOT=/mnt/label-nas/maps`。挂载恢复后可重试部署；已有本地素材仍按原路径读取。

## 验收与回退

- `findmnt` 显示 NAS 来源和 `ro`；
- 容器 `running/healthy`，`/api/health/ready` 返回成功；
- NAS 导入素材的 `source_uri` 为 `nas://maps/...`，图片接口读取成功且哈希与导入时一致；
- 修改 NAS 文件或卸载挂载后，接口和评测任务明确失败，不回退到本地猜测路径；
- 本地旧素材仍可读，未删除或迁移任何旧文件。

回退时先停止新版本，再卸载 NAS 挂载并恢复上一版本代码；不要删除 `/data/images` 或数据库历史记录。
