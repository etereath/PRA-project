# 任务13共享写锁与中断恢复验收报告

## 结论

任务13的公共写锁和 Worker 中断恢复矩阵已由自动化测试覆盖，可作为持续回归门禁。

这部分不需要新的平台写操作。它验证的是动作发布前的安全边界，以及 Worker 在
phase 已经记录但 result 尚未正常生成时，能否保守地恢复逐商品事实。

## 同一 SKU 的共享写锁

测试 `test_same_sku_write_lock_is_shared_across_all_write_actions` 使用同一个
`internal_sku`，分别构造来源为 `UPDATE_PRICE`、`SET_ONLINE` 和
`SET_OFFLINE` 的写锁，再从三种写操作入口逐一请求。

结果矩阵为：

| 锁状态 | UPDATE_PRICE | SET_ONLINE | SET_OFFLINE | 阻断原因 |
|---|---|---|---|---|
| `ACTIVE` | 阻断 | 阻断 | 阻断 | `WRITE_LOCK_ACTIVE` |
| `UNKNOWN` | 阻断 | 阻断 | 阻断 | `OPERATION_RECONCILIATION_PENDING` |
| `REVIEW_BLOCKED` | 阻断 | 阻断 | 阻断 | `PARTIAL_OPERATION_REVIEW_PENDING` |

门禁不按动作建立独立锁域；锁的主体是平台加内部 SKU。因此改价持有的锁可以阻断
上下架，上下架持有的锁也可以阻断改价。`POST_PUBLISH_PREFLIGHT` 仅允许当前
operation 继续使用自己持有的 `ACTIVE` 锁，其他 operation 仍被阻断。

## phase/result 中断恢复

`tests/test_shadowbot_task13_worker_recovery.py` 覆盖两类关键中断：

1. 上架资料已保存，但尚未完成正式上架：
   - 已可靠回读资料的前序商品恢复为 `PARTIALLY_APPLIED`；
   - 当前资料保存结果无法确认的商品恢复为 `NEEDS_RECONCILIATION`；
   - 批次保持 `UNKNOWN`，不能把部分副作用记成未尝试。
2. 严格串行下架中断：
   - 前序已确认商品保持 `VERIFIED`；
   - 当前最终确认已点击但未回读的商品恢复为 `NEEDS_RECONCILIATION`；
   - 后续尚未开始的商品保持 `NOT_ATTEMPTED`；
   - 计数分别为成功 1、UNKNOWN 1、未尝试 1。

恢复结果由耐久 phase 中的逐商品状态生成；不会因为 Worker 异常而把已知写入事实
抹平，也不会把没有证据的副作用猜测为成功或失败。

## 本轮复核

执行命令：

`python -m pytest tests/test_task13_listing_contract.py::test_same_sku_write_lock_is_shared_across_all_write_actions tests/test_task13_listing_contract.py::test_post_publish_gate_only_allows_own_active_lock tests/test_shadowbot_task13_worker_recovery.py -q`

结果：`7 passed`。

这些测试随项目 pytest 测试集进入 CI；证据包校验器则由
[core-ci.yml](../../.github/workflows/core-ci.yml) 在 Windows 和 Linux 作业中分别执行。
