# LabelLab 等级档位与豆包 Thinking 控制验收回执

## 范围

- 基线：`830188aca3ab673b62beed6ea68c42d7a9c62294`
- 目标：类目等级档位专用 API/前端、豆包 thinking 控制、快照与 provider trace 可追溯。
- 环境：隔离 worktree；不修改生产。

## 已实现

- `level_scale` 增加关闭档、展示名、分数整数校验、启用档引用校验和旧阈值投影。
- 等级 API 使用 revision/hash 乐观并发保护，支持等级和红线命中档原子切换，写入审计事件。
- `ModelConfig.thinking_mode` 默认 `auto`，迁移 60；豆包显式模式发送 `thinking.type`，OpenAI 保持 `reasoning_effort`。
- 策略快照、A/B provider trace 记录 thinking 模式且不写入密钥。
- 前端增加 L1-L5 行式档位编辑和豆包 thinking segmented control。

## 验证命令

```text
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m compileall -q backend/app
cd frontend && npm run contract:level-scale-thinking
cd frontend && npm run build
git diff --check
```

定向测试已覆盖等级纯函数、等级 API、豆包 payload、策略快照、provider trace 和迁移回填。全量结果以最终部署回执中的实际命令输出为准。

## 部署门禁

必须生成公布 `refs/remotes/origin/main` 的 Git bundle，并通过 `/usr/local/sbin/deploy-3d66-label-test` 部署到 `192.168.1.35`。部署后核验 health=200、数据库 integrity/FK、schema migration=60、容器健康、等级 API revision/hash 与前端静态资源；不调用真实业务评测。
