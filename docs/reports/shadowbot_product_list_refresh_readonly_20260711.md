# ShadowBot 商品列表强制刷新 READ_ONLY 验收（2026-07-11）

## 目标

验证影刀 `test2` 在读取列表价格前，即使已经位于商品管理页，仍会点击“商品管理”入口触发平台重新拉取，并将刷新过程写入结果审计字段。

## 前提

- 已人工验证：重复点击“商品管理”会重新拉取平台商品数据。
- 本次只投递 `READ_ONLY`，不填写、确认或保存价格。
- Worker 心跳在投递前为 `RUNNING`，年龄约 1 秒，队列无活动请求。

## 实机结果

- `execution_attempt_id=ATTEMPT-REFRESH-READ-20260711-135356`
- `execution_mode=READ_ONLY`
- `status=READ_COMPLETED`
- `run_success_flag=true`
- `business_operation_completed=false`
- `side_effect_state=NOT_STARTED`
- `actual_price=9.00`

结果中的 `product_list_refreshes` 包含一条成功记录：

- `stage=BEFORE_PRICE_READ`
- `refresh_entry=蚂蚁_首页_商品管理_入口`
- `status=SUCCESS`
- `matched_product_name=艾莎`
- `matched_grade=C级`
- `matched_row_index=1`

共享证据已上传至 `\\LAPTOP-O9O76RQV\pra-evidence\ATTEMPT-REFRESH-READ-20260711-135356`，本地与共享 SHA-256 一致：`ae1ac9b1bb347847897faf38877a33060f7910f60a66214d353ca8177cda9222`。

## 验收

PRA Result Importer 已导入并归档请求、phase、结果和 checksum。自动验收报告全部通过：

- 请求、结果与数据库 hash 绑定一致。
- `READ_COMPLETED`、无业务完成、无平台副作用均符合预期。
- 共享证据存在、上传状态为 `SUCCESS`，且 hash 校验通过。
- `inbox/working/results` 无活动队列文件，未进入 quarantine。

## 结论

首次列表价格读取已确认使用强制刷新路径，且刷新审计、动态重定位、证据和文件队列归档均正常。提交后 `AFTER_SUBMIT_VERIFY` 刷新路径尚未在本轮只读验收中触发；它仍须在后续受控 COMMIT 或独立对账实机样本中验证。
