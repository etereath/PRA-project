# ShadowBot 自动登录只读验收记录

日期：2026-07-11  
对象：影刀 `test2`、PRA 文件队列、桌面微信小程序 `蚂蚁花团供应商`

## 结论

本次真实 `READ_ONLY` 任务证明：Worker 能从失效登录页识别账号密码状态，先切换到员工账号模式，再从本机凭据提供器完成单次账号密码提交；登录成功后继续同一执行尝试并读取商品价格。全程未产生改价副作用。

验证码人工接管、验证码超时和账号密码被拒绝分支已具备单元测试覆盖，但本次平台登录未要求手机验证码，因此尚未完成这些分支的真实平台验收。

## 实机任务

| 项目 | 值 |
|---|---|
| execution_attempt_id | `ATTEMPT-LOGIN-READ-20260711-170223` |
| execution_mode | `READ_ONLY` |
| 最终状态 | `READ_COMPLETED` |
| 实际价格 | `9.00` |
| side_effect_state | `NOT_STARTED` |
| business_operation_completed | `false` |
| 证据 | 已上传到共享目录，SHA-256 已核验 |

## 登录时间线

1. `17:02:26`：Worker 写入 `UI_STARTED`。
2. `17:02:51`：点击已捕获的 `登录页_员工按钮`，结果仅记录 `employee_mode_clicked=true`。
3. `17:02:58`：完成一次账号密码提交，结果仅记录 `account_password_submitted=true`。
4. `17:03:04`：检测到商品管理入口，记录 `login_completed_at` 并继续原 attempt。
5. `17:03:11`：读取到 `C级 / 艾莎` 当前价格 `9.00`，写出 `READ_COMPLETED`。

登录前第一次商品列表刷新失败是预期现象，错误为未找到商品管理入口；系统随后识别登录页并完成登录。登录成功后重新刷新、重新定位商品行并读取价格。

## 审计与安全核验

- 请求、phase、结果、证据均已归档，request/result checksum、instruction hash、request hash 和证据 hash 全部通过验收器校验。
- 归档请求不包含凭据字段；归档 phase 和 result 中仅有无敏感状态字段：员工模式已点击、账号密码已提交、登录完成时间和登录页文本标记。
- 账号、密码、`CredentialBlob` 均未写入请求、phase、结果、日志、SQLite 或证据。
- 结果中的 `account_password_submitted` 是布尔状态名称，不包含账号或密码值。

## 待完成实机验收

1. 平台出现手机验证码时，确认 Worker 写入 `LOGIN_VERIFICATION_REQUIRED`，PRA 创建唯一人工介入 review 并实际发送飞书通知。
2. 人工完成验证码后，确认同一 attempt 恢复并完成只读查询。
3. 以受控短超时验证 `LOGIN_VERIFICATION_TIMEOUT`。
4. 在非生产测试账号上验证 `LOGIN_CREDENTIALS_REJECTED`，不得自动二次提交。
5. 在验证码等待期间写入 `stop.signal`，确认返回 `WORKER_STOP_REQUESTED / NOT_STARTED`。
