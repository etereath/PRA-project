# Web 安全边界矩阵

本矩阵对应任务 1B/1C 交接记录，用于说明路由、路径、会话、CSRF、Cookie、限流与审计边界。更新日期：2026-07-16。

| 范围 | 读取行为 | 写入行为与保护 | 关键边界 |
| --- | --- | --- | --- |
| `/health` | 只读取固定受信任运行库 | 不建立 Session，不接受请求路径 | 健康检查不受 Web 路径参数影响 |
| `/runtime/login` GET | 只渲染登录页 | 不校验凭据、不消耗登录限流配额、不写失败审计；签发绑定浏览器的 HttpOnly、SameSite=Lax 预认证 Cookie | 预认证 CSRF 上下文有 TTL、容量上限，并在 POST 单次消费 |
| `/runtime/login` POST | — | 预认证 Cookie + 登录 CSRF；失败受限流保护；成功轮换 Session ID | 错误/过期/重放/跨浏览器 token 均拒绝；非 GET/POST 返回 405 |
| `/runtime/logout`、`/runtime`、`/reviews`、`/execution-logs`、`/business-inputs`、系统通知 | GET 仅读 | Session 写操作要求 Session CSRF（表单、JSON、Header 均支持）；登出清理 Session、预认证 Cookie 与 CSRF 上下文 | 旧 Session Cookie、旧 CSRF token 在轮换/登出后失效 |
| `/tasks?task_tab=automation` | GET 只读已有数据库 | 不调用 schema 初始化，不创建缺失数据库 | 自动化页读取失败时返回空态，不改变应用状态 |
| `/mobile/review/{id}` | 使用独立移动复核 token | 不依赖后端 Session CSRF；token 单次使用、过期失效 | 与 Web Session/CSRF 边界隔离，审计不记录 token 或完整 URL |
| Web 文件路径 | 请求路径与服务器默认路径均经同一策略解析 | 默认路径也必须落在 `PRA_ALLOWED_DATA_DIRS`；`allow_create=False` 的缺失目标拒绝 | 拒绝原始及多层 URL 编码 `.`/`..`、UNC/设备/驱动器切换、尾随点空格，以及解析后越界的 symlink/junction |
| Cookie | — | 生产环境 `Secure; HttpOnly; SameSite=Lax`；开发环境可显式关闭 Secure；删除时保持一致属性 | 预认证与 Session Cookie 均遵循同一安全属性矩阵 |
| 安全审计 | 事件保留最近 1000 条 | 登录失败、CSRF、路径拒绝等高频事件按类型/主体/路由/原因限速并汇总 | 日志不包含密码、Session/CSRF/token、Mobile Review URL；限流表有容量上限 |

## 验证证据

- 路径专项：默认路径、allowlist、编码遍历、Windows 特殊命名空间、尾随点/空格、symlink/junction 场景均覆盖；当前环境可执行的路径测试通过。
- CSRF/认证专项：登录 GET 只读、预认证绑定与单次消费、Session 轮换/登出、Cookie 属性、限流 key/窗口/转发头、审计限速与 automation GET 只读均覆盖。
- 回归结果：`python -m pytest -q tests` — 342 passed，60 个子测试通过；`python scripts/run_system_smoke_tests.py` — 16/16 通过。
