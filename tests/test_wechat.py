# 微信测试号模板消息发送器测试 —— HTTP 全部走注入的 FakePost，不碰网络不碰库
import pytest

from app.sms import WechatSandboxSender, wechat_fields


class FakePost:
    """按序吐出预设响应，记录全部调用（url, payload）"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.responses.pop(0)


def make_sender(fake, openid="oABC123"):
    """构造已注入 fake HTTP 的发送器；openid 查表固定返回（真实实现查 receivers 表）"""
    import os
    os.environ.update(WECHAT_APP_ID="wx-test", WECHAT_APP_SECRET="secret-test",
                      WECHAT_TEMPLATE_ID="tpl-test")
    s = WechatSandboxSender(post=fake)
    s._openid_for = lambda phone: openid
    return s


# ---------- 字段拆分（测试号每行约 20 字上限，长文案必须拆多行短字段） ----------

def test_wechat_fields_violation_split_short_lines():
    alarm = {"device_id": "Di-Jiu-Shui-Chang-1", "param_key": "Vf", "type": "low",
             "value": 19.66, "threshold": 25}
    fields = wechat_fields(alarm, "原始文案不参与", "10-14 12:00")
    assert [fields[f"keyword{i}"]["value"] for i in (1, 2, 3, 4)] == [
        "九厂一期-1#", "沉降比 19.66%", "低于下限 25%", "10-14 12:00"]
    for f in fields.values():
        assert len(f["value"]) <= 16  # 模板里每行还有 3 字标签，标签+值 ≤19 留余量

def test_wechat_fields_offline_split():
    alarm = {"device_id": "Di-Jiu-Shui-Chang-3", "param_key": None, "type": "offline",
             "value": None, "threshold": None}
    fields = wechat_fields(alarm, "x", "10-14 12:00")
    assert [fields[f"keyword{i}"]["value"] for i in (1, 2, 3)] == [
        "九厂一期-3#", "设备离线", "超过13分钟"]

def test_wechat_fields_fallback_and_clip():
    """没有告警结构（如直发测试）时文案兜底；超长行防御性截断到 ≤16"""
    fields = wechat_fields(None, "渠道验证：这是一条很长的测试文案用于验证截断逻辑是否生效", "10-14 12:00")
    assert fields["keyword2"]["value"].startswith("渠道验证")
    assert len(fields["keyword2"]["value"]) <= 16
    assert fields["keyword4"]["value"] == "10-14 12:00"


def test_wechat_send_ok_and_token_cached():
    """两次发送只取一次 token（2h 过期，缓存命中不再请求）"""
    fake = FakePost([
        {"access_token": "T1", "expires_in": 7200},   # 第一次 send 前取 token
        {"errcode": 0, "errmsg": "ok"},               # 发送 1
        {"errcode": 0, "errmsg": "ok"},               # 发送 2（token 走缓存）
    ])
    s = make_sender(fake)
    assert s.send("13800000001", "告警A") is True
    assert s.send("13800000001", "告警B") is True
    token_calls = [c for c in fake.calls if "cgi-bin/token" in c[0]]
    send_calls = [c for c in fake.calls if "template/send" in c[0]]
    assert len(token_calls) == 1 and len(send_calls) == 2
    # payload 形状：touser=openid、模板 id、四行短字段（测试号每行约 20 字上限）
    url, payload = send_calls[0]
    assert "access_token=T1" in url
    assert payload["touser"] == "oABC123"
    assert payload["template_id"] == "tpl-test"
    assert set(payload["data"]) == {"keyword1", "keyword2", "keyword3", "keyword4"}


def test_wechat_expired_token_refresh_and_retry_once():
    """token 失效（40001/42001）→ 重取一次再发；第二次成功算成功"""
    fake = FakePost([
        {"access_token": "T1", "expires_in": 7200},   # 取 token1
        {"errcode": 42001, "errmsg": "access_token expired"},  # 用旧 token 发 → 过期
        {"access_token": "T2", "expires_in": 7200},   # 强制重取 token2
        {"errcode": 0, "errmsg": "ok"},               # 重发成功
    ])
    s = make_sender(fake)
    assert s.send("13800000001", "告警A") is True
    token_calls = [c for c in fake.calls if "cgi-bin/token" in c[0]]
    assert len(token_calls) == 2  # 缓存被作废，确实重取了


def test_wechat_send_api_error_returns_false():
    """业务错误（如 43004 未关注）→ 不重试、返回 False"""
    fake = FakePost([
        {"access_token": "T1", "expires_in": 7200},
        {"errcode": 43004, "errmsg": "require subscribe"},
    ])
    s = make_sender(fake)
    assert s.send("13800000001", "告警A") is False
    assert len(fake.calls) == 2  # 没有额外的 token/发送调用


def test_wechat_missing_openid_skips_http():
    """接收人没配 openid：不发 HTTP、记日志、该人判失败（False）"""
    fake = FakePost([])
    s = make_sender(fake, openid=None)
    assert s.send("13800000001", "告警A") is False
    assert fake.calls == []


def test_wechat_token_get_failure_raises_to_failed():
    """appsecret 错等原因拿不到 token：异常不能炸掉检测线程——send 捕获返回 False"""
    fake = FakePost([{"errcode": 40125, "errmsg": "invalid appsecret"}])
    s = make_sender(fake)
    assert s.send("13800000001", "告警A") is False


def test_wechat_requires_env_config():
    """缺 .env 配置直接实例化 → 明确报缺哪些变量"""
    import os
    saved = {k: os.environ.pop(k, None) for k in
             ("WECHAT_APP_ID", "WECHAT_APP_SECRET", "WECHAT_TEMPLATE_ID")}
    try:
        with pytest.raises(RuntimeError, match="WECHAT_APP_ID"):
            WechatSandboxSender(post=FakePost([]))
    finally:
        os.environ.update({k: v for k, v in saved.items() if v})
