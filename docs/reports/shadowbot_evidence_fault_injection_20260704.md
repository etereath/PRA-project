# ShadowBot 证据故障注入报告

## 1. 结论

2026-07-04 已完成证据共享目录不可用和共享证据 hash 不一致两项实机故障注入，结果均符合安全预期。

测试全程使用 `READ_ONLY`，未打开价格弹窗、未修改输入框、未点击提交按钮，平台副作用状态始终为 `NOT_STARTED`。

## 2. 共享目录不可用

| 项目 | 结果 |
| --- | --- |
| attempt | `ATTEMPT-FI-EVIDENCE-UNWRITABLE-20260704-001` |
| 注入方式 | 将普通文件 `D:\PRA_Runtime\shadowbot_evidence_blocker` 作为 `evidence_share_dir` |
| status | `FAILED` |
| current_step | `CAPTURE_EVIDENCE` |
| error_code | `EVIDENCE_UPLOAD_FAILED` |
| side_effect_state | `NOT_STARTED` |
| retryable | `true` |
| 页面实际价格 | `11.00` |

Worker 成功读取并核对 C级艾莎，但证据复制阶段因目标不是目录而失败。结果由独立故障注入数据库正常导入归档，没有进入平台副作用区。测试 blocker 已删除。

## 3. 共享证据 hash 不一致

| 项目 | 结果 |
| --- | --- |
| attempt | `ATTEMPT-FI-EVIDENCE-HASH-20260704-001` |
| 初始状态 | `READ_COMPLETED / ok=true` |
| 注入方式 | 对该 attempt 的共享 PNG 追加测试标记，不修改本地原图和归档 result |
| result 记录哈希 | `9f09320886884ee918e04cc7f10f19208aeb881c11f17cd22bd2a1f3f7a004c8` |
| 篡改后共享文件哈希 | `fc9d25a6391cca5126afae17e89144c355b671761eeed920d6dda52d55b4ea15` |
| 验收器结果 | `ok=false`，退出码 1 |
| 唯一失败项 | `evidence_1_storage_hash` |

PRA 验收器没有因为数据库状态为 `READ_COMPLETED` 而接受被替换的证据。恢复本地原始截图后，共享哈希重新变为 `9f0932...`，验收器恢复为 `ok=true / failed_checks=[]`。

## 4. 实现修正

`vertical_slice_read_price.py` 的证据复制结果现在保留独立错误码：

- 复制或目录写入失败：`EVIDENCE_UPLOAD_FAILED`
- 源文件与存储文件哈希不一致：`EVIDENCE_HASH_MISMATCH`

增加了仅供测试代码路径使用的确定性 hash mismatch 注入点；生产 `ShadowBotExecutor` 仍拒绝所有非空 `fault_injection`。证据测试改为读取仓库中的影刀规范副本，不再依赖固定用户目录。

## 5. 恢复状态

- blocker 文件已删除。
- 被篡改的专用测试截图已从本地原图恢复。
- 共享证据恢复后验收再次通过。
- Worker 可继续用于下一组只读故障注入。

