# Web 安全边界矩阵

本矩阵对应任务 1B/1C 交接记录，用于说明路由、路径、会话、CSRF、Cookie、限流与审计边界。更新日期：2026-07-16。

| 范围 | 读取行为 | 写入行为与保护 | 关键边界 |
| --- | --- | --- | --- |
| `/health` | 只读取固定受信任运行库 | 不建立 Session，不接受请求路径 | 健康检查不受 Web 路径参数影响 |
| `/runtime/login` GET | 只渲染登录页 | 不校验凭据、不消耗登录限流配额、不写失败审计；签发绑定浏览器的 HttpOnly、SameSite=Lax 预认证 Cookie | 预认证 CSRF 上下文有 TTL、容量上限，并在 POST 单次消费 |
| `/runtime/login` POST | — | 预认证 Cookie + 登录 CSRF；失败受限流保护；成功轮换 Session ID | 错误/过期/重放/跨浏览器 token 均拒绝；非 GET/POST 返回 405 |
| `/runtime`、`/reviews`、`/execution-logs`、系统通知 | GET 仅读 | Session 写操作要求 Session CSRF（表单、JSON、Header 均支持） | 旧 Session Cookie、旧 CSRF token 在轮换/登出后失效 |
| `/runtime/logout` | 不接受 GET、PUT、PATCH、DELETE；统一返回 405，不改变 Session、审计或 Cookie | 仅 POST，要求有效 Session CSRF；成功后清理 Session、预认证 Cookie 与 CSRF 上下文 | 非 POST 请求在 CSRF guard 前拒绝，避免方法差异造成状态变化 |
| `/business-inputs` | GET 只读已有文件；缺失文件返回空态/提示，不调用 ensure/create | 创建或初始化业务映射仅在受 Session + CSRF 保护的 POST 中执行 | GET 前后文件、目录和工作簿内容不变 |
| `/task-generator` | GET 只读取规则与平台摘要并渲染任务生成表单 | 批量及单规则校验、预览与确认生成均要求后台 Session；确认后导出工作簿并通过 RuntimeTaskService 去重写入任务中心；POST 要求 Session CSRF，所有路径均受白名单约束 | 通配平台规则必须选择实际平台；单规则商品任务共享规则组 ID/截止时间并派生组状态，商品任务 ID 仍唯一；重复任务不会再次入库；原 `/` 继续保持安全关闭 |
| `/tasks?task_tab=automation` | GET 只读已有数据库 | 不调用 schema 初始化，不创建缺失数据库 | 自动化页读取失败时返回空态，不改变应用状态 |
| `/mobile/review/{id}` | 使用独立移动复核 token | 不依赖后端 Session CSRF；token 单次使用、过期失效 | 与 Web Session/CSRF 边界隔离，审计不记录 token 或完整 URL |
| Web 文件路径 | 请求路径与服务器默认路径均经同一策略解析 | 默认路径也必须落在 `PRA_ALLOWED_DATA_DIRS`；`allow_create=False` 的缺失目标拒绝 | 拒绝原始及多层 URL 编码 `.`/`..`、UNC/设备/驱动器切换、尾随点空格，以及解析后越界的 symlink/junction |
| Cookie | — | 生产环境 `Secure; HttpOnly; SameSite=Lax`；开发环境可显式关闭 Secure；删除时保持一致属性 | 预认证与 Session Cookie 均遵循同一安全属性矩阵 |
| 安全审计 | 事件保留最近 1000 条 | 高频事件先按事件类型做进程级窗口限速，再保留有限事件摘要 | 高基数主体不能绕过全局日志上限；日志不包含密码、Session/CSRF/token、Mobile Review URL |

## 验证证据

- 路径专项：默认路径、allowlist、编码遍历、Windows 特殊命名空间、尾随点/空格、symlink/junction 场景均覆盖；当前环境可执行的路径测试通过。
- CSRF/认证专项：登录 GET 只读、预认证绑定与单次消费、Session 轮换/登出、Cookie 属性、限流 key/窗口/转发头、审计限速与 automation GET 只读均覆盖。
- 新增复验专项：logout 非 POST 405 且无状态变化、business-inputs GET 不创建缺失工作簿、活跃限流桶不被容量淘汰、高基数审计事件受全局事件类型窗口约束。
- 回归结果：`python -m pytest -q tests` — 342 passed，60 个子测试通过；`python scripts/run_system_smoke_tests.py` — 16/16 通过。
