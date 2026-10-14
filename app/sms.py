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

from app.monitor_core import DEVICE_NAMES, PARAM_META, OFFLINE_AFTER_SECONDS
from app.db import get_db_conn
from app.log import log


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


def _clip(s, limit=19):
    """微信模板消息每行约 20 字上限且不换行：超长行截断留余量"""
    return s if len(s) <= limit else s[:limit - 1] + "…"


def wechat_fields(alarm, text, now_str):
    """告警 → 微信模板四行短字段（每行 ≤19 字，规避每行约 20 字的展示截断）。
    有告警结构按 设备/指标数值/阈值/时间 拆行；没有则文案兜底进第二行。"""
    name = DEVICE_NAMES.get(alarm.get("device_id"), alarm.get("device_id")) if alarm else None
    if alarm and alarm.get("type") == "offline":
        mins = int(OFFLINE_AFTER_SECONDS // 60)
        vals = [name, "设备离线", f"超过{mins}分钟"]
    elif alarm and alarm.get("param_key") in PARAM_META:
        _, cname, unit = PARAM_META[alarm["param_key"]]
        direction = "超上限" if alarm["type"] == "high" else "低于下限"
        vals = [name, f"{cname} {_fmt(alarm['value'])}{unit}",
                f"{direction} {_fmt(alarm['threshold'])}{unit}"]
    else:
        vals = ["设备告警", text, "–"]
    vals.append(now_str)
    return {f"keyword{i}": {"value": _clip(v), "color": "#173177"}
            for i, v in enumerate(vals, start=1)}


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
    ok = all(sender.send(phone, text, record) for phone in phones)  # 遇失败即止
    record["sms"]["status"] = "sent" if ok else "failed"
    record["sms"]["sent_at"] = int(time.time() * 1000)
    return record


class DryrunSender:
    """模拟发送：日志可见、实际不发（SMS_PROVIDER=dryrun，默认）"""

    def send(self, phone, text, record=None):
        log(f"[SMS-dryrun] → {phone}: {text}", flush=True)
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

    def send(self, phone, text, record=None):
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
                log(f"[SMS-aliyun] 发送失败: {result}", flush=True)
                return False
            return True
        except Exception as e:
            log(f"[SMS-aliyun] 发送异常: {e}", flush=True)
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


class WechatSandboxSender:
    """微信公众号测试号模板消息（SMS_PROVIDER=wechat_sandbox）。
    按手机号查 receivers.openid 逐人发送，订阅路由/状态机不变，渠道只管送达。
    测试号的「接口配置信息」（服务器回调）不需要——我们只发不收。
    .env 填 WECHAT_APP_ID / WECHAT_APP_SECRET / WECHAT_TEMPLATE_ID（测试号管理页可取）。"""

    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
    SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"
    BAD_TOKEN_CODES = (40001, 40014, 42001)  # token 无效/不合法/过期 → 重取一次再发

    def __init__(self, post=None):
        cfg = {
            "WECHAT_APP_ID": os.environ.get("WECHAT_APP_ID"),
            "WECHAT_APP_SECRET": os.environ.get("WECHAT_APP_SECRET"),
            "WECHAT_TEMPLATE_ID": os.environ.get("WECHAT_TEMPLATE_ID"),
        }
        missing = [k for k, v in cfg.items() if not v]
        if missing:
            raise RuntimeError(f"SMS_PROVIDER=wechat_sandbox 但 .env 缺少配置: {missing}")
        self.cfg = cfg
        self._token = None
        self._token_expires_at = 0.0
        self._post = post or self._http_post_json  # 测试注入点

    @staticmethod
    def _http_post_json(url, payload):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    @staticmethod
    def _openid_for(phone):
        """手机号 → 测试号 openid（接收人扫码关注后从测试号用户列表抄录，配进 receivers 表）"""
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT openid FROM receivers WHERE phone = %s", (phone,))
            row = cur.fetchone()
        return row[0] if row and row[0] else None

    def _get_token(self):
        """access_token 缓存（提前 5 分钟过期防边界），失效即重取"""
        if self._token and time.time() < self._token_expires_at:
            return self._token
        r = self._post(
            f"{self.TOKEN_URL}?grant_type=client_credential"
            f"&appid={self.cfg['WECHAT_APP_ID']}&secret={self.cfg['WECHAT_APP_SECRET']}", None)
        if "access_token" not in r:
            raise RuntimeError(f"微信 access_token 获取失败: {r}")
        self._token = r["access_token"]
        self._token_expires_at = time.time() + r.get("expires_in", 7200) - 300
        return self._token

    def send(self, phone, text, record=None):
        openid = self._openid_for(phone)
        if not openid:
            log(f"[SMS-wechat] {phone} 未配 openid，无法发送（测试号用户列表可查）", flush=True)
            return False
        # 四行短字段（设备/指标数值/阈值/时间）：微信每行约 20 字上限且不能换行，
        # 长文案整段塞一个字段必被截断（实测"…"截断且卡片无"查看全文"入口）
        now_str = datetime.now().strftime("%m-%d %H:%M:%S")
        payload = {
            "touser": openid,
            "template_id": self.cfg["WECHAT_TEMPLATE_ID"],
            "data": wechat_fields(record, text, now_str),
        }
        try:
            r = self._post(f"{self.SEND_URL}?access_token={self._get_token()}", payload)
            if r.get("errcode", 0) in self.BAD_TOKEN_CODES:  # token 过期：重取一次再发
                self._token = None
                self._token_expires_at = 0.0
                r = self._post(f"{self.SEND_URL}?access_token={self._get_token()}", payload)
            if r.get("errcode", 0) != 0:
                log(f"[SMS-wechat] 发送失败: {r}", flush=True)
                return False
            return True
        except Exception as e:  # 网络/凭据异常不能炸掉检测线程
            log(f"[SMS-wechat] 发送异常: {e}", flush=True)
            return False


def get_sender():
    provider = os.environ.get("SMS_PROVIDER", "dryrun").lower()
    if provider == "aliyun":
        return AliyunSender()
    if provider == "wechat_sandbox":
        return WechatSandboxSender()
    return DryrunSender()
