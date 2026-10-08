# 判定/告警/短信核心逻辑测试 —— 用例语义移植自前端 sc-front 的 alarmEngine.spec.ts / statusRule，
# 移植时语义必须逐条对齐（冷却边界、边沿触发、严格比较等都有前端测试钉死）。
from datetime import datetime, timedelta

import pytest

from anomaly_detector import judge_device, plan_alarms, cooldown_key, OFFLINE_AFTER_SECONDS
from sms import build_sms_text, settle_sms


NOW = datetime(2026, 10, 8, 12, 0, 0)
NOW_MS = int(NOW.timestamp() * 1000)


def reading(age_seconds=0, **params):
    """构造一条读数，age_seconds 表示距今多久上报"""
    cols = {"Vf": "vf", "Sf": "sf", "Fc": "fc", "pHf": "phf", "Tf": "tf", "Cf": "cf"}
    return {"reported_at": NOW - timedelta(seconds=age_seconds),
            **{cols[k]: v for k, v in params.items()}}


def rule(param_key, low, high, enabled=True):
    return {"param_key": param_key, "low": low, "high": high, "enabled": enabled}


# ---------- 越限判定 ----------

def test_equal_boundary_is_not_violation():
    """等于边界不算越限（前端注释明确，严格 > / <）"""
    judged = judge_device(reading(Vf=35), [rule("Vf", 5, 35)], NOW)
    assert judged["status"] == "online"
    assert judged["violations"] == []

def test_above_high_and_below_low():
    judged = judge_device(reading(Vf=36.2), [rule("Vf", 5, 35)], NOW)
    assert judged["status"] == "abnormal"
    assert judged["violations"] == [{"param_key": "Vf", "value": 36.2, "type": "high", "threshold": 35}]
    judged = judge_device(reading(Vf=4.9), [rule("Vf", 5, 35)], NOW)
    assert judged["violations"][0]["type"] == "low"
    assert judged["violations"][0]["threshold"] == 5

def test_disabled_rule_skipped_and_missing_param_skipped():
    judged = judge_device(
        reading(Vf=999),
        [rule("Vf", 5, 35, enabled=False), rule("Fc", 800, 1500)],  # Fc 缺失(None)也要跳过
        NOW,
    )
    assert judged["status"] == "online"

# ---------- 离线判定 ----------

def test_offline_strict_age_boundary():
    """恰好 13 分钟不算离线，超过才算（严格 >）；无数据也算离线"""
    rules = []
    assert judge_device(reading(age_seconds=OFFLINE_AFTER_SECONDS), rules, NOW)["status"] == "online"
    judged = judge_device(reading(age_seconds=OFFLINE_AFTER_SECONDS + 0.1), rules, NOW)
    assert judged["status"] == "offline"
    assert judge_device(None, rules, NOW)["status"] == "offline"

# ---------- 告警生成与等级 ----------

def test_violation_alarm_is_warning():
    judged = judge_device(reading(Vf=36.2), [rule("Vf", 5, 35)], NOW)
    alarms, seq = plan_alarms("Di-Jiu-Shui-Chang-1", "online", judged, {}, NOW_MS, 0)
    assert len(alarms) == 1
    a = alarms[0]
    assert a["level"] == "warning" and a["type"] == "high"
    assert a["device_id"] == "Di-Jiu-Shui-Chang-1" and a["param_key"] == "Vf"
    assert a["value"] == 36.2 and a["threshold"] == 35
    assert a["time"] == NOW_MS and a["id"] == f"alarm-{NOW_MS}-0"
    assert a["sms"] == {"status": "pending", "receivers": []}

def test_offline_alarm_is_critical_and_edge_triggered():
    judged = judge_device(None, [], NOW)
    # prev=None（首轮）不报 —— 对应前端 prevStatus=null
    alarms, _ = plan_alarms("D", None, judged, {}, NOW_MS, 0)
    assert alarms == []
    # prev=offline → offline 不报（持续离线不重复）
    alarms, _ = plan_alarms("D", "offline", judged, {}, NOW_MS, 0)
    assert alarms == []
    # prev=online/abnormal → offline 报 critical
    for prev in ("online", "abnormal"):
        alarms, _ = plan_alarms("D", prev, judged, {}, NOW_MS, 0)
        assert len(alarms) == 1
        a = alarms[0]
        assert a["type"] == "offline" and a["level"] == "critical"
        assert a["param_key"] is None and a["value"] is None and a["threshold"] is None

# ---------- 冷却去重 ----------

def test_cooldown_strict_boundary():
    """now - time < 600000ms 抑制；恰好 600000 允许再报"""
    judged = judge_device(reading(Vf=36.2), [rule("Vf", 5, 35)], NOW)
    key = cooldown_key("D", "Vf", "high")
    # 10 分钟内 1 毫秒 → 抑制
    recent = {cooldown_key("D", "Vf", "high"): NOW_MS - 599_999}
    alarms, _ = plan_alarms("D", "online", judged, recent, NOW_MS, 0)
    assert alarms == []
    # 恰好 10 分钟 → 允许
    recent = {cooldown_key("D", "Vf", "high"): NOW_MS - 600_000}
    alarms, _ = plan_alarms("D", "online", judged, recent, NOW_MS, 0)
    assert len(alarms) == 1

def test_cooldown_independent_across_params_and_types():
    judged = judge_device(reading(Vf=36.2, Sf=4.0), [rule("Vf", 5, 35), rule("Sf", 0.5, 3.5)], NOW)
    recent = {cooldown_key("D", "Vf", "high"): NOW_MS - 1}  # 只有 Vf-high 在冷却
    alarms, _ = plan_alarms("D", "online", judged, recent, NOW_MS, 0)
    assert [a["param_key"] for a in alarms] == ["Sf"]

def test_cooldown_offline_key_uses_placeholder():
    assert cooldown_key("D", None, "offline") == "D|__offline__|offline"

def test_within_scan_same_key_pushed_once():
    """同一轮里 recent_keys 会被就地更新（前端 tryPush 的 recent.add 语义）"""
    judged = judge_device(reading(Vf=36.2), [rule("Vf", 5, 35)], NOW)
    recent = {}
    alarms1, _ = plan_alarms("D1", "online", judged, recent, NOW_MS, 0)
    alarms2, _ = plan_alarms("D1", "online", judged, recent, NOW_MS, 0)  # 同键第二轮
    assert alarms1 and alarms2 == []

# ---------- 短信 ----------

class StubSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []
    def send(self, phone, text):
        self.sent.append((phone, text))
        return self.ok

def test_sms_no_receivers_immediate_failed():
    rec = {"id": "a1", "sms": {"status": "pending", "receivers": []}}
    settle_sms(rec, [], StubSender())
    assert rec["sms"]["status"] == "failed"
    assert "sent_at" not in rec["sms"]  # 立即 failed 不记 sent_at
    assert rec["sms"]["receivers"] == []

def alarm_record(aid):
    return {"id": aid, "time": NOW_MS, "device_id": "Di-Jiu-Shui-Chang-1",
            "param_key": "Vf", "type": "high", "value": 36.2, "threshold": 35,
            "level": "warning", "sms": {"status": "pending", "receivers": []}}

def test_sms_sent_and_failed_settle_with_sent_at():
    stub = StubSender(ok=True)
    rec = alarm_record("a1")
    settle_sms(rec, ["13800000000", "13900000000"], stub)
    assert rec["sms"]["status"] == "sent"
    assert rec["sms"]["receivers"] == ["13800000000", "13900000000"]
    assert "sent_at" in rec["sms"]
    assert len(stub.sent) == 2

    rec = alarm_record("a2")
    settle_sms(rec, ["13800000000"], StubSender(ok=False))
    assert rec["sms"]["status"] == "failed"
    assert "sent_at" in rec["sms"]

# ---------- 短信文案 ----------

def test_build_sms_text():
    dev = "Di-Jiu-Shui-Chang-1"
    high = {"device_id": dev, "param_key": "Vf", "type": "high", "value": 36.2, "threshold": 35}
    t = build_sms_text(high)
    assert "九厂一期-1#" in t and "沉降比" in t and "超上限" in t and "35" in t
    low = {"device_id": dev, "param_key": "Cf", "type": "low", "value": 0.1, "threshold": 0.2}
    assert "低于下限" in build_sms_text(low)
    offline = {"device_id": dev, "param_key": None, "type": "offline"}
    assert "离线" in build_sms_text(offline) and "13" in build_sms_text(offline)
