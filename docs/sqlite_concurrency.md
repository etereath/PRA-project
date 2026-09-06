# SQLite 并发运行说明

## 连接入口

运行时 SQLite 连接统一由 `app.repositories.sqlite_connection.SQLiteConnectionFactory` 创建：

- `connect_read()` 只打开已存在的数据库，不创建父目录、迁移 schema 或切换 journal mode。
- `connect_write()` 允许创建本地父目录，但不自动迁移 schema 或启用 WAL。
- `initialize_database()` 是显式初始化入口，负责确认本地磁盘、启用 `journal_mode=WAL`、设置 `synchronous=NORMAL`，然后执行传入的 schema 初始化。
- `SQLiteRuntimeRepository.connect()` 保留为兼容入口，等价于 `connect_write()`。

每个连接都会设置并验证 `foreign_keys=ON`、`row_factory=sqlite3.Row` 和有界 `busy_timeout`。`:memory:` 与严格解析后的 `file:<name>?mode=memory`、`file::memory:?cache=shared` 仅用于受控测试；磁盘路径、authority、重复参数、编码变体和文件名偶然包含 `mode=memory` 的 URI 都会失败关闭，不宣称具备文件数据库的 WAL 语义。
运行态 `synchronous` 固定为 `NORMAL`；若 `PRA_SQLITE_SYNCHRONOUS` 被设置为其他值，配置会失败关闭。

## 配置边界

环境变量及默认值：

| 配置名 | 默认值 | 安全范围 |
| --- | ---: | ---: |
| `PRA_SQLITE_BUSY_TIMEOUT_MS` | 5000 | 100–30000 |
| `PRA_SQLITE_RETRY_MAX_ATTEMPTS` | 3 | 0–8 |
| `PRA_SQLITE_RETRY_MAX_ELAPSED_MS` | Web 2000；Worker 10000 | 0–10000 |
| `PRA_SQLITE_RETRY_BASE_DELAY_MS` | 25 | 1–500 |

非法值失败关闭，不解释为无限等待。

## 锁与退避

内部 `_execute_with_sqlite_retry()` 只根据 `sqlite_errorcode` 或 `sqlite_errorname` 识别 `SQLITE_BUSY*` 和 `SQLITE_LOCKED*`。它同时受最大调用次数和总耗时限制，并支持注入 `monotonic`、`sleep` 与 `jitter` 以便确定性测试。耗尽预算后抛出 `SQLiteConcurrencyError`，保留 SQLite 错误码但不回显数据库路径或 SQL 参数。

退避只适用于没有外部副作用、可以重新读取权威状态的短数据库操作，例如条件更新、lease 领取和状态竞争。ShadowBot COMMIT、文件发布、UI 点击、Importer 外部文件移动和通知网络发送不得交给该工具自动重试。

ShadowBot lease 续期在每次成功取得 `BEGIN IMMEDIATE` 后重新读取当前时间，并以锁内时间计算新的截止时间；调用前缓存的时间不能延长已经在锁等待期间过期的 lease。测试通过注入时钟和退避等待屏障固定并发时序。

Windows 上拒绝 UNC、设备命名空间和 `DRIVE_REMOTE` 远程盘；路径链中的符号链接、junction 和其他 reparse point 使用 Win32 最终路径解析后再执行本地盘检查。运行中的 WAL 数据库备份应使用 SQLite backup API 或受控停机，不应只复制主 `.sqlite3` 文件。
