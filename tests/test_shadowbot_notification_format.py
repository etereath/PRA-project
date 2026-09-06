from app.enums import NotificationSendStatus
from app.models import NotificationLog
from app.services.runtime import _build_feishu_review_notification_post_body


def test_shadowbot_login_handoff_post_uses_specialized_fields():
    log = NotificationLog(
        notification_id="N-shadowbot-login",
        related_task_id="TASK-1",
        related_review_task_id="LOGIN-VERIFY-1",
        recipient_type="role",
        recipient="operations",
        channel="feishu",
        sent_at=None,
        send_status=NotificationSendStatus.PENDING.value,
        dedupe_key="shadowbot-login|1",
        message="ShadowBot 登录验证码人工接管",
    )
    body = _build_feishu_review_notification_post_body(
        log,
        {
            "notification_kind": "shadowbot_login_verification",
            "title": "ShadowBot 登录验证码人工接管",
            "platform_name": "蚂蚁花团供应商",
            "execution_attempt_id": "ATTEMPT-LOGIN-1",
            "required_by": "2026-07-12T12:00+08:00",
            "action": "请在已打开的桌面端微信小程序中完成手机验证码；完成后 Worker 将继续原任务。",
        },
    )

    post = body["content"]["post"]["zh_cn"]
    lines = [part["text"] for row in post["content"] for part in row]
    assert post["title"] == "ShadowBot 登录验证码人工接管"
    assert "平台：蚂蚁花团供应商" in lines
    assert "执行尝试：ATTEMPT-LOGIN-1" in lines
    assert "截止时间：2026-07-12T12:00+08:00" in lines
    assert not any(line.startswith("业务日期：") for line in lines)
    assert not any(line.startswith("处理对象：") for line in lines)
    assert not any(line.startswith("原因：") for line in lines)
