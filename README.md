# PRA MVP 项目说明

这是一个面向鲜花多平台销售自动化场景的后端 MVP。当前阶段聚焦“平台无关的业务核心”，已经打通以下链路：

- Excel 主数据导入与校验
- 价格规则与上下架规则计算
- AI 定价建议接口预留
- 任务预览、任务生成与 Excel 导出
- Web 管理页与表格编辑页
- 模拟执行与执行日志回写

## 文档入口

项目说明文本以中文为主，相关规范见 [项目注意事项.md](/D:/PRA%20project/项目注意事项.md)。

- 字段说明文档：[Excel表格字段说明.md](/D:/PRA%20project/doc/Excel表格字段说明.md)
- 运行与排错手册：[运行与排错手册.md](/D:/PRA%20project/doc/运行与排错手册.md)
- 阶段验收清单：[阶段验收清单.md](/D:/PRA%20project/doc/阶段验收清单.md)
- 项目背景与设计说明：[project_overview.md](/D:/PRA%20project/doc/project_overview.md)

## 快速开始

安装依赖：

```bash
pip install -e .
```

生成模板与样例工作簿：

```bash
python scripts/create_sample_workbooks.py
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

启动 Web 管理页：

```bash
python -m app.cli serve-web --host 127.0.0.1 --port 8765
```

Windows 一键启动：

```bat
start_web.bat
```

## CLI 命令

- `templates`：创建空模板工作簿
- `validate`：校验输入工作簿
- `import-data`：校验并输出输入数据摘要
- `preview-tasks`：预览任务但不写出文件
- `generate-tasks`：生成任务工作簿
- `mock-ai-decision`：预览单个 SKU 的 Mock AI 定价决策
- `simulate-execution`：模拟执行任务并写出执行日志
- `serve-web`：启动简易 Web 管理页

## Web 页面

当前内置 3 个页面：

1. `任务面板`：校验数据、预览任务、确认导出任务
2. `Excel 表格管理`：直接编辑 `products`、`price_rules`、`listing_rules`
3. `执行回写`：读取任务文件，模拟执行并写出执行日志

## 当前阶段边界

当前版本仍属于 MVP，暂不包含：

- 真实平台登录与页面操作
- 真正的 RPA 执行器接入
- AI 模型训练与在线预测闭环
- 数据库持久化与完整权限系统

当前建议把它作为“规则验证、任务生成、流程演示”的基础版本继续推进。
