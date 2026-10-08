# 异常检测器（二期）：常驻进程，周期扫描 sensor_data 生成告警并触发短信。
# 判定语义与前端 v1 完全一致（由前端 alarmEngine.spec.ts 钉死，tests/test_detector.py 移植），
# 细节见 docs/superpowers/specs/2026-10-08-backend-detection-design.md
# 启动：./scripts/start_detector.sh（或 python3 -m app.anomaly_detector）
import json
import os
import time
from datetime import datetime

from app.db import get_db_conn
from app.monitor_core import (
    SNAPSHOT_DEVICES, PARAM_COLUMN, OFFLINE_KEY, ALARM_COOLDOWN_MS,
    load_thresholds, load_receivers, upsert_alarms,
)
from app.sms import settle_sms, get_sender
from app.log import log

# 均可用环境变量覆盖；离线窗口默认 780s（=13 分钟，与前端 OFFLINE_AFTER_MS 一致），覆盖值仅供测试
OFFLINE_AFTER_SECONDS = float(os.environ.get("DETECTOR_OFFLINE_SECONDS", 780))
POLL_SECONDS = float(os.environ.get("DETECTOR_POLL_SECONDS", 10))

# 进程内状态：上一轮各设备状态（离线边沿判定用，重启后为空 → 首轮不报，对齐前端 prevStatus=null）
prev_status = {}
# 告警 id 序号（进程内单调递增，对齐前端模块级 seq）
seq = 0


def judge_device(reading, rules, now, offline_after=None):
    """单设备判定：返回 {status: online|abnormal|offline, violations: [...]}
    离线 = 无数据或数据龄严格 > offline_after 秒；越限 = 严格 >high / <low（等于边界不算），high 优先"""
    if offline_after is None:
        offline_after = OFFLINE_AFTER_SECONDS
    if reading is None or (now - reading["reported_at"]).total_seconds() > offline_after:
        return {"status": "offline", "violations": []}
    violations = []
    for r in rules:
        if not r["enabled"]:
            continue
        value = reading.get(PARAM_COLUMN[r["param_key"]])
        if value is None:
            continue
        if value > r["high"]:
            violations.append(
                {"param_key": r["param_key"], "value": value, "type": "high", "threshold": r["high"]}
            )
        elif value < r["low"]:
            violations.append(
                {"param_key": r["param_key"], "value": value, "type": "low", "threshold": r["low"]}
            )
    return {"status": "abnormal" if violations else "online", "violations": violations}


def cooldown_key(device_id, param_key, alarm_type):
    """冷却键：(设备, 参数, 类型) 三元组；离线告警 param_key 用占位符"""
    return f"{device_id}|{param_key or OFFLINE_KEY}|{alarm_type}"


def plan_alarms(device_id, prev, judged, recent, now_ms, seq_start):
    """按冷却去重生成新告警。
    recent: {冷却键: 最近一次告警 time(ms)}，就地更新（同轮同键只报一次，对齐前端 tryPush）
    冷却语义：now - 最近告警 < ALARM_COOLDOWN_MS 则抑制；恰好到期允许再报；与是否恢复无关。
    离线为边沿触发：prev 非 None 且非 offline → 本轮 offline 才报。
    返回 (新告警列表, 下一个序号)"""
    alarms = []
    n = seq_start
    for v in judged["violations"]:
        key = cooldown_key(device_id, v["param_key"], v["type"])
        if now_ms - recent.get(key, float("-inf")) < ALARM_COOLDOWN_MS:
            continue
        recent[key] = now_ms
        alarms.append({
            "id": f"alarm-{now_ms}-{n}", "time": now_ms, "device_id": device_id,
            "param_key": v["param_key"], "type": v["type"], "value": v["value"],
            "threshold": v["threshold"], "level": "warning",
            "sms": {"status": "pending", "receivers": []},
        })
        n += 1
    if judged["status"] == "offline" and prev is not None and prev != "offline":
        key = cooldown_key(device_id, None, "offline")
        if now_ms - recent.get(key, float("-inf")) >= ALARM_COOLDOWN_MS:
            recent[key] = now_ms
            alarms.append({
                "id": f"alarm-{now_ms}-{n}", "time": now_ms, "device_id": device_id,
                "param_key": None, "type": "offline", "value": None, "threshold": None,
                "level": "critical", "sms": {"status": "pending", "receivers": []},
            })
            n += 1
    return alarms, n


# ---------- 以下为扫描流程（依赖数据库） ----------

def latest_readings():
    """四台设备各自的最新一条读数（Decimal 统一转 float，无数据的设备不在返回里）"""
    cols = ["sf", "vf", "fc", "phf", "tf", "cf"]
    conn = get_db_conn()
    readings = {}
    with conn.cursor() as cur:
        for dev in SNAPSHOT_DEVICES:
            cur.execute(
                f"SELECT {', '.join(cols)}, reported_at FROM sensor_data"
                " WHERE device_id = %s ORDER BY reported_at DESC LIMIT 1",
                (dev,),
            )
            row = cur.fetchone()
            if row:
                readings[dev] = {c: (float(v) if v is not None else None)
                                 for c, v in zip(cols, row[:-1])}
                readings[dev]["reported_at"] = row[-1]
    return readings


def recent_cooldowns(now_ms):
    """查最近冷却窗口内的告警，返回 {冷却键: time(ms)}（等价于前端用内存 records 算 recent）"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        # created_at 是入库时间，比记录自身的 time 晚一点点，窗口放宽到 11 分钟做预过滤
        cur.execute("SELECT payload FROM alarms WHERE created_at >= NOW() - INTERVAL 11 MINUTE")
        rows = cur.fetchall()
    recent = {}
    for (payload,) in rows:
        try:
            a = json.loads(payload)
        except (TypeError, ValueError):
            continue
        t = a.get("time")
        if t is None or now_ms - t >= ALARM_COOLDOWN_MS:
            continue
        key = cooldown_key(a.get("device_id"), a.get("param_key"), a.get("type"))
        recent[key] = max(recent.get(key, 0), t)
    return recent


def heartbeat():
    """写 detector_status 心跳（GET /api/detector/status 读它）"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO detector_status (id, last_scan_at, scans_count)"
            " VALUES (1, %s, 1) AS incoming"
            " ON DUPLICATE KEY UPDATE last_scan_at = incoming.last_scan_at,"
            " scans_count = detector_status.scans_count + 1",
            (datetime.now(),),
        )


def scan_once(sender):
    """一轮完整扫描：判定 → 生成告警落库 → 发短信 → 回写状态。返回本轮新告警数"""
    global seq
    now = datetime.now()
    now_ms = int(time.time() * 1000)
    heartbeat()

    rules_by_dev = {}
    for r in load_thresholds():
        rules_by_dev.setdefault(r["device_id"], []).append(r)
    readings = latest_readings()
    recent = recent_cooldowns(now_ms)
    phones = load_receivers()

    new_alarms = []
    for dev in SNAPSHOT_DEVICES:
        judged = judge_device(readings.get(dev), rules_by_dev.get(dev, []), now)
        alarms, seq = plan_alarms(dev, prev_status.get(dev), judged, recent, now_ms, seq)
        prev_status[dev] = judged["status"]
        new_alarms.extend(alarms)

    if not new_alarms:
        return 0

    upsert_alarms(new_alarms, trim=False)  # 先落库（此时短信是 pending）
    for a in new_alarms:
        log(f"🚨 [{a['level']}] {a['device_id']} {a['type']}"
              + (f" {a['param_key']}={a['value']} (阈值 {a['threshold']})" if a["param_key"] else ""),
              flush=True)
        settle_sms(a, phones, sender)
    upsert_alarms(new_alarms, trim=True)  # 短信结算后整条回写 + 裁剪
    return len(new_alarms)


if __name__ == "__main__":
    sender = get_sender()
    log(f"检测器启动：每 {POLL_SECONDS:g}s 扫描 {len(SNAPSHOT_DEVICES)} 台设备，"
          f"离线窗口 {OFFLINE_AFTER_SECONDS:g}s，冷却 {ALARM_COOLDOWN_MS // 1000}s，"
          f"短信 {type(sender).__name__}（Ctrl+C 退出）", flush=True)
    while True:
        try:
            n = scan_once(sender)
            if n:
                log(f"本轮生成 {n} 条告警", flush=True)
        except Exception as e:
            log(f"⚠️ 扫描异常（下轮继续）: {e}", flush=True)
        time.sleep(POLL_SECONDS)
