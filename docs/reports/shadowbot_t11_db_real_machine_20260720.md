# 任务11实机测试报告

**结论：本次实机测试通过。**

影刀在只读模式下读取 5 个目标，处理 5 个：成功 5 个，失败 0 个，跳过 0 个，人工复核 0 个。

## 运行信息

- 商品平台：蚂蚁花团供应商
- 执行模式：READ_ONLY（只读，不修改商品数据）
- 运行 ID：`ATTEMPT-T11-DB-REAL-20260720-005427`
- 任务 ID：`TASK-T11-DB-REAL-20260720-005427`
- 操作 ID：`READ-READ-BATCH-T11-DB-REAL-20260720-005427`
- 读取批次 ID：`READ-BATCH-T11-DB-REAL-20260720-005427`
- 影刀运行 ID：`filequeue:ATTEMPT-T11-DB-REAL-20260720-005427`

## 排序前后

- 排序规则：等级优先
- 排序前（上一轮验收范围）：卡布奇诺 B级 → 艾莎 B级
- 排序后（本次页面顺序）：卡布奇诺 B级 → 艾莎 B级 → 艾莎 C级 → 卡布奇诺 C级 → 艾莎 D级
- 本次实际读取顺序：卡布奇诺 B级 → 艾莎 B级 → 艾莎 C级 → 卡布奇诺 C级 → 艾莎 D级

## 逐商品读取结果与证据

### 1. 卡布奇诺 B级

- 页面位置：第 1 行（parent-index:1）
- SKU：`SKU-CAPPUCCINO-B`
- 结果：**读取成功：库存 1，价格 ¥24.00，ONLINE。**
- 逐商品证据：1 份，上传成功，哈希校验通过；证据 ID `EVD-READ-BATCH-T11-DB-REAL-20260720-005427-ITEM-CAPPUCCINO-B-DB-REAL-20260720-005427`

### 2. 艾莎 B级

- 页面位置：第 2 行（parent-index:17）
- SKU：`SKU-AISHA-B`
- 结果：**读取成功：库存 19，价格 ¥8.60，ONLINE。**
- 逐商品证据：1 份，上传成功，哈希校验通过；证据 ID `EVD-READ-BATCH-T11-DB-REAL-20260720-005427-ITEM-AISHA-B-DB-REAL-20260720-005427`

### 3. 艾莎 C级

- 页面位置：第 3 行（parent-index:33）
- SKU：`SKU-AISHA-C`
- 结果：**读取成功：库存 19，价格 ¥6.00，ONLINE。**
- 逐商品证据：1 份，上传成功，哈希校验通过；证据 ID `EVD-READ-BATCH-T11-DB-REAL-20260720-005427-ITEM-AISHA-C-DB-REAL-20260720-005427`

### 4. 卡布奇诺 C级

- 页面位置：第 4 行（parent-index:49）
- SKU：`SKU-CAPPUCCINO-C`
- 结果：**读取成功：库存 1，价格 ¥16.80，ONLINE。**
- 逐商品证据：1 份，上传成功，哈希校验通过；证据 ID `EVD-READ-BATCH-T11-DB-REAL-20260720-005427-ITEM-CAPPUCCINO-C-DB-REAL-20260720-005427`

### 5. 艾莎 D级

- 页面位置：第 5 行（parent-index:65）
- SKU：`SKU-AISHA-D`
- 结果：**读取成功：库存 6，价格 ¥5.00，ONLINE。**
- 逐商品证据：1 份，上传成功，哈希校验通过；证据 ID `EVD-READ-BATCH-T11-DB-REAL-20260720-005427-ITEM-AISHA-D-DB-REAL-20260720-005427`

## 计数恒等式

`total_count = processed_count`：5 = 5
`processed_count = success_count + failed_count + skipped_count + manual_check_count`：5 = 5 + 0 + 0 + 0
- 恒等式校验：**通过**

## 数据库回读

- 回读结果：**通过**
- execution_attempts：`READ_COMPLETED`，模式 `READ_ONLY`
- operation ledger：`PENDING`；任务记录：`running`
- execution_logs：1 条，成功标记：是
- 数据库记录的结果 ID：`RESULT-fd756359d86fc0ce2ebd2b4b`
- 请求哈希回读一致：是；结果哈希已记录：是
- 只读状态说明：READ_ONLY 只完成读取，不触发业务完成写回；数据库保留 attempt 和 execution log，operation/task 状态不作为业务成功的替代指标。

## 测试收尾与异常

测试结束后队列已正常停止，待处理目录均为空，未发现残留停止信号。业务副作用：否。
本次未发现未处理异常。底层验收校验：通过；中文编码校验：通过。
