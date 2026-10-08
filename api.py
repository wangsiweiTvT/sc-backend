# 传感器数据查询 + 监控配置 API（供前端调用）
# 启动：python3 -m uvicorn api:app --port 8000
# 交互文档：http://127.0.0.1:8000/docs
# 接口契约见前端仓库 docs/backend-api.md
import json
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import get_db_conn

app = FastAPI(title="水厂传感器数据 API", version="0.2.0")

# CORS：前端 dev server（VITE_USE_MOCK=false 时跨域调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 传感器数据查询 ----------

# 查询返回的列，行转字典时按这个顺序取值
COLUMNS = ["id", "device_id", "sf", "vf", "fc", "phf", "tf", "cf",
           "settling_ratio", "reported_at", "received_at"]

SELECT_SQL = f"SELECT {', '.join(COLUMNS)} FROM sensor_data"

def row_to_dict(row):
    """数据库行转 JSON 友好的字典：Decimal 转 float，datetime 转 ISO 字符串"""
    d = dict(zip(COLUMNS, row))
    for key in ("sf", "vf", "fc", "phf", "tf", "cf", "settling_ratio"):
        if d[key] is not None:
            d[key] = float(d[key])
    for key in ("reported_at", "received_at"):
        d[key] = d[key].isoformat()
    return d

@app.get("/api/devices")
async def list_devices():
    """设备列表，附带数据条数和最后上报时间"""
    sql = """SELECT device_id, COUNT(*), MAX(reported_at)
             FROM sensor_data GROUP BY device_id ORDER BY device_id"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [
        {
            "device_id": r[0],
            "count": r[1],
            "last_reported_at": r[2].isoformat(),
        }
        for r in rows
    ]

@app.get("/api/devices/{device_id}/latest")
async def latest_data(device_id: str):
    """某设备最新一条数据"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            f"{SELECT_SQL} WHERE device_id = %s ORDER BY reported_at DESC LIMIT 1",
            (device_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"设备 {device_id} 没有数据")
    return row_to_dict(row)

@app.get("/api/devices/{device_id}/data")
async def history_data(
    device_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(default=1000, ge=1, le=10000),
):
    """历史区间查询，默认最近 24 小时，按上报时间升序（画曲线直接用）"""
    now = datetime.now()
    start = start or now - timedelta(hours=24)
    end = end or now
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            f"{SELECT_SQL} WHERE device_id = %s AND reported_at >= %s AND reported_at <= %s"
            " ORDER BY reported_at ASC LIMIT %s",
            (device_id, start, end, limit),
        )
        rows = cur.fetchall()
    return [row_to_dict(r) for r in rows]

# ---------- 实时快照 ----------

# 固定四台设备；快照里还会带上 sensor_data 中出现过的其他设备
SNAPSHOT_DEVICES = [f"Di-Jiu-Shui-Chang-{i}" for i in range(1, 5)]

@app.get("/api/readings/latest")
async def readings_latest():
    """全部设备最新一条一次返回，无数据的设备为 null"""
    conn = get_db_conn()
    readings = {}
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT device_id FROM sensor_data")
        devices = sorted(set(SNAPSHOT_DEVICES) | {r[0] for r in cur.fetchall()})
        for dev in devices:
            cur.execute(
                f"{SELECT_SQL} WHERE device_id = %s ORDER BY reported_at DESC LIMIT 1",
                (dev,),
            )
            row = cur.fetchone()
            readings[dev] = row_to_dict(row) if row else None
    return {"server_time": datetime.now().isoformat(), "readings": readings}

# ---------- 告警存储（记录由前端判定生成，后端原样存取） ----------

ALARM_KEEP_MAX = 500

@app.get("/api/alarms")
async def list_alarms():
    """全部告警记录，按追加顺序返回"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM alarms ORDER BY created_at ASC, id ASC")
        return [json.loads(r[0]) for r in cur.fetchall()]

@app.post("/api/alarms")
async def append_alarms(alarms: List[dict]):
    """批量追加，id 冲突覆盖，追加后裁剪保留最新 500 条"""
    if any(not a.get("id") for a in alarms):
        raise HTTPException(status_code=400, detail="每条记录必须带 id 字段")
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO alarms (id, payload) VALUES (%s, %s)"
            " AS incoming ON DUPLICATE KEY UPDATE payload = incoming.payload",
            [(a["id"], json.dumps(a, ensure_ascii=False)) for a in alarms],
        )
        cur.execute("SELECT COUNT(*) FROM alarms")
        overflow = cur.fetchone()[0] - ALARM_KEEP_MAX
        if overflow > 0:
            cur.execute(
                "DELETE FROM alarms ORDER BY created_at ASC, id ASC LIMIT %s",
                (overflow,),
            )
    return {}

@app.put("/api/alarms/{alarm_id}")
async def update_alarm(alarm_id: str, alarm: dict):
    """按 id 覆盖单条记录，不存在就插入（upsert），始终 200"""
    key = alarm.get("id", alarm_id)
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO alarms (id, payload) VALUES (%s, %s)"
            " AS incoming ON DUPLICATE KEY UPDATE payload = incoming.payload",
            (key, json.dumps(alarm, ensure_ascii=False)),
        )
    return {}

@app.delete("/api/alarms")
async def clear_alarms():
    """清空全部告警"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alarms")
    return {}

# ---------- 阈值规则（兼作二期后端检测器的配置源） ----------

# 空表时返回的默认值，与前端文档 §4.3 的默认表一致
THRESHOLD_PARAMS = {
    "Vf": (5, 35), "Sf": (0.5, 3.5), "Fc": (800, 1500),
    "pHf": (6.5, 8.5), "Tf": (8, 30), "Cf": (0.2, 1.0),
}

def default_thresholds():
    return [
        {"deviceId": dev, "paramKey": param, "low": low, "high": high, "enabled": True}
        for dev in SNAPSHOT_DEVICES
        for param, (low, high) in THRESHOLD_PARAMS.items()
    ]

@app.get("/api/thresholds")
async def get_thresholds():
    """24 条阈值规则；表为空时返回代码默认值（前端不兜底）"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT device_id, param_key, low, high, enabled FROM thresholds"
            " ORDER BY device_id, param_key"
        )
        rows = cur.fetchall()
    if not rows:
        return default_thresholds()
    return [
        {"deviceId": r[0], "paramKey": r[1], "low": r[2], "high": r[3], "enabled": bool(r[4])}
        for r in rows
    ]

@app.put("/api/thresholds")
async def put_thresholds(rules: List[dict]):
    """整体覆盖保存阈值规则"""
    parsed = []
    for r in rules:
        try:
            parsed.append((
                r["deviceId"], r["paramKey"], float(r["low"]), float(r["high"]),
                1 if r.get("enabled", True) else 0,
            ))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"规则字段缺失或非数字: {r}")
    conn = get_db_conn()
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM thresholds")
            cur.executemany(
                "INSERT INTO thresholds (device_id, param_key, low, high, enabled)"
                " VALUES (%s, %s, %s, %s, %s)",
                parsed,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {}

# ---------- 短信接收人 ----------

class ReceiverIn(BaseModel):
    name: str
    phone: str = Field(pattern=r"^1\d{10}$")  # 后端兜底校验，前端已校验

@app.get("/api/receivers")
async def list_receivers():
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, phone FROM receivers ORDER BY id")
        return [{"id": r[0], "name": r[1], "phone": r[2]} for r in cur.fetchall()]

@app.post("/api/receivers")
async def add_receiver(receiver: ReceiverIn):
    rec = {"id": f"rcv-{uuid4().hex[:8]}", "name": receiver.name, "phone": receiver.phone}
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO receivers (id, name, phone) VALUES (%s, %s, %s)",
            (rec["id"], rec["name"], rec["phone"]),
        )
    return rec

@app.delete("/api/receivers/{receiver_id}")
async def delete_receiver(receiver_id: str):
    """幂等删除，id 不存在也返回 200"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM receivers WHERE id = %s", (receiver_id,))
    return {}

# ---------- 演示用离线开关 ----------

@app.post("/api/devices/{device_id}/simulate-offline")
async def simulate_offline(device_id: str):
    """演示"手动置离线"按钮专用，前端本地表现，后端空实现"""
    return {}
