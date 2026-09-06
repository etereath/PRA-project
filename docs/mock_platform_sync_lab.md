# Mock 平台同步实验室

本文档说明当前项目新增的 Mock Platform Sync Lab。它是本地测试台，用于验证“运行态任务 -> 模拟平台执行 -> 执行日志 -> 平台状态同步评估 -> 人工复核”的闭环。

Mock 平台同步实验室不是系统接入的真实销售平台，也不是 RPA 执行器。它不会登录任何外部平台，不会产生真实订单，不会修改真实销售数据。

## 1. 核心定位

Mock 平台同步实验室负责：

- 保存一份独立的模拟平台状态。
- 模拟执行 `update_price / set_online / set_offline / sync_status` 运行态任务。
- 将模拟执行结果写入 `execution_logs`。
- 通过 `PlatformSyncEvaluator` 对比 PRA 期望状态与模拟平台实际状态。
- 当发现价格、上下架或平台库存差异时，生成人工复核 proposal。

Mock 平台同步实验室不负责：

- 不连接真实平台。
- 不接真实 RPA。
- 不生成真实订单。
- 不把平台库存写回 PRA 公共库存。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不直接发送飞书。
- 不自动修复平台差异。

## 2. 独立 Mock 平台数据库

Mock 平台状态默认保存到：

```text
data/runtime/mock_platform.sqlite3
```

该数据库独立于运行态数据库 `data/runtime/pra_runtime.sqlite3`，用于避免测试平台状态污染正式运行态事实。

当前表：

```text
mock_platform_products
```

主要字段：

- `platform_name`：模拟平台名称。
- `internal_sku`：PRA 内部 SKU。
- `platform_sku`：模拟平台商品 SKU。
- `product_name`：商品名称/品种。
- `grade`：等级。
- `platform_price`：模拟平台价格。
- `platform_online_status`：模拟平台上下架状态，当前为 `online / offline`。
- `platform_stock_qty`：模拟平台库存。
- `last_synced_at`：最后同步时间。
- `last_platform_update_at`：最后平台更新时间。
- `last_error`：最后错误摘要。

## 3. PRA 期望状态与平台实际状态

本测试台明确区分两类数据：

- PRA 期望状态：来自运行态 `tasks`，例如目标价格、目标上下架状态。
- 平台实际状态：来自 `mock_platform.sqlite3` 中的 `mock_platform_products`。

`platform_stock_qty` 仅表示模拟平台侧库存状态。它用于发现平台状态异常或同步差异，不会反向覆盖 `products.xlsx` 中的公共库存，也不会改写 PRA 的库存输入。

## 4. Mock Platform Executor

新增 CLI：

```powershell
python scripts/run_mock_platform_executor.py --dry-run
python scripts/run_mock_platform_executor.py --apply
```

常用参数：

```powershell
python scripts/run_mock_platform_executor.py --init
python scripts/run_mock_platform_executor.py --reset-sample
python scripts/run_mock_platform_executor.py --platform default_platform
python scripts/run_mock_platform_executor.py --task-id TASK_ID
python scripts/run_mock_platform_executor.py --runtime-db data/runtime/pra_runtime.sqlite3
python scripts/run_mock_platform_executor.py --mock-platform-db data/runtime/mock_platform.sqlite3
```

默认行为是 `dry-run`。

### dry-run

`dry-run` 只预览将要执行的运行态任务，不写入：

- `tasks`
- `execution_logs`
- `mock_platform_products`

### apply

`apply` 会：

- 读取待执行的运行态任务。
- 通过 `RuntimeTaskService` 将任务从 `pending` 推动到 `running`，再根据执行结果写为 `success / failed`。
- 修改 Mock 平台状态。
- 写入 `execution_logs`。

执行器不直接改 `tasks` 表，不直接发飞书，也不创建人工复核。

### 模拟失败规则

当前第一版支持以下测试场景：

- 平台商品不存在：执行失败。
- `update_price` 的目标价低于 Mock 平台最低允许价格：执行失败。
- 其他异常写入 `execution_logs.error_message`。

## 5. PlatformSyncEvaluator

新增 evaluator：

```text
platform_sync
```

它用于对比 PRA 期望状态与 Mock 平台实际状态。

运行示例：

```powershell
python scripts/evaluate_business_rules.py --evaluator platform_sync --trade-date 2026-05-05 --dry-run
python scripts/evaluate_business_rules.py --evaluator platform_sync --trade-date 2026-05-05 --apply
```

可指定 Mock 平台数据库：

```powershell
python scripts/evaluate_business_rules.py --evaluator platform_sync --trade-date 2026-05-05 --dry-run --mock-platform-db data/runtime/mock_platform.sqlite3
```

### 差异类型

当前会生成以下复核 proposal：

- `price_mismatch`：PRA 目标价与 Mock 平台价格不一致。
- `listing_status_mismatch`：PRA 目标上下架状态与 Mock 平台状态不一致。
- `stock_mismatch`：Mock 平台库存为 0 或负数。
- `platform_sync_warning`：平台商品缺失或其他同步警告。

### 处理边界

`PlatformSyncEvaluator` 只生成 review proposal，不自动改价、不自动上下架、不自动修复 Mock 平台状态。

`apply` 时仍通过现有自动规则 runner 和运行态服务落成结果：

- 先生成 source task。
- 再通过 `ReviewTaskService.create_from_tasks()` 生成 `review_tasks`。
- pending review 触发 `notification_logs`。

重复 `apply` 会基于 `dedupe_key` 跳过，不重复生成同一复核。

## 6. Web 入口

任务中心新增只读分页：

```text
/tasks?task_tab=mock_platform
```

该页用于查看 Mock 平台测试状态，字段包括：

- 平台名称
- 内部 SKU
- 平台 SKU
- 商品名称/品种
- 等级
- 平台价格
- 平台上下架状态
- 平台库存
- 最后同步时间
- 最后平台更新时间
- 最后错误

Web 第一版不提供执行按钮。执行测试请通过 CLI 完成。

## 7. 推荐测试场景

建议保留以下可重复测试：

1. `update_price` 成功，Mock 平台价格更新，任务写入 `success` 和 `execution_logs`。
2. `set_online` 成功，Mock 平台状态变为 `online`。
3. `set_offline` 成功，Mock 平台状态变为 `offline`。
4. 平台商品不存在，任务执行失败并写入执行日志。
5. 非法低价执行失败，Mock 平台价格不变。
6. 外部手动修改 Mock 平台价格，`PlatformSyncEvaluator` 生成 `price_mismatch`。
7. 外部手动修改 Mock 平台上下架状态，生成 `listing_status_mismatch`。
8. Mock 平台库存为 0，生成 `stock_mismatch`。
9. 重复 apply 不重复生成相同复核。

## 8. 安全与边界

Mock 平台同步实验室必须遵守：

- 不展示 secret、raw token、`token=`、完整 webhook、完整 mobile review URL。
- 不把 Mock 平台库存写回 `products.xlsx`。
- 不绕过 `RuntimeTaskService / ReviewTaskService / NotificationSender`。
- 不直接发送飞书。
- 不接真实平台或真实 RPA。
- 不引入 Celery / Prefect。

如果后续接入真实平台适配器，应单独规划平台 API/RPA 边界，不应复用 Mock 平台数据库作为真实平台状态来源。
