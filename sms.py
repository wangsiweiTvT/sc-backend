# 短信模块：可插拔发送器 + 文案生成 + 告警短信状态机（语义对齐前端 smsService.ts）
# .env 里 SMS_PROVIDER=dryrun（默认，只打日志）/ aliyun（真实发送）
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from monitor_core import DEVICE_NAMES, PARAM_META, OFFLINE_AFTER_SECONDS


def _fmt(v):
    """36.2 → '36.2'，35.0 → '35'：去掉尾零，短信里别占字数"""
    return f"{float(v):.2f}".rstrip("0").rstrip(".")


def build_sms_text(alarm):
    """生成短信文案，格式对齐前端 buildSmsText"""
    name = DEVICE_NAMES.get(alarm["device_id"], alarm["device_id"])
    if alarm["type"] == "offline":
        mins = int(OFFLINE_AFTER_SECONDS // 60)
        return f"【水厂监控】{name} 设备离线超过{mins}分钟，请及时处理。"
    _, cname, unit = PARAM_META[alarm["param_key"]]
    direction = "超上限" if alarm["type"] == "high" else "低于下限"
    return (
        f"【水厂监控】{name} {cname} {_fmt(alarm['value'])}{unit}，"
        f"{direction} {_fmt(alarm['threshold'])}{unit}，请及时处理。"
    )


def settle_sms(record, phones, sender):
    """告警短信状态机（对齐前端）：
    - 无接收人：立即 failed，不记 sent_at
    - 有接收人：逐个发送，全部成功 sent / 任一失败 failed，都记 sent_at（毫秒）
    """
    record["sms"]["receivers"] = list(phones)
    if not phones:
        record["sms"]["status"] = "failed"
        return record
    text = build_sms_text(record)
    ok = all(sender.send(phone, text) for phone in phones)  # 遇失败即止
    record["sms"]["status"] = "sent" if ok else "failed"
    record["sms"]["sent_at"] = int(time.time() * 1000)
    return record


class DryrunSender:
    """模拟发送：日志可见、实际不发（SMS_PROVIDER=dryrun，默认）"""

    def send(self, phone, text):
        print(f"[SMS-dryrun] → {phone}: {text}", flush=True)
        return True


class AliyunSender:
    """阿里云短信（SMS_PROVIDER=aliyun）。
    ⚠️ 未实测：账号/签名/模板批下来后，在 .env 填 SMS_ALIYUN_ACCESS_KEY_ID /
    SMS_ALIYUN_ACCESS_KEY_SECRET / SMS_ALIYUN_SIGN_NAME / SMS_ALIYUN_TEMPLATE_CODE 即启用。
    TemplateParam 目前传 {"msg": 文案}，必须与审核通过的模板占位符一致，届时按模板调整。"""

    API_URL = "https://dysmsapi.aliyuncs.com/"

    def __init__(self):
        cfg = {
            "SMS_ALIYUN_ACCESS_KEY_ID": os.environ.get("SMS_ALIYUN_ACCESS_KEY_ID"),
            "SMS_ALIYUN_ACCESS_KEY_SECRET": os.environ.get("SMS_ALIYUN_ACCESS_KEY_SECRET"),
            "SMS_ALIYUN_SIGN_NAME": os.environ.get("SMS_ALIYUN_SIGN_NAME"),
            "SMS_ALIYUN_TEMPLATE_CODE": os.environ.get("SMS_ALIYUN_TEMPLATE_CODE"),
        }
        missing = [k for k, v in cfg.items() if not v]
        if missing:
            raise RuntimeError(f"SMS_PROVIDER=aliyun 但 .env 缺少配置: {missing}")
        self.cfg = cfg

    def send(self, phone, text):
        params = {
            "Action": "SendSms", "Version": "2017-05-25", "Format": "JSON",
            "PhoneNumbers": phone,
            "SignName": self.cfg["SMS_ALIYUN_SIGN_NAME"],
            "TemplateCode": self.cfg["SMS_ALIYUN_TEMPLATE_CODE"],
            "TemplateParam": json.dumps({"msg": text}, ensure_ascii=False),
            "AccessKeyId": self.cfg["SMS_ALIYUN_ACCESS_KEY_ID"],
            "SignatureMethod": "HMAC-SHA1", "SignatureVersion": "1.0",
            "SignatureNonce": uuid.uuid4().hex,
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        params["Signature"] = self._sign(params)
        url = self.API_URL + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                result = json.loads(resp.read())
            if result.get("Code") != "OK":
                print(f"[SMS-aliyun] 发送失败: {result}", flush=True)
                return False
            return True
        except Exception as e:
            print(f"[SMS-aliyun] 发送异常: {e}", flush=True)
            return False

    def _sign(self, params):
        """阿里云 RPC 签名 V1：HMAC-SHA1(GET&%2F&<按 key 排序并编码的 query>, secret&)"""
        def enc(s):
            return urllib.parse.quote(str(s), safe="~-._")
        query = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(params.items()))
        string_to_sign = "GET&%2F&" + enc(query)
        digest = hmac.new(
            (self.cfg["SMS_ALIYUN_ACCESS_KEY_SECRET"] + "&").encode(),
            string_to_sign.encode(), hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode()


def get_sender():
    provider = os.environ.get("SMS_PROVIDER", "dryrun").lower()
    if provider == "aliyun":
        return AliyunSender()
    return DryrunSender()
