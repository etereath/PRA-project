# Excel 表格字段说明

本文档说明当前阶段 Web 管理页和任务生成流程中使用的 3 份 Excel 主表。  
注意：Excel 文件中的第一行字段名保持英文不变，Web 页面会显示更易阅读的中文表头。

## 1. 商品主表 `products.xlsx`

用于维护统一商品主数据，是当前系统最核心的输入表。

| Excel 字段 | 中文说明 | 是否必填 | 示例 |
| --- | --- | --- | --- |
| `internal_sku` | 内部 SKU，系统内唯一商品编码 | 是 | `SKU-001` |
| `product_name` | 商品名称 | 是 | `红色月季A级` |
| `variety` | 品种 | 是 | `rose` |
| `grade` | 等级 | 是 | `A` |
| `stem_length` | 枝长或规格 | 是 | `60cm` |
| `unit` | 销售单位 | 是 | `bundle` |
| `base_cost` | 基础成本 | 是 | `10` |
| `current_stock` | 当前库存 | 是 | `50` |
| `sale_enabled` | 是否允许销售，支持 `true/false` | 是 | `true` |
| `last_price` | 上次售价，可为空 | 否 | `18` |
| `remark` | 备注 | 否 | `normal stock` |
| `feature_season` | 预留的季节特征，供后期 AI 建模使用 | 否 | `spring` |
| `feature_color` | 预留的颜色特征，供后期 AI 建模使用 | 否 | `red` |

### 使用建议

- `internal_sku` 必须唯一，不能重复。
- `base_cost`、`current_stock`、`last_price` 应填写数值。
- `sale_enabled=false` 时，系统会优先生成下架任务。

## 2. 价格规则表 `price_rules.xlsx`

用于配置商品如何从成本计算出目标价格。

| Excel 字段 | 中文说明 | 是否必填 | 示例 |
| --- | --- | --- | --- |
| `rule_id` | 规则 ID，建议唯一 | 是 | `RULE-ALL-1` |
| `rule_name` | 规则名称 | 是 | `全局固定加价` |
| `scope_type` | 作用范围类型 | 是 | `all` / `grade` / `variety` / `sku` / `platform` |
| `scope_value` | 作用范围值 | 是 | `*` / `A` / `rose` |
| `pricing_method` | 定价方式 | 是 | `fixed_markup` / `percentage_markup` |
| `markup_value` | 加价值 | 是 | `5` / `10` |
| `min_price` | 最低价限制，可为空 | 否 | `14` |
| `rounding_rule` | 取整规则 | 是 | `none` / `round` / `ceil` / `floor` / `step` |
| `rounding_step` | 取整步长，`step` 模式下建议填写 | 否 | `0.5` |
| `active` | 是否启用，支持 `true/false` | 是 | `true` |
| `priority` | 优先级，数字越小越先执行 | 是 | `10` |
| `remark` | 备注 | 否 | `A级单独加价` |

### 使用建议

- `scope_type=all` 时，`scope_value` 建议写 `*`。
- `pricing_method=percentage_markup` 时，`markup_value=10` 表示加价 10%。
- 多条规则会按 `priority` 从小到大依次计算。

## 3. 上下架规则表 `listing_rules.xlsx`

用于根据库存和销售开关决定是否上架或下架。

| Excel 字段 | 中文说明 | 是否必填 | 示例 |
| --- | --- | --- | --- |
| `rule_id` | 规则 ID，建议唯一 | 是 | `LIST-LOW` |
| `rule_name` | 规则名称 | 是 | `库存小于等于0下架` |
| `condition_type` | 条件类型 | 是 | `stock_lte` / `stock_gte` / `sale_disabled` |
| `condition_value` | 条件值，部分条件可为空 | 否 | `0` / `10` |
| `action` | 执行动作 | 是 | `set_online` / `set_offline` |
| `active` | 是否启用，支持 `true/false` | 是 | `true` |
| `priority` | 优先级，数字越小越先执行 | 是 | `1` |
| `remark` | 备注 | 否 | `恢复库存后允许上架` |

### 使用建议

- `condition_type=stock_lte` 常用于缺货下架。
- `condition_type=stock_gte` 常用于补货后恢复上架。
- `sale_enabled=false` 会在业务逻辑中优先触发强制下架。

## 4. Web 页面与 Excel 的关系

- Web 管理页表头显示中文，方便阅读和录入。
- 实际保存回 Excel 时，字段名仍然保持英文，不会改动第一行结构。
- 如果切换表格类型，页面会自动切换到对应默认文件路径。

## 5. 常见错误

### 表头不匹配

如果出现：

```text
invalid headers. Expected [...]
```

说明当前加载的 Excel 文件与所选表格类型不一致。  
例如：选择了 `listing_rules`，但实际路径还是 `products.xlsx`。

### 必填字段缺失

如果出现：

```text
row N: xxx is required
```

说明第 `N` 行缺少必填字段，需要补齐后再保存。

### 数值或布尔值格式错误

- 数值字段请填写纯数字或小数。
- 布尔字段建议填写 `true` 或 `false`。
