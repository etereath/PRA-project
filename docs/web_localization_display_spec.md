# Web 展示本地化与运营术语表

本文档记录 Web 后台和飞书通知的展示层规则。目标是让运营人员优先看到业务中文表达，同时保留必要的英文 ID 和枚举值用于排障。

## 1. 展示原则

- 普通运营页面优先使用业务中文，不在主说明中使用 `SQLite`、`runtime`、`schema`、`review_task`、`notification_log` 等开发者术语。
- 技术词、完整 ID、英文枚举值可以保留在详情页、系统维护页或文档中，方便开发和排障。
- 中文映射只影响页面和通知展示，不修改数据库中的英文枚举值。
- 如果代码中存在 `update_price`、`set_online`、`set_offline` 等英文值，只补展示映射，不改存储值。

## 2. 状态映射

| 英文值 | 中文展示 |
| --- | --- |
| `pending` | 待处理 |
| `running` | 执行中 |
| `success` | 已完成 / 发送成功 |
| `failed` | 失败 / 发送失败 |
| `skipped` | 已跳过 |
| `manual_review` | 等待人工确认 |
| `cancelled` | 已取消 |
| `expired` | 已过期 |
| `approved` | 已通过 |
| `rejected` | 已拒绝 |
| `adjusted` | 已调整 |

## 3. 任务与复核类型映射

| 英文值 | 中文展示 |
| --- | --- |
| `update_price` | 改价 |
| `set_online` | 上架 |
| `set_offline` | 下架 |
| `sync_status` | 同步状态 |
| `capacity_warning` | 产能预警 |
| `labor_required` | 临时工确认 |
| `manual_price_review` | 人工价格复核 |
| `below_break_even_review` | 低于保本价复核 |
| `shortage_warning` | 短缺预警 |
| `cold_storage_warning` | 冷库预警 |
| `clearance_warning` | 清库存预警 |
| `manual_review` | 人工复核 |

## 4. 价格规则映射

| 英文值 | 中文展示 |
| --- | --- |
| `fixed_markup` | 固定改价 |
| `percentage_markup` | 百分比改价 |
| `none` | 不取整 |
| `round` | 四舍五入到整数 |
| `ceil` | 向上取整 |
| `floor` | 向下取整 |
| `step` | 按步长向上取整 |

## 5. 范围、通知与接收人映射

| 英文值 | 中文展示 |
| --- | --- |
| `all` | 全部商品 |
| `grade` | 按等级 |
| `variety` | 按品种 |
| `product_name` | 按品种 |
| `global` | 全局事项 |
| `forecast_group` | 预测分组 |
| `sku` | 单个商品 |
| `platform` | 单个平台 |
| `task` | 单个任务 |
| `mock` | 模拟通知 |
| `feishu` | 飞书 |
| `role` | 角色 |
| `system` | 系统 |

## 6. 时间展示

- 页面和飞书通知中的时间统一展示为 `YYYY-MM-DD HH:mm`。
- timezone-aware datetime 转为 UTC+8 / Asia/Shanghai 后展示。
- naive datetime 按当前系统既有语义作为本地业务时间展示，避免二次加 8 小时。
- 不改变数据库存储逻辑，不迁移历史数据。

## 7. 安全展示

- 页面和日志展示不得出现 `token=`、raw token、完整 webhook、secret 或完整 mobile review URL。
- `notification_logs.message` 仍只保存简短摘要，不保存完整处理链接。
- Web 后台运行态页面需要登录；旧路由保留直接访问能力，但不作为主导航入口。

## 8. 业务输入表单展示

- 商品资料与库存录入页面面向运营人员，主文案使用“商品资料”“录入库存”“补充库存”“本次入库数量”等业务词，不把“Excel 行列”作为主要表达。
- 品种录入使用“选择已有品种 + 新增品种弹窗”。“品种代码”只在新增品种时出现，不作为主表单长期字段。
- 字段标题需要明显、加粗，输入框、选择框和操作按钮需要统一高度与对齐，双栏布局应按输入框 Y 轴对齐，而不是简单移动整列。
- `sale_enabled` 展示为“是 / 否”，但底层值仍保持现有可读格式，不改变 Excel 列名或任务生成逻辑。
- 价格规则管理页面面向运营人员，主文案使用“价格规则”“适用范围”“价格类型”“最低价”“取整规则”等业务词，不把 Excel 字段名作为第一视觉重点。
- 价格规则表单只写入当前 `price_rules.xlsx` 已有字段，不把中文展示值直接写入 `pricing_method / rounding_rule / variety_filter / grade_filter / platform_filter` 等任务生成需要的标准值。
- 价格规则范围展示可以使用“全部品种 / 全部等级 / 全部平台”等中文文案，但 Excel 中通配符仍保存为 `*`；旧 `scope_type / scope_value` 已不再作为价格规则展示和写入主路径。
- 旧 `/tables` 是高级兼容入口，普通运营页面不应把在线 Excel 表格编辑作为主要日常路径。
