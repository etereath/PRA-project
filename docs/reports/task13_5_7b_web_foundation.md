# 任务 13.5-7B：运营 Web 应用基础实施报告

- 实施日期：2026-08-12
- Review Profile：R4
- 分支：`codex/task13-5-7b-web-foundation`
- 基线：`08ea0d2`（7A PR #31 合并提交）
- 范围：应用骨架、安全、权限、GET 零写、外部协议外壳和生命周期解耦

## 1. 结论

7B 建立了新的 `app/operations_web` 生产代码边界。它不复用旧 `app/web.py` 的请求级路径、
HTML 拼接、业务 Route 或隐式数据库初始化；只复用已验证的 SQLite 只读健康检查、安全审计/
登录限流以及原 Mobile Review 外部路径形状。

当前新应用具备：

- 启动时固定的 Runtime DB、商品/价格/上下架工作簿和 Queue 根目录；
- development/production 与 HTTP/HTTPS、Secure Cookie 的显式一致性校验；
- 有界内存 Session、登录后轮换、POST 退出和 CSRF；
- 后端集中 capability 判定，模板不决定权限；
- CSP、`X-Content-Type-Options`、frame、Referrer 和 Permissions Policy；
- `/health`、登录/退出、四入口安全骨架和 Mobile Review 无写入外壳；
- 打包进 wheel/sdist 的本地 HTML/CSS，不依赖 CDN；
- 独立的 Web 与 Queue Service 启停脚本。

7B 没有业务 POST，没有 Runtime Schema 变化，没有真实平台动作，也没有把静态样板中的示例
数据接入生产页面。

## 2. 复用矩阵

| 能力 | 分类 | 7B 处理 |
| --- | --- | --- |
| `SQLiteRuntimeRepository.check_schema_health()` | 原样复用 | `/health` 和页面就绪提示只读调用 |
| `check_operational_health()` 与 `connect_read()` | 原样复用 | 不调用 `init_schema()`、迁移或写连接 |
| `LOGIN_RATE_LIMITER`、`record_security_event` | 原样复用 | 登录失败、CSRF 和 capability 拒绝继续进入有界安全审计 |
| `/health` 的 `200 ok / 503 unhealthy` 外部探针 | 参数化复用 | 数据库目标改由 Composition Root 固定，拒绝请求覆盖 |
| `/mobile/review/{id}`、`/resolve` 路径形状 | 参数化复用 | 7B 返回 `503` 无写入外壳，不回显 ID/token |
| 旧内存 Session 思路 | 抽取公共能力 | 新建独立、有界、带锁的 SessionManager；Session 不保存路径 |
| 登录 CSRF、轮换、HttpOnly/SameSite | 抽取公共能力 | 去除旧 Runtime 页面和 request DB 依赖后重新实现 |
| CSP/frame/Referrer、capability、统一错误边界 | 确需新增 | 仓库此前不存在统一实现 |
| 本地模板/CSS package data | 确需新增 | wheel/sdist 严格 allowlist 同步收紧 |
| Web/Queue 生命周期拆分 | 确需新增 | 删除 `start_local.ps1` 对 Queue Service 的拥有和停止行为 |
| 旧重试归档并发竞态 | 门禁窄修复 | 同一源文件已被另一线程归档时稳定返回既有冲突，不改变授权/租约/格式 |

## 3. 固定依赖与请求边界

`OperationsWebSettings.from_environment()` 只在 Composition Root 构造时读取环境变量并把路径
解析为绝对路径。容器持有固定 Repository、认证、授权和 Session 服务。Route 不读取环境
变量，也不接受数据库、工作簿或 Queue 路径。

以下字段若出现在 query 或 form，统一返回 `400`：`runtime_db`、商品/规则工作簿路径和
`queue_dir/queue_root`。Session 只保存主体、capability、CSRF 和过期时间，不保存路径。

## 4. 环境与 Cookie

新应用只允许两种完整组合：

| 环境 | 对外协议 | Cookie |
| --- | --- | --- |
| `PRA_ENV=development` | `PRA_WEB_PUBLIC_SCHEME=http` | `PRA_COOKIE_SECURE=false` |
| `PRA_ENV=production` | `PRA_WEB_PUBLIC_SCHEME=https` | `PRA_COOKIE_SECURE=true` |

变量缺失、非法或交叉组合均在应用构造时抛出中文
`OperationsWebConfigurationError`。生产 TLS 终止仍需部署层证明；代码不信任任意请求转发头
来降级 Cookie。

## 5. GET 零写与真实库问题边界

专项测试在合成 v16 Runtime DB、三份合成工作簿和空 Queue 四目录上比较所有 GET 前后：

- Runtime 主库 size、mtime 和 SHA-256；
- WAL/SHM sidecar 的 size 和 SHA-256；
- 工作簿 size、mtime 和 SHA-256；
- Queue `inbox/working/results/archive` 清单及文件内容。

SQLite 在打开 WAL 数据库的只读连接时可能更新 `-shm` 协调文件的 mtime，因此测试不把该
操作系统级时间戳当成业务写入；sidecar 内容和大小仍必须不变。所有 GET 同时用替身把
`init_schema()` 设为立即失败，证明请求链没有调用它。

真实 Runtime DB 的既有外键违规不在 7B 推断、迁移或修复。新页面捕获只读健康异常并显示
“另走显式维护”的中文提示；`/health` 返回 `503 unhealthy`。7B 按计划只使用合成库，真实
库 READ_ONLY 在 7C 经独立门禁验收。

## 6. 认证、授权和错误边界

- GET 登录页签发一次性预认证 Session；登录成功轮换 Session ID；
- Session Cookie 固定 `HttpOnly`、`SameSite=Lax`，`Secure` 只由启动组合决定；
- 退出只接受 POST 且必须携带当前 Session CSRF；
- `PrincipalCapabilityBackend` 在 Route 调用前判定 `VIEW_TODAY`、`VIEW_DATABASE`、
  `MANAGE_BUSINESS`、`VIEW_SYSTEM` 等 capability；测试替身拒绝后稳定返回 `403`；
- 所有响应，包括静态资源、404、健康失败和异常，统一附加安全 Header；
- 未捕获异常只向用户返回随机错误编号，日志不记录 query；Mobile Review token 不进入访问
  日志或页面。

## 7. Mobile Review 外壳

7B 保留现有 GET `/mobile/review/{review_task_id}?token=...` 和 POST
`/mobile/review/{review_task_id}/resolve` 的路径形状。为了遵守“只用合成库、不接业务 POST”，
两者当前稳定返回 `503` 和维护提示：

- 不校验或消费真实 token；
- 不读取/修改 Review、Task、Incident、Outbox；
- 不回显 review ID 或 token；
- 不创建平台任务或 Queue 请求。

有效、无效、过期、已处理只读状态属于 7C；真正复核写入切换属于后续批准阶段。旧 Web 的
最终切换删除仍在 7F，因此 7B 不宣称真实飞书链接已切到本外壳。

## 8. 生命周期与打包

`scripts/start_local.ps1` 现在只启动 Web，不再检查、创建、拥有或在 Web 退出时停止 Queue
Service。`scripts/start_local_services.ps1` 独立运行既有
`run_shadowbot_queue_services.py`。Automation 与真实 Worker 继续按既有手册独立管理。

`pyproject.toml` 和 `MANIFEST.in` 显式声明 `app.operations_web/templates/*.html` 与
`static/*.css`。严格包校验只允许这两个目录的声明扩展名，并把三个模板和 CSS 加入必需
wheel 成员；没有扩大到任意静态文件或真实数据。

## 9. 测试结果

开发阶段专项：

```text
28 passed, 3 subtests passed
```

覆盖环境冲突、固定路径、GET 零写、缺失 DB 不创建、请求路径覆盖拒绝、Session 轮换、
Secure/非 Secure Cookie、CSRF、POST 退出、capability 拒绝、安全 Header、Mobile Review
零写、7B 无业务 POST、本地资源和严格打包单测。

首次完整回归另暴露既有 `archive_attempt_artifacts()` 的并发竞态：两个线程都通过
`source.exists()` 后，其中一方先完成 `os.replace()`，另一方会泄露 Windows
`FileNotFoundError`，而不是返回测试和上层合同预期的 `ValidationError`。7B 未改写重试
状态机，只在“源已消失且同名归档目标已存在”时稳定返回既有
`OLD_QUEUE_ARTIFACT_CONFLICT`；其他异常缺失返回 `OLD_QUEUE_ARTIFACT_MISSING` 并继续
fail closed。该用例修复后将重复运行并纳入完整回归。

受影响组合：

```text
144 passed, 23 subtests passed
```

并发恢复竞态用例修复后连续运行 20 次均通过。完整回归：

```text
1147 passed, 3 skipped, 97 subtests passed
```

系统冒烟：`16 passed, 0 failed`。Windows Core fixture 通过。实际 wheel/sdist 构建、严格
边界、secret scan、仓库外 wheel 安装、运营 Web 资源读取、CLI、v16 初始化和健康检查均
通过。Linux Core 由 Draft PR CI 统一验证；本地 Windows 结果不能替代 Linux Runner。

最终本地发行物摘要：

```text
wheel sha256=885067a4d7e8637a09d26272d2e5eedf9503eba5a21588d4209353d5cb9f610c
sdist sha256=2b23d9b938daecf6b945c026efafa3a6fd3f09506cb3e8365f1364ce0300c2cd
```

## 10. 明确未实现

- 四入口经营事实、分页、详情和销售分析；
- 真实库存、销售扣减、取消恢复和预警；
- 即时任务、执行授权、人工复核和 Automation 配置业务 POST；
- Worker 恢复、备份、飞书测试等 Maintenance Service；
- 旧 Web 默认入口切换、旧 Route/Presenter/测试删除；
- 真实 Runtime DB READ_ONLY、手机/桌面、飞书或真实平台验收；
- Agent、第二平台或任何新增平台动作。
