import paho.mqtt.client as mqtt
import json
import os
import random
import time
from datetime import datetime

from app.log import log

class MQTTClient:
    def __init__(self, broker_host, broker_port=1883, client_id=None):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id or f"mqtt_client_{int(time.time())}"
        self.client = None
        self.connected = False
        self.topic_prefix = "Chen-Su-Yi"
        
    def connect(self):
        """连接到 MQTT 服务器"""
        try:
            # ====== 修改这里：指定回调 API 版本 ======
            # 使用 CallbackAPIVersion.VERSION1 兼容旧版本代码
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.client_id
            )
            # ======================================
            
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            
            # 如果需要认证，取消下面的注释
            # self.client.username_pw_set("username", "password")
            
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()  # 非阻塞模式
            
            # 等待连接成功
            wait_time = 0
            while not self.connected and wait_time < 5:
                time.sleep(0.1)
                wait_time += 0.1
            
            return self.connected
        except Exception as e:
            log(f"MQTT 连接失败: {e}")
            return False
    
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            log(f"✅ MQTT 已连接到 {self.broker_host}:{self.broker_port}")
        else:
            self.connected = False
            log(f"❌ MQTT 连接失败，返回码: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        log("MQTT 断开连接")
    
    def publish_data(self, data_dict):
        """发布数据到 MQTT"""
        if not self.connected or self.client is None:
            return False
        
        try:
            # 添加时间戳
            data_dict['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 转换为 JSON
            payload = json.dumps(data_dict, ensure_ascii=False)
            
            # 发布到主题
            topic = f"{self.topic_prefix}/{self.client_id}"
            result = self.client.publish(topic, payload, qos=1)
            
            if result.rc == 0:
                log(f"📤 MQTT 发布成功: {topic} -> {payload}")
                return True
            else:
                log(f"❌ MQTT 发布失败，返回码: {result.rc}")
                return False
                
        except Exception as e:
            log(f"MQTT 发布异常: {e}")
            return False
    
    def publish_sf_vf(self, sf, vf, settle_ratio=None):
        """专门发布 Sf 和 Vf 数据"""
        data = {
            "Sf": sf,  # 沉降速度
            "Vf": vf,  # 沉降比
        }
        if settle_ratio is not None:
            data["settling_ratio"] = settle_ratio
            
        return self.publish_data(data)
    
    def disconnect(self):
        """断开 MQTT 连接"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            log("MQTT 已断开")

def jittered_data(data, ratio=0.1):
    """给基础值加 ±ratio 随机波动，模拟真实传感器读数"""
    return {k: round(v * (1 + random.uniform(-ratio, ratio)), 2)
            for k, v in data.items()}

if __name__ == "__main__":
    MQTT_BROKER_HOST = "127.0.0.1"  # MQTT 服务器地址（本地 broker）
    MQTT_BROKER_PORT = 1883
    # 模拟 4 台设备，每台一条独立连接、各自的主题（与前端四台设备/默认阈值对应）
    DEVICE_IDS = [f"Di-Jiu-Shui-Chang-{i}" for i in range(1, 5)]
    # 发送间隔（秒），默认 13 分钟；测试可用环境变量覆盖，如 MQTT_INTERVAL=5
    INTERVAL_SECONDS = int(os.environ.get("MQTT_INTERVAL", 13 * 60))

    clients = []
    for device_id in DEVICE_IDS:
        c = MQTTClient(MQTT_BROKER_HOST, MQTT_BROKER_PORT, client_id=device_id)
        if c.connect():
            clients.append(c)
        else:
            log(f"⚠️ {device_id} 连接失败，本轮跳过")
    if not clients:
        raise SystemExit("❌ 一台设备都没连上，退出")

    # 基础值取在默认阈值带的中间（见前端 docs/backend-api.md §4.3），
    # ±10% 波动后仍在带内，不会触发前端告警
    detailed_data = {
        "Sf": 2.0,    # 沉降速度 m/h（阈值 0.5~3.5）
        "Vf": 20,     # 沉降比 %（阈值 5~35）
        "Fc": 1150,   # 流量 m³/h（阈值 800~1500）
        "pHf": 7.5,   # pH（阈值 6.5~8.5）
        "Tf": 19,     # 温度 ℃（阈值 8~30）
        "Cf": 0.6,    # 余氯 mg/L（阈值 0.2~1.0）
    }

    log(f"⏱ {len(clients)} 台设备，每 {INTERVAL_SECONDS} 秒各发一条（±10% 波动），Ctrl+C 退出")
    try:
        while True:
            for c in clients:
                if not c.publish_data(jittered_data(detailed_data)):
                    log(f"⚠️ {c.client_id} 本次发送失败（可能断线），paho 会自动重连，下轮重试")
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")
    finally:
        for c in clients:
            c.disconnect()