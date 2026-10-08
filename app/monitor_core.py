# 监控公共定义：api.py 与 anomaly_detector.py 共用，改这里两边同时生效
# 语义契约见前端仓库 docs/backend-api.md；判定规则细节见
# docs/superpowers/specs/2026-10-08-backend-detection-design.md
import json

from app.db import get_db_conn

# 固定的四台设备（判定/快照范围；历史测试设备不参与）
SNAPSHOT_DEVICES = [f"Di-Jiu-Shui-Chang-{i}" for i in range(1, 5)]

# 设备显示名（短信文案用，与前端设备档案一致）
DEVICE_NAMES = {f"Di-Jiu-Shui-Chang-{i}": f"九厂一期-{i}#" for i in range(1, 5)}

# 参数元数据：阈值键 -> (数据库列, 中文名, 单位)；单位仅 Vf 带百分号（对齐前端文案）
PARAM_META = {
    "Vf": ("vf", "沉降比", "%"),
    "Sf": ("sf", "沉降速度", ""),
    "Fc": ("fc", "流量", ""),
    "pHf": ("phf", "pH", ""),
    "Tf": ("tf", "温度", ""),
    "Cf": ("cf", "余氯", ""),
}
PARAM_COLUMN = {key: col for key, (col, _, _) in PARAM_META.items()}

# 判定常量（与前端 params.ts 一致：13 分钟离线、10 分钟告警冷却）
OFFLINE_AFTER_SECONDS = 780
ALARM_COOLDOWN_MS = 600_000
OFFLINE_KEY = "__offline__"  # 离线告警冷却键里 param_key 的占位（不能用 None 拼字符串）

# 空表时的默认阈值（与前端文档 §4.3 的默认表一致）
THRESHOLD_PARAMS = {
    "Vf": (5, 35), "Sf": (0.5, 3.5), "Fc": (800, 1500),
    "pHf": (6.5, 8.5), "Tf": (8, 30), "Cf": (0.2, 1.0),
}

ALARM_KEEP_MAX = 500


def default_thresholds():
    """thresholds 表为空时返回的默认 24 条（GET /api/thresholds 空表行为）"""
    return [
        {"deviceId": dev, "paramKey": param, "low": low, "high": high, "enabled": True}
        for dev in SNAPSHOT_DEVICES
        for param, (low, high) in THRESHOLD_PARAMS.items()
    ]


def load_thresholds():
    """检测器用：读 thresholds 表（snake_case 规则），空表回默认值"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT device_id, param_key, low, high, enabled FROM thresholds")
        rows = cur.fetchall()
    if not rows:
        return [
            {"device_id": dev, "param_key": param, "low": low, "high": high, "enabled": True}
            for dev in SNAPSHOT_DEVICES
            for param, (low, high) in THRESHOLD_PARAMS.items()
        ]
    return [
        {"device_id": r[0], "param_key": r[1], "low": float(r[2]), "high": float(r[3]),
         "enabled": bool(r[4])}
        for r in rows
    ]


def load_receivers():
    """全部短信接收人手机号（告警发给所有人，不按设备区分，与前端一致）"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT phone FROM receivers")
        return [r[0] for r in cur.fetchall()]


def upsert_alarms(records, trim=True):
    """按 id upsert 告警记录（JSON 原样存 alarms.payload），可选裁剪保留最新 ALARM_KEEP_MAX 条"""
    if not records:
        return
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO alarms (id, payload) VALUES (%s, %s)"
            " AS incoming ON DUPLICATE KEY UPDATE payload = incoming.payload",
            [(a["id"], json.dumps(a, ensure_ascii=False)) for a in records],
        )
        if trim:
            cur.execute("SELECT COUNT(*) FROM alarms")
            overflow = cur.fetchone()[0] - ALARM_KEEP_MAX
            if overflow > 0:
                cur.execute(
                    "DELETE FROM alarms ORDER BY created_at ASC, id ASC LIMIT %s",
                    (overflow,),
                )
