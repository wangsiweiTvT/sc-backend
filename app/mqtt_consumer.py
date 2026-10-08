# 这是mqtt接受消息的代码
import json
import warnings
from datetime import datetime

import paho.mqtt.client as mqtt

from app.db import get_db_conn
from app.log import log, setup

setup("consumer")  # 本服务日志写 logs/consumer.log（从仓库根目录启动）

# MQTT 服务器配置（本地 broker）
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
TOPIC = "Chen-Su-Yi/#"

def to_number(value):
    """能转数字就转，转不了返回 None"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def save_to_mysql(topic, payload):
    """把一条消息存入 sensor_data 表。返回 'ok'（新增）/ 'dup'（重复已跳过）/ 'error'"""
    device_id = topic.split("/")[-1]

    # 设备上报时间；缺失或格式不对就用当前时间
    try:
        reported_at = datetime.strptime(payload["timestamp"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError, TypeError):
        reported_at = datetime.now()

    # ON DUPLICATE KEY UPDATE id = id 是哑更新：撞 uk_device_time 唯一键时不改数据，
    # 只借 rowcount 区分（1=新插入，0=重复），其余错误照常抛出（不像 INSERT IGNORE 会全吞）
    sql = """INSERT INTO sensor_data
             (device_id, topic, sf, vf, fc, phf, tf, cf, settling_ratio, reported_at)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
             ON DUPLICATE KEY UPDATE id = id"""
    args = (
        device_id, topic,
        to_number(payload.get("Sf")), to_number(payload.get("Vf")),
        to_number(payload.get("Fc")), to_number(payload.get("pHf")),
        to_number(payload.get("Tf")), to_number(payload.get("Cf")),
        to_number(payload.get("settling_ratio")),
        reported_at,
    )
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return "dup" if cur.rowcount == 0 else "ok"
    except Exception as e:
        log(f"❌ MySQL 写入异常: {e}")
        return "error"

# 连接回调函数
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log("✅ 成功连接到 MQTT 服务器")
        # 订阅主题
        client.subscribe(TOPIC)
        log(f"📡 已订阅主题: {TOPIC}")
    else:
        log(f"❌ 连接失败，返回码: {rc}")

# 接收消息回调函数
def on_message(client, userdata, msg):
    log(f"📩 收到消息 - 主题: {msg.topic}, 内容: {msg.payload.decode()}")

    # 解析 JSON，不是合法 JSON 就跳过
    try:
        payload = json.loads(msg.payload.decode())
    except (ValueError, UnicodeDecodeError) as e:
        log(f"⚠️ 消息不是合法 JSON，跳过入库: {e}")
        return

    result = save_to_mysql(msg.topic, payload)
    if result == "ok":
        log("💾 已存入 MySQL")
    elif result == "dup":
        log("♻️ 重复数据，已跳过")

# 创建客户端实例（paho-mqtt 2.x 需显式指定回调 API 版本；
# VERSION1 的弃用警告是已知启动噪音，压掉让 *.err 只记真故障）
warnings.filterwarnings("ignore", message="Callback API version 1")
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
client.on_connect = on_connect
client.on_message = on_message

# 连接到 MQTT 服务器
log(f"🔗 正在连接 MQTT 服务器 {BROKER_HOST}:{BROKER_PORT}...")
client.connect(BROKER_HOST, BROKER_PORT, 60)

# 保持运行，等待接收消息
log("⏳ 等待接收消息... (按 Ctrl+C 退出)")
client.loop_forever()