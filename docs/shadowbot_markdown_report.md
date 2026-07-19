# ShadowBot 人工可读 Markdown 报告模块

`app.services.shadowbot_markdown_report` 以验收 JSON 为唯一事实来源，生成 UTF-8 编码的人工可读报告。模块只读输入 JSON，不读取或修改队列，也不执行任何业务操作。

## 命令行用法

任务11的实机报告使用数据库登记批次时，用下面的构建命令；它会同时生成验收 JSON 和 Markdown：

```powershell
python scripts/build_task11_human_report_payload.py `
  --archive-dir D:\PRA_Runtime\shadowbot_queue\archive\ATTEMPT-T11-DB-REAL-20260720-005427 `
  --runtime-db D:\PRA_Runtime\task11_db_real_20260720-005427.sqlite3 `
  --sort-acceptance D:\PRA_Runtime\shadowbot_queue\archive\T11_COVERAGE_ACCEPTANCE_20260719.json `
  --output-json D:\PRA_Runtime\shadowbot_queue\archive\T11_DB_REAL_HUMAN_REPORT_20260720.json `
  --output-markdown docs\reports\shadowbot_t11_db_real_machine_20260720.md
```

生成器会：

- 先用自然语言给出本次实机测试是否通过；
- 按商品逐项列出商品名称、等级、读取结果、库存、价格和上架状态；
- 展示排序前后、任务/运行/操作/读取批次 ID；
- 为每个商品列出页面位置和逐商品证据 ID、上传状态、哈希校验；
- 展示数据库回读的 attempt、execution log、结果 ID 和请求哈希一致性；
- 展示 `total=processed` 以及 `processed=success+failed+skipped+manual_check` 计数恒等式；
- 用一句话说明队列是否正常收尾、是否产生业务副作用；
- 以 UTF-8 写入并支持 UTF-8-SIG 输入 JSON；
- 在测试中回读报告，检查替换字符和意外问号。

对应测试位于 `tests/test_shadowbot_markdown_report.py`。
