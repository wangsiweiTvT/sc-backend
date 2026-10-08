# sc-backend · 水厂传感器监控后端

九厂一期水质监控系统的后端：MQTT 采集传感器数据入 MySQL，并对外提供 REST API 供前端（sc-front）调用。

## 目录结构

```
app/        全部 Python 源码（包名 app，从仓库根目录以 -m 方式启动）
scripts/    各服务启停脚本（start_*.sh / stop_*.sh）
logs/       运行日志（git 不跟踪）
tests/      判定/冷却/短信语义测试（移植自前端 spec）
docs/       设计文档
根目录      schema.sql / requirements*.txt / .env / README
```

## 架构

```
app/mqtt_client.py       mosquitto          app/mqtt_consumer.py       MySQL
(模拟 4 台设备)    →    (本地 broker)  →   (订阅 Chen-Su-Yi/# 入库)  → (mqtt_data 库)
                                                          ├→ app/anomaly_detector.py（每 10s 判定，越限/离线告警 + 短信）
                                                          └→ app/api.py（FastAPI REST，供前端）
```

- **app/mqtt_client.py** — 模拟发送端：4 台设备（`Di-Jiu-Shui-Chang-1..4`）各自独立连接，默认每 13 分钟上报一条六参数读数（±10% 随机波动）。测试可用 `MQTT_INTERVAL=5 python3 -m app.mqtt_client` 加快频率。
- **app/mqtt_consumer.py** — 采集端：订阅 `Chen-Su-Yi/#`，解析 JSON 入库 `sensor_data` 表；按「同一设备 + 同一上报时间 = 同一条数据」做幂等（unique key + `ON DUPLICATE KEY UPDATE`），MQTT QoS 1 重复投递不会产生重复行。
- **app/anomaly_detector.py** — 异常检测器（二期）：常驻进程每 10 秒扫库，按阈值越限 + 设备离线生成告警并触发短信；同 (设备,参数,类型) 10 分钟冷却防刷屏，规则与前端 v1 逐条对齐（`tests/` 有从前端移植的语义测试）。
- **app/sms.py** — 短信模块：文案生成 + 可插拔发送器，`.env` 里 `SMS_PROVIDER` 切换 `dryrun`（日志模拟，默认）/ `aliyun`（预留）。
- **app/monitor_core.py** — api 与 detector 的共享定义（设备清单、默认阈值、判定常量、告警读写）。
- **app/api.py** — REST API（FastAPI），交互文档见 `http://127.0.0.1:8000/docs`。
- **app/db.py** — 数据库连接公共模块，连接配置从环境变量 / 根目录 `.env` 读取。
- **app/log.py** — 日志模块（loguru）：统一时间/级别/位置格式，日志文件超 10MB 自动轮转、旧文件保留 7 天。
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

# 4. 启动各服务（互相独立，按需启动；都在仓库根目录执行）
./scripts/start_consumer.sh   # MQTT 采集端
./scripts/start_detector.sh   # 异常检测 + 短信
./scripts/start_api.sh        # REST API（端口 8000）
./scripts/start_client.sh     # 模拟发送端（真实环境用真设备，不需要它）
```

停止用对应的 `scripts/stop_*.sh`；日志在 `logs/` 目录（如 `tail -f logs/consumer.log`），由 loguru 管理：超 10MB 自动轮转、旧文件保留 7 天；`*.err` 平时为空，只在进程启动失败/崩溃时有内容。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/devices` | 设备列表（条数、最后上报时间） |
| GET | `/api/devices/{id}/latest` | 单设备最新一条（无数据 404） |
| GET | `/api/devices/{id}/data` | 单设备历史区间（升序，默认近 24h，limit 1~10000） |
| GET | `/api/readings/latest` | 4 台设备最新读数快照（无数据为 null） |
| GET / POST / PUT / DELETE | `/api/alarms` | 告警记录存储（后端判定生成，前端只读+删除，上限 500 条） |
| GET / PUT | `/api/thresholds` | 阈值规则（4 设备 × 6 参数，空表返回默认值） |
| GET / POST / DELETE | `/api/receivers` | 短信接收人 |
| GET | `/api/detector/status` | 检测器心跳（60 秒内扫过视为运行中） |
| POST | `/api/devices/{id}/simulate-offline` | 演示用离线开关（空实现） |

监控参数：`Sf` 沉降速度 / `Vf` 沉降比 / `Fc` 流量 / `pHf` pH / `Tf` 温度 / `Cf` 余氯。

## 说明

- **告警判定与短信由后端 `app/anomaly_detector.py` 常驻完成**（浏览器关闭不影响），前端只做展示；判定语义与前端 v1 完全一致，设计细节见 `docs/superpowers/specs/`。
- 短信默认 dryrun（日志可见、实际不发）；`.env` 改 `SMS_PROVIDER=aliyun` 并填好阿里云四项配置即真实发送。
- 三期规划：五项时序检查（spike/stuck/gap/sustained_deviation）+ anomalies 表。
- 接口契约与联调记录见前端仓库 `docs/backend-api.md`。
- 测试：`python3 -m pytest`（判定/冷却/短信语义，移植自前端 spec）。
