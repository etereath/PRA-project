# Excel 表格字段说明

本文档说明当前阶段 Web 管理页、CLI 和任务生成流程使用的 Excel 表格。

注意：Excel 第一行字段名必须保持英文不变；Web 页面会把字段显示为中文，但不会改变 Excel 表头结构。

## 1. 商品主表 `products.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `internal_sku` | 内部 SKU | 是 | 系统内唯一商品编码。 |
| `product_name` | 商品名称/品种 | 是 | 当前项目只关注玫瑰，可作为业务上的品种字段使用。 |
| `grade` | 等级 | 是 | 如 `A`、`B`、`C`。 |
| `stem_length` | 枝长/规格 | 是 | 如 `60`、`70`。 |
| `unit` | 单位 | 是 | 如 `扎`。 |
| `base_cost` | 基础成本 | 是 | 用于保本价、最低价和规则定价。 |
| `current_stock` | 当前库存 | 否 | 为空时按 `0` 处理。 |
| `sale_enabled` | 允许销售 | 是 | `false` 是最高优先级禁售信号。 |
| `last_price` | 上次售价 | 否 | 用于限制自动降价幅度。 |
| `recommended_price` | 推荐价格 | 否 | 兼容字段；后续优先使用 `price_forecasts.xlsx` 的推荐价。 |
| `remark` | 备注 | 否 | 人工说明。 |
| `feature_season` | 季节特征 | 否 | 为后续 AI/预测特征预留。 |
| `feature_color` | 颜色特征 | 否 | 为后续 AI/预测特征预留。 |

## 2. 价格规则表 `price_rules.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | 规则 ID | 是 | 价格规则唯一标识。 |
| `rule_name` | 规则名称 | 是 | 便于人工查看。 |
| `scope_type` | 作用范围类型 | 是 | 支持 `all`、`grade`、`product_name`、`product`、`sku`、`platform`。 |
| `scope_value` | 作用范围值 | 是 | 如 `*`、`A`、某个 SKU。 |
| `pricing_method` | 定价方式 | 是 | `fixed_markup` 或 `percentage_markup`。 |
| `markup_value` | 加价值 | 否 | 固定加价金额或百分比数值。 |
| `min_price` | 最低价 | 否 | 当前规则层最低价限制。 |
| `rounding_rule` | 取整规则 | 是 | `none`、`round`、`ceil`、`floor`、`step`。 |
| `rounding_step` | 取整步长 | 否 | `rounding_rule=step` 时使用。 |
| `active` | 是否启用 | 是 | `true` / `false`。 |
| `priority` | 优先级 | 是 | 数字越小越先执行。 |
| `remark` | 备注 | 否 | 人工说明。 |

## 3. 上下架规则表 `listing_rules.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | 规则 ID | 是 | 上下架规则唯一标识。 |
| `rule_name` | 规则名称 | 是 | 便于人工查看。 |
| `condition_type` | 条件类型 | 是 | `stock_lte`、`stock_gte`、`sale_disabled`、`time_gte`。 |
| `condition_value` | 条件值 | 视条件而定 | 库存阈值或 `HH:MM` 时间。 |
| `action` | 执行动作 | 是 | `set_online` 或 `set_offline`。 |
| `active` | 是否启用 | 是 | `true` / `false`。 |
| `priority` | 优先级 | 是 | 数字越小越先执行。 |
| `remark` | 备注 | 否 | 人工说明。 |

## 4. 产量预测表 `harvest_forecasts.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `forecast_id` | 预测 ID | 是 | 产量预测记录唯一标识。 |
| `forecast_date` | 预测生成日期 | 是 | 预测生成的日期，格式 `YYYY-MM-DD`。 |
| `target_trade_date` | 目标交易日 | 是 | 该预测服务的交易日。 |
| `variety` | 品种 | 是 | 当前与 `products.product_name` 匹配。 |
| `grade` | 等级 | 是 | 与商品等级匹配。 |
| `predicted_harvest_qty` | 预测采收量 | 是 | 单位为扎。 |
| `lower_bound_qty` | 预测数量下界 | 否 | 预测区间下界。 |
| `upper_bound_qty` | 预测数量上界 | 否 | 预测区间上界。 |
| `confidence` | 置信度 | 否 | 0 到 1 的数值。 |
| `source` | 来源 | 否 | 如 `manual`、`ai_model`、`market_data`。 |
| `generated_at` | 生成时间 | 否 | ISO 时间，如 `2026-05-03T16:00:00`。 |
| `note` | 说明 | 否 | 人工说明。 |

## 5. 价格预测表 `price_forecasts.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `forecast_id` | 预测 ID | 是 | 价格预测记录唯一标识。 |
| `forecast_date` | 预测生成日期 | 是 | 预测生成的日期，格式 `YYYY-MM-DD`。 |
| `target_trade_date` | 目标交易日 | 是 | 该预测服务的交易日。 |
| `variety` | 品种 | 是 | 当前与 `products.product_name` 匹配。 |
| `grade` | 等级 | 是 | 与商品等级匹配。 |
| `recommended_price` | 推荐价格 | 是 | 单位为元/扎，是预测基准价，不是最终锁价。 |
| `lower_bound_price` | 预测价格下界 | 否 | 预测区间下界。 |
| `upper_bound_price` | 预测价格上界 | 否 | 预测区间上界。 |
| `confidence` | 置信度 | 否 | 0 到 1 的数值。 |
| `source` | 来源 | 否 | 如 `manual`、`rule_based`、`ai_model`、`market_data`、`hybrid`。 |
| `generated_at` | 生成时间 | 否 | ISO 时间。 |
| `note` | 说明 | 否 | 人工说明。 |

## 6. 包装产能计划表 `capacity_plans.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `trade_date` | 交易日 | 是 | 产能计划对应的交易日。 |
| `normal_packing_capacity_qty` | 正常包装产能 | 否 | 默认 250 扎/天。 |
| `temp_worker_capacity_qty` | 每名临时工产能 | 否 | 默认 100 扎/天。 |
| `confirmed_temp_worker_count` | 已确认临时工人数 | 否 | 未确认时填 0 或留空。 |
| `allocation_rule` | 产能分配规则 | 否 | 当前默认 `proportional_by_forecast`。 |
| `note` | 说明 | 否 | 人工说明。 |

## 7. 冷库状态表 `cold_storage_status.xlsx`

| Excel 字段 | 中文名 | 必填 | 说明 |
| --- | --- | --- | --- |
| `trade_date` | 交易日 | 是 | 冷库状态对应的交易日。 |
| `cold_storage_total_capacity_qty` | 冷库总容量 | 否 | 默认 500 扎，全场共享。 |
| `cold_storage_current_qty` | 冷库已用容量 | 否 | 当前已占用冷库数量。 |
| `note` | 说明 | 否 | 人工说明。 |

## 8. 常见错误

1. `invalid headers`：表头与系统预期不一致，常见原因是把错误的 Excel 文件路径填到了当前表格类型里。
2. `row N: xxx is required`：第 `N` 行缺少必填字段。
3. `condition_value must be HH:MM`：`condition_type=time_gte` 时，时间格式不正确。
4. `请输入 YYYY-MM-DD 日期`：日期列需要填写 `2026-05-04` 这样的格式。
