# 微信测试号模板消息发送器测试 —— HTTP 全部走注入的 FakePost，不碰网络不碰库
import pytest

from app.sms import WechatSandboxSender


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
    # payload 形状：touser=openid、模板 id、文案进 content 字段
    url, payload = send_calls[0]
    assert "access_token=T1" in url
    assert payload["touser"] == "oABC123"
    assert payload["template_id"] == "tpl-test"
    assert payload["data"]["content"]["value"] == "告警A"


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
