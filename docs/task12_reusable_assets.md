# 任务12可复用资产清单

本文列出任务12之后可以直接复用的代码、合同、测试、运行流程和工程原则。复用的含义是保留已验证边界并做最小扩展，不是复制当前平台的页面元素去假装支持第二个平台。

## 1. 公共业务层

| 资产 | 位置 | 可复用范围 | 不应做的事 |
| --- | --- | --- | --- |
| 运行态任务服务 | `app/services/runtime.py` | 任务状态变更、历史记录 | RPA 直接写任务表 |
| 平台名称规范化 | `app/platform_identity.py` | 规范平台代码和名称 | 在公共层写死页面标题 |
| 页面业务身份规范化 | `app/listing_identity.py` | 品种、等级归一化和业务键 | 用固定行号作为长期身份 |
| 平台状态策略 | `app/listing_status_policy.py` | 状态写入、库存和在售事实边界 | 用目标价冒充实际回读价 |
| SQLite Repository | `app/repositories/sqlite_runtime_repository.py` | v11 schema、事务、回读 | 绕过 Repository 拼接业务 SQL |

## 2. ShadowBot 合同和调度层

| 资产 | 位置 | 复用方式 |
| --- | --- | --- |
| v4 批次合同 | `app/services/shadowbot_commit_batch.py` | 保留一次请求、完整 items、逐项哈希、批次哈希和禁止页面位置字段 |
| 批次管线 | `app/services/shadowbot_commit_pipeline.py` | 从任务中心读取、SKU 映射、准备账本、原子发布和结果更新 |
| 合同基础函数 | `app/shadowbot_contract_primitives.py` | 金额、文本、哈希和规范化逻辑共享 |
| 队列服务 | `app/services/shadowbot_queue.py` | `.ready.json + .sha256`、working phase、结果导入、隔离和归档 |
| Executor 状态机 | `app/services/shadowbot_executor.py` | operation/attempt、side-effect、UNKNOWN 和唯一 RECONCILE |
| CLI | `scripts/run_shadowbot_commit_batch.py` | 开发清单预览、生产任务批次构建和单次投递 |

新增平台时可以复用合同和状态机，但必须新建平台 adapter/executor；不能把蚂蚁花团供应商的 selector 或微信窗口逻辑放进公共合同。

## 3. 当前平台 adapter/executor

| 资产 | 位置 | 已验证能力 |
| --- | --- | --- |
| 主执行代码 | `shadowbot/test2/vertical_slice_read_price.py` | 登录、刷新、READ_ONLY、批次预扫描、COMMIT、独立回读、RECONCILE |
| 长驻 Worker | `shadowbot/test2/shadowbot_queue_worker.py` | 单线程领取、租约、phase、结果发布、停止信号 |
| 商品索引规律 | 当前平台 adapter 内部 | 商品名称 `wx-view index=1,17,33...` 及同行字段偏移 |
| READ_ONLY 结束判定 | 当前平台 adapter 内部 | 下一个索引不存在且出现“没有更多了”时结束 |
| 视口恢复 | 当前平台 adapter 内部 | 依据实际元素边界向上/向下滚动，不通过误点击试错 |

这些内容只能在蚂蚁花团供应商页面结构仍相容时复用。微信、小程序或页面升级后，先运行只读结构冒烟和非默认视口回归，再开放 COMMIT。

## 4. 数据和映射

| 数据 | 权威含义 | 使用规则 |
| --- | --- | --- |
| `data/samples/products.xlsx` | 商品主数据和内部 SKU 映射 | SKU 必须唯一映射到启用的商品名称和等级 |
| SQLite `tasks` | 正式运行态任务 | COMMIT 输入使用 SKU、旧价、目标价 |
| SQLite `listing_status` | 最近一次已确认的平台状态 | 不是无条件实时事实；COMMIT 仍需页面核价 |
| `shadowbot_commit_batches` | 批次账本 | 保存合同版本、平台、attempt、result 和终态 |
| `shadowbot_commit_batch_items` | 逐商品账本 | 保存预扫描行、实际执行顺序、提交状态和回读价 |

禁止重新引入并行的静态 SKU JSON 作为正式默认映射。测试映射文件必须明确标注测试用途。

## 5. 可复用安全门禁

1. 正式写操作前所有目标必须唯一存在。
2. 正式写操作前所有页面旧价必须与任务旧价一致。
3. 批次任何目标不满足门禁时，不提交任何商品。
4. 页面行号只在当次运行内使用，不进入正式任务合同。
5. 每个成功项必须有提交后的独立平台回读。
6. 提交后结果未知时停止剩余项，禁止自动重试 COMMIT。
7. Result Importer 必须验证请求/结果/哈希/逐项绑定/计数恒等式。
8. `listing_status.current_price` 只接受真实回读价，不回退到目标价。
9. READ_ONLY 不依赖目标清单，也不把页面未出现商品自动判为下架。
10. 开发对话确认与正式任务业务授权分层处理，不能互相替代。

## 6. 可复用测试

| 测试 | 保护内容 |
| --- | --- |
| `tests/test_shadowbot_commit_success_baseline.py` | 已成功的弹窗提交动作不被重写或绕开 |
| `tests/test_shadowbot_commit_batch.py` | v4 manifest/request 字段、哈希和重复身份门禁 |
| `tests/test_shadowbot_commit_pipeline.py` | 任务读取、SKU 映射、账本和发布流程 |
| `tests/test_shadowbot_commit_v4_orchestration.py` | 预扫描、实时排序、视口恢复和逐项执行 |
| `tests/test_shadowbot_readonly_snapshot_baseline.py` | READ_ONLY 全页面快照基线 |
| `tests/test_shadowbot_product_read.py` | READ_ONLY 合同和商品结果 |
| `tests/test_listing_status.py` | 平台身份、库存新鲜度和状态回写 |

后续修改当前平台执行代码时，至少先运行以上测试。涉及 task、Repository 或 Web 回写时还需要运行对应运行态和 Web 测试。

## 7. 可复用运行流程

```text
读取 lifecycle state
→ 若 RUNNING 且 Worker/队列事实一致：继续复用 test2
→ 若 STOPPED：从影刀应用列表启动 test2
→ 若记录与实际不一致：进入异常恢复并按需重启影刀
→ 投递任务并等待 Result Importer 归档
→ 后续仍有任务：保持 Worker 运行
→ 需要结束：确认无活动请求，创建 stop.signal
→ 等待 Worker STOPPED 和末端 关闭.flow
→ 删除 stop.signal 并回读确认
```

仅在修改 `test2`、达到 8 小时/50 任务上限或状态异常时重启。不要因为完成一条请求就关闭影刀。

## 8. 下一个开发阶段的使用建议

### 任务13：上下架和 OFFLINE 对账

直接复用：

- v4 批次合同的单次请求和逐商品账本模式。
- SKU 映射、页面唯一身份和完整快照。
- 副作用状态、UNKNOWN→RECONCILE 和 Importer 校验。
- 长驻 Worker 生命周期。

必须新增：

- “待上架/上架中/审核中/未通过”等页面状态 adapter。
- 上架和下架各自的副作用边界与独立回读定义。
- 页面未出现商品与真实 OFFLINE 的可证明关系。
- 状态任务自己的旧状态门禁，不能套用价格字段。

### 第二平台

可以复用公共合同、任务、账本、队列、Importer 和状态机；必须新建平台专属元素、登录、页面读取和动作实现。第二平台必须先完成结构化 READ_ONLY 基线，不能复制当前等差索引并假设页面相同。

### 性能优化

继续使用 `batch_performance` 分别记录窗口准备、刷新、预扫描、逐项执行和总耗时。每项结论同时保留冷态和暖态样本，优先减少批次固定成本，不削弱独立回读。
