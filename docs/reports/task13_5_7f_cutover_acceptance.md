# 任务 13.5-7F：系统维护、切换删除与运营验收报告

- 实施日期：2026-08-13
- Review Profile：`R3`；真实平台发布继续沿用既有 `R4` 门禁
- 分支：`codex/task13-5-7f-cutover-acceptance`
- 基线：`e27046b`（7E PR #35 合并提交）

## 1. 结论

7F 已把新运营 Web 收敛为仓库唯一 Web 实现，删除旧 `app.web`、旧样式和重复测试，
同时补齐系统运行状态、通知、数据与备份、高级诊断四个分区。系统维护 POST 只接受固定
类型化意图，不接受脚本、命令、SQL、路径或 Queue 内容，也不在 Web 请求内启动 Worker、
发送通知、执行备份或等待长任务。

本分支没有迁移或修复真实 Runtime DB，没有启动或重启真实 Worker，没有投递真实 Queue，
没有发送真实飞书，也没有执行真实平台读写动作。真实飞书验收需要独立通知后台服务具有
新鲜心跳；真实平台写验收仍需用户另行明确商品和批次授权。

## 2. 复用与新增矩阵

| 能力 | 分类 | 7F 处理 |
| --- | --- | --- |
| Worker 健康与恢复 | 原样复用 | 读取既有 Worker health；异常进入既有 `WORKER_UNAVAILABLE` Incident 和恢复 Automation |
| 通知测试 | 原样复用 | 只写既有 Notification Outbox；由独立 Notification Worker 实际发送 |
| 运行备份 | 参数化复用 | 薄 Automation Handler 调既有 `release_backup.py` 创建与回读验证，不复制备份逻辑 |
| Automation Run、租约、完成接口 | 原样复用 | 新增 `MANUAL_ONLY` 固定 Job，只能由类型化维护意图创建 Run |
| 系统状态 | 公共抽取 | 聚合 Web、Runtime、工作簿、Automation、Queue、Worker、Importer、Outbox 和备份状态 |
| 旧 Web | 删除 | 删除旧 Route、HTML 拼接、样式与重复测试；打包门禁禁止其回流 |
| CLI | 保留 | 继续承担测试、Mock、诊断、备份和恢复，不恢复日常运营旁路 |

## 3. 类型化维护与权限

`SYSTEM_ADMIN` 与只读 `VIEW_SYSTEM` 分离。普通系统查看者只能查看运行状态；通知测试、
受控备份和高级诊断均要求管理员能力。所有维护 POST 继续要求 Session、CSRF、确认和幂等键。

- Worker 恢复先读取既有健康报告；健康时零写返回，无健康证据时才创建 Incident 和既有
  Automation Run。Automation Service 必须有 30 秒内 `RUNNING` 心跳并明确注册恢复 Handler。
- 通知测试只创建 `system_test` Outbox；Queue Service 必须有 30 秒内 `RUNNING` 心跳，且
  通知 Worker 已启用、通道与 Web 启动配置一致。
- 备份只创建 `RELEASE_BACKUP_MAINTENANCE` Run；Automation Service 必须在启动时固定
  wheel 和备份目录并注册 Handler。Handler 要求回执绑定同一 Run ID 且回读验证成功。

后台载体缺失、过期、身份或通道不一致时，请求在业务写入前拒绝。Web 重启不启停 Queue、
Worker 或 Automation；`start_local.ps1` 与 `start_local_services.ps1` 继续独立。

## 4. 唯一 Web 与界面验收

`serve-web` 是唯一运营 Web 入口。旧 `app/web.py`、`app/web_styles.py`、旧 Web 测试已删除；
wheel/sdist 审计会显式拒绝重新包含旧模块。系统页不显示 secret、完整 token、webhook 或本地
绝对路径。

内置浏览器使用真实 Runtime DB 的只读页面完成桌面和 `390×844` 手机验收：四个一级入口、
系统四分区和业务管理弹窗均无整页横向溢出。验收中发现并修复：

- HTML `hidden` 被表单 CSS 覆盖，导致平台目标库存误显示；现在全局 `[hidden]` 强制隐藏；
- 静态资源一小时强缓存可能让发布后浏览器继续使用旧交互；现在资源 URL 带 7F 版本并要求
  `no-cache` 重验证；
- 全部平台映射停用时任务弹窗没有选项却可预览；现在显示中文原因并禁用预览。

复验确认：调整价格只显示目标价格；下架不显示价格或库存；上架显示上架价格和平台目标
库存；无可用平台时不能提交预览。

## 5. 真实 Runtime DB 只读验收

受控脚本固定读取真实 Runtime DB、三份工作簿和真实 Queue 根目录，对 `/today`、
`/database`、`/database/project`、`/database/quality`、`/management`、`/system` 发起
已认证 GET。结果：六页均为 `200`，主数据库大小、修改时间与 SHA-256 不变，WAL 内容不变，
预热后的 SQLite 侧车内容不变，`platform_write_performed=false`。

SQLite 只读连接第一次建立 WAL 共享内存时可能更新 `-shm` 锁元数据，因此验收先预热再比较
侧车内容；这不等于业务数据库写入。真实库既有健康问题仍使 `/health` 返回 `503`，页面只
报告不可用。本任务不推断违规来源，也不授权初始化、迁移或修复真实数据。

## 6. 测试与制品

- 最终直接专项：`77 passed, 3 subtests passed`；
- 完整 pytest：`1215 passed, 3 skipped, 82 subtests passed`，耗时 269.13 秒；
- 系统冒烟：`16 passed, 0 failed`，使用临时数据库和 mock 通知；
- Ruff、`git diff --check`、`compileall`：通过；
- wheel/sdist 构建、allowlist、secret scan、仓库外 wheel 安装：通过；
- Windows ShadowBot fixture 与失败退出码：通过；
- wheel/sdist 均不包含旧 Web 模块。

Linux/Windows CI 由 Draft PR 执行；本地 Windows 结果不能替代 GitHub Actions。

## 7. 未关闭的外部验收门禁

以下不是代码缺口，但在外部条件满足前不能宣称 13.5-7 全部运营验收完成：

1. 真实飞书：当前没有经本分支验证的新鲜 Queue Service/Notification Worker 心跳，因此 Web
   会拒绝制造“已发送”假象；需在独立服务运行后由管理员发起一次通知测试并确认手机收到。
2. 真实平台写：本轮没有用户指定商品和批次授权，未执行 COMMIT。后续授权后必须完整通过
   Queue → Worker → Importer → Archive 和既有 UNKNOWN/RECONCILE 门禁。
3. 真实 Runtime 健康：既有外键违规另走显式维护、备份与回读流程；7F 不修复真实数据。
