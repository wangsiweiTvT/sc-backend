# sc-backend · 水厂传感器监控后端

九厂一期水质监控系统的后端：MQTT 采集传感器数据入 MySQL，并对外提供 REST API 供前端（sc-front）调用。

## 架构

```
mqtt_client.py          mosquitto           mqtt_consumer.py         MySQL            api.py
(模拟 4 台设备上报)  →  (本地 broker)  →   (订阅 Chen-Su-Yi/#)  →  (mqtt_data 库)  →  (FastAPI REST)
```

- **mqtt_client.py** — 模拟发送端：4 台设备（`Di-Jiu-Shui-Chang-1..4`）各自独立连接，默认每 13 分钟上报一条六参数读数（±10% 随机波动）。测试可用 `MQTT_INTERVAL=5 python3 mqtt_client.py` 加快频率。
- **mqtt_consumer.py** — 采集端：订阅 `Chen-Su-Yi/#`，解析 JSON 入库 `sensor_data` 表；按「同一设备 + 同一上报时间 = 同一条数据」做幂等（unique key + `ON DUPLICATE KEY UPDATE`），MQTT QoS 1 重复投递不会产生重复行。
- **api.py** — REST API（FastAPI），交互文档见 `http://127.0.0.1:8000/docs`。
- **db.py** — 数据库连接公共模块，连接配置从环境变量 / `.env` 读取。
- **schema.sql** — 全部建表语句。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置数据库密码（不会进仓库）
cp .env.example .env
# 编辑 .env 填入 DB_PASSWORD

# 3. 建库建表（MySQL 8.x）
mysql -uroot -p < schema.sql

# 4. 启动各服务（互相独立，按需启动）
./start_consumer.sh   # MQTT 采集端
./start_api.sh        # REST API（端口 8000）
./start_client.sh     # 模拟发送端（真实环境用真设备，不需要它）
```

停止用对应的 `stop_*.sh`。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/devices` | 设备列表（条数、最后上报时间） |
| GET | `/api/devices/{id}/latest` | 单设备最新一条（无数据 404） |
| GET | `/api/devices/{id}/data` | 单设备历史区间（升序，默认近 24h，limit 1~10000） |
| GET | `/api/readings/latest` | 4 台设备最新读数快照（无数据为 null） |
| GET / POST / PUT / DELETE | `/api/alarms` | 告警记录存储（前端判定，后端只存取，上限 500 条） |
| GET / PUT | `/api/thresholds` | 阈值规则（4 设备 × 6 参数，空表返回默认值） |
| GET / POST / DELETE | `/api/receivers` | 短信接收人 |
| POST | `/api/devices/{id}/simulate-offline` | 演示用离线开关（空实现） |

监控参数：`Sf` 沉降速度 / `Vf` 沉降比 / `Fc` 流量 / `pHf` pH / `Tf` 温度 / `Cf` 余氯。

## 说明

- v1 的告警判定与短信模拟在前端完成，后端只负责存储；二期计划把判定和真实短信网关移到后端（`thresholds` 表届时直接复用）。
- 接口契约与联调记录见前端仓库 `docs/backend-api.md`。
