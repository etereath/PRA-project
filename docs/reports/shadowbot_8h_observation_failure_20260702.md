# ShadowBot 8 小时观察故障分析报告

## 1. 结论

2026-07-02 首轮 8 小时观察判定为 `FAILED`，存在两个相互独立的问题：

1. Worker heartbeat 后台线程因 Windows 文件占用冲突退出，主任务循环仍继续运行。
2. 商品定位器只按前三个固定 `wx-view index` 读取商品，其中一个位置捕获失败；`C级艾莎` 实际一直存在，但位置变化后未被定位器覆盖，两次 READ_ONLY 均误报 `PRODUCT_NOT_FOUND`。

本轮没有发生价格修改或其他平台副作用。两个 READ_ONLY 结果均被 Result Importer 正常导入并归档，最终队列为空，Worker 在收到停止信号后写出 `STOPPED`。

## 2. 时间线

| 本地时间 | 事件 |
| --- | --- |
| 10:26 | 影刀 `test2` Worker 启动 |
| 10:29 | 第0小时 READ_ONLY 固定行定位只读到 D级、B级艾莎，并有一个行位 `ELEMENT_NOT_FOUND`，误报 `PRODUCT_NOT_FOUND` 后导入归档 |
| 11:02:38 | 最后一次成功 heartbeat，状态仍为 `RUNNING` |
| 11:02:43 | 下一次 heartbeat 临时文件写出，但替换目标文件时发生 `PermissionError [WinError 5]` |
| 14:55 | 主 Worker 仍成功领取第二条 READ_ONLY，固定行定位再次误报 `PRODUCT_NOT_FOUND` 并导入归档 |
| 15:09 | 人工停止 Worker；主线程写出最终 `STOPPED`，`processed=2`；影刀在进程退出时输出此前 heartbeat 线程 traceback |

## 3. 直接证据

影刀日志：

```text
C:\Users\etere\AppData\Local\ShadowBot\log\20260702.log
```

关键 traceback：

```text
Exception in thread Thread-1
shadowbot_queue_worker.py, line 141, in _heartbeat_loop
shadowbot_queue_worker.py, line 151, in _write_heartbeat
os.replace(str(temporary), str(path))
PermissionError: [WinError 5] 拒绝访问
```

失败时遗留的完整临时文件已移动到：

```text
D:\PRA_Runtime\shadowbot_queue\archive\8h-observation-20260702\heartbeat.json.tmp-3097d377a5a34fbca4ff4548e29f84e7
```

其内容时间为 `2026-07-02T03:02:43.940547+00:00`，恰好是最后成功 heartbeat 后约 5 秒。SHA-256：

```text
74FFCE36D591B8B328DBA61A504CA3701E2DC11A8AB5F1747E26FC480C8184EA
```

影刀当日日志 SHA-256：

```text
679344FA7CF68171ECB352899F3615904ABAB622CF661B8DD731D602EDC44FDC
```

第0小时和第4小时归档结果均显示：

```text
status=FAILED
error_code=PRODUCT_NOT_FOUND
side_effect_state=NOT_STARTED
fixed rows: 一个 ELEMENT_NOT_FOUND、D级艾莎、B级艾莎
```

2026-07-02 后续通过当前小程序页面直接检查，确认 `C级艾莎` 仍在上架中，并可见于第二个商品卡片。因此上述结果只能证明固定索引定位失败，不能证明商品不存在。

## 4. 根因分析

### 4.1 Heartbeat 线程退出

直接根因是 heartbeat 原子写入最后一步 `os.replace(temp, heartbeat.json)` 遭遇 Windows 目标文件占用，抛出 `PermissionError [WinError 5]`。

第1小时人工检查读取 heartbeat 的时间与冲突时间高度吻合，因此最可能是 `Get-Content heartbeat.json` 与 5 秒写入发生竞争；日志没有记录具体锁文件进程，所以不能把锁持有者作为绝对结论。杀毒软件、索引器或其他读进程也可能产生同类冲突。

放大故障的实现缺陷：

- 原子替换没有针对 Windows 共享冲突重试。
- heartbeat 循环没有捕获单次写异常，一次失败便永久结束线程。
- 主循环没有检测或重启已退出 heartbeat 线程。
- Watchdog 对空闲但 stale 的 `RUNNING` heartbeat 不输出事件。
- Python 线程异常由影刀在进程退出时才集中写入日志，运行期间不易察觉。

### 4.2 固定索引定位产生假阴性

正式脚本当时默认 `max_product_rows=3`，并用 `1 + 16 × (row-1)` 推导商品父级 `wx-view index`，没有先枚举页面中的真实商品名称元素，也没有从元素父级读取实际 index。商品排序或 DOM 位置改变后，一个固定行位捕获失败，C级艾莎被漏掉。

因此 `PRODUCT_NOT_FOUND` 的旧语义不可靠：它实际表示“固定扫描范围没有匹配”，而不是“平台确认商品不存在”。开发文档曾描述动态枚举策略，但正式脚本没有采用已经在探测脚本中验证过的枚举实现，属于实现与文档不一致。

人工流程只检查 `inbox/working/results` 是否被清空，没有立即检查归档结果或运行验收校验器。Result Importer 正常归档失败结果后，活动目录为空被误读为成功。

## 5. 影响评估

- Worker 主循环未退出，仍能领取和执行任务。
- Result Importer 和文件归档正常。
- heartbeat 在约 4 小时内错误地保持旧 `RUNNING` 状态，失去存活证明价值。
- Watchdog 未对空闲 stale heartbeat 告警。
- 两条 READ_ONLY 均在商品定位阶段失败，未触碰输入框或提交边界。
- 本轮 8 小时观察不能作为通过证据。

影刀日志中的 HTTP 403、SSL EOF 和远程日志上传错误与 heartbeat `os.replace` traceback 时间、调用栈不同，当前无证据表明它们是本故障根因。

## 6. 已完成修复

1. `_atomic_write` 对 `PermissionError` 和 Windows 共享冲突错误增加指数退避重试，并清理失败临时文件。
2. heartbeat 循环捕获写入错误，记录失败后继续下一周期，不再退出线程。
3. heartbeat 增加累计失败、连续失败、最后错误、错误时间和线程重启次数字段。
4. 新增 `control/heartbeat_errors.jsonl` 独立错误记录。
5. 主 Worker 检测 heartbeat 线程意外退出并自动重启。
6. Watchdog 对空闲且 stale 的 `RUNNING` heartbeat 输出一次去重的 `WORKER_HEARTBEAT_STALE`。
7. 新增 `scripts/check_shadowbot_worker_health.py` 严格健康检查。
8. 修复后 Worker 已同步到影刀 `test2`；重新运行前必须关闭并重新打开应用。
9. 商品定位改为先枚举全部可访问的商品名称元素，从其父级读取真实 `wx-view index`，再读取同一商品的等级和价格；固定等差索引仅作为兼容回退。
10. 定位失败证据明确区分 `DYNAMIC` 与 `FIXED_FALLBACK`，不再把固定扫描范围描述成完整商品列表。
11. 心跳、队列、Executor、持久化、对账和动态商品定位相关回归持续扩充，最新为 `83 passed`。

### 6.1 动态定位现场复测

`ATTEMPT-READ-DYNAMIC-20260702-165627` 已通过真实 READ_ONLY 复测：动态定位命中 `C级艾莎`，父级 `wx-view index=17`，读取价格 `18.30`，结果为 `READ_COMPLETED`，共享证据及 SHA-256 验证成功。

首次自动导入时，Result Importer 遇到瞬时文件 I/O 错误，把合法结果错误地归类为 `RESULT_CONTRACT_INVALID` 并移入 quarantine。相同文件未经修改再次导入即成功，最终验收全部通过。Importer 已调整为：`OSError/PermissionError` 返回 `RESULT_IO_RETRY_PENDING` 并保留原结果供下一轮重试；只有确定的契约、JSON 或 checksum 错误才隔离，并写入 `.error.json` 原因文件。

### 6.2 第二轮观察 T4 Watchdog 读取竞争

第二轮观察的 `ATTEMPT-8H-T4-20260703-004819` 中，Worker 正常完成 READ_ONLY 并写出结果，但 Watchdog 在读取 `heartbeat.json` 时遇到一次 `PermissionError`，未捕获异常导致 PRA 队列服务退出。结果仍完整保留在 `results`，使用修复后的服务单次导入后全部验收通过：`READ_COMPLETED`、实际价格 `18.30`、共享证据和哈希有效、无平台副作用。

现已为队列 JSON 读取增加指数退避重试，并在队列服务循环中隔离 Watchdog 的 `OSError/ValidationError/JSONDecodeError`。重试后仍失败时仅输出 `RETRY_PENDING/WATCHDOG_INSPECTION_FAILED`，Importer 和后续轮询继续运行，不再因一次 heartbeat 读取竞争退出整个服务。

## 7. 下一轮复测门槛

不直接重跑 8 小时，按以下顺序逐级放行：

1. 关闭并重新打开影刀，确保加载修复代码。
2. 用当前仍在上架的 C级艾莎执行一次 READ_ONLY，验证商品位置变化不会导致误报。
3. 立即运行验收校验器；必须为 `READ_COMPLETED` 且 `ok=true`，并检查结果中的动态父级 index。
4. 进行 5 至 10 分钟 heartbeat 读写竞争测试，多次运行严格健康检查；heartbeat 必须持续新鲜，线程不得退出。
5. 完成 30 分钟短时观察并投递两次 READ_ONLY。
6. 以上全部通过后，再开始新的 8 小时观察。

每次 READ_ONLY 后必须检查 attempt 结果，不允许仅根据活动队列目录为空判断成功。
