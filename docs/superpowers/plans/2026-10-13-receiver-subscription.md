# 接收人按设备订阅(三期)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 告警短信从 receivers 全员广播改为按设备订阅路由,并给 API 加 PUT 端点。

**Architecture:** receivers 表加 `subscribed_devices` JSON 列;`monitor_core.load_receivers` 返回 `[{phone, devices}]`;检测器每条告警经纯函数 `recipients_for` 现算收件人;api.py 的 GET/POST 加字段、新增 PUT。判定/冷却/文案/`settle_sms` 状态机零改动。

**Tech Stack:** Python 3.11 / FastAPI / PyMySQL / pytest,零新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-13-receiver-subscription-design.md`(契约细节见前端仓库 `docs/backend-api.md` §8,已提交)

## Global Constraints

- **无订阅(NULL/空数组)= 收不到任何短信**,显式订阅才收(用户拍板,不是全员兜底)
- 某设备无人订阅时告警照常落库,`sms.status` 沿用 `failed`,**不新增状态值**
- 设备 id 校验:必须 ∈ `SNAPSHOT_DEVICES`(Di-Jiu-Shui-Chang-1..4),非法 → **400**(区别于手机号 422)
- `PUT /api/receivers/{id}` 三字段必传(缺了 422),id 不存在 **404**;DELETE 幂等语义不变
- **禁止给 MySQL 连接加 CLIENT_FOUND_ROWS**:会破坏 `mqtt_consumer.py:57` 用 rowcount==0 判"重复"的幂等逻辑
- PyMySQL 读 JSON 列返回的是**字符串**,必须 `json.loads`(仓库先例:`anomaly_detector.py:122` 解析 alarms.payload)
- API 字段名 camelCase(`subscribedDevices`),对齐 backend-api.md §4.5
- 测试只做纯函数级(pytest),不建 API 自动化、不引依赖

## Review Focus

实现时最容易踩、且各任务测试未必覆盖的点(对应测试已钉进所属任务):

1. **JSON 列读出来是 str 不是 list**——直接 `list(r[1])` 会把字符串拆成单字符 → Task 2 的 `test_parse_devices_null_corrupt_and_normal` 钉死
2. **PUT 用 UPDATE rowcount==0 判 404 会被"数据没变化"误伤**(MySQL 默认 rowcount 只算真正改动的行)→ 先 SELECT 存在性,Task 3 的 curl 验证步骤含"PUT 相同数据应 200"
3. **手改库导致 subscribed_devices 是损坏 JSON**——不能让一轮扫描抛异常停发所有短信 → `parse_devices` 兜底 [],Task 2 测试钉死
4. **路由只按 device_id,离线告警(param_key=None)必须同样路由** → `recipients_for` 只看 device_id,Task 2 测试注释说明
5. **无人订阅时告警必须仍落库**(不能因没人订阅吞告警)→ Task 4 E2E 验证 3# 告警存在且 failed

---

### Task 1: receivers 表加 subscribed_devices 列

**Files:**
- Modify: `schema.sql`(receivers 建表语句)
- 无代码、无测试,产出是库结构变更

**Interfaces:**
- Produces: `receivers.subscribed_devices`(JSON,可 NULL)——Task 2/3 的 SQL 都依赖它存在

- [ ] **Step 1: schema.sql 的 receivers 表加列**

把(schema.sql:36-41):

```sql
CREATE TABLE `receivers` (
  `id` varchar(32) NOT NULL,
  `name` varchar(64) NOT NULL,
  `phone` varchar(16) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='短信接收人';
```

改为:

```sql
CREATE TABLE `receivers` (
  `id` varchar(32) NOT NULL,
  `name` varchar(64) NOT NULL,
  `phone` varchar(16) NOT NULL,
  `subscribed_devices` json DEFAULT NULL COMMENT '订阅设备 id 数组，空/NULL=不收短信',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='短信接收人';
```

- [ ] **Step 2: 本地存量库执行 ALTER**

走 app.db 复用 .env 的连接配置(autocommit=True,DDL 即时生效),不碰命令行密码:

```bash
python3 -c "
from app.db import get_db_conn
conn = get_db_conn()
with conn.cursor() as cur:
    cur.execute(\"ALTER TABLE receivers ADD COLUMN subscribed_devices json NULL COMMENT '订阅设备 id 数组,空/NULL=不收短信' AFTER phone\")
print('ALTER OK')"
```

- [ ] **Step 3: 验证列已存在**

```bash
python3 -c "
from app.db import get_db_conn
conn = get_db_conn()
with conn.cursor() as cur:
    cur.execute(\"SHOW COLUMNS FROM receivers LIKE 'subscribed_devices'\")
    print(cur.fetchall())"
```

Expected: 一行,Type 为 `json`,Null 为 `YES`。

- [ ] **Step 4: Commit**

```bash
git add schema.sql
git commit -m "三期：receivers 表加 subscribed_devices JSON 列（本库已 ALTER）"
```

---

### Task 2: 检测器按设备订阅路由

**Files:**
- Modify: `app/monitor_core.py:67-72`(`load_receivers` 改造 + 新增 `parse_devices`)
- Modify: `app/anomaly_detector.py`(`scan_once` 接线 + 新增 `recipients_for`)
- Test: `tests/test_detector.py`(短信小节后追加)

**Interfaces:**
- Produces: `monitor_core.parse_devices(raw: str|list|None) -> list[str]`(Task 3 的 GET 端点复用);`anomaly_detector.recipients_for(device_id: str, receivers: list[dict]) -> list[str]`
- Consumes: Task 1 的 `subscribed_devices` 列;`settle_sms(record, phones, sender)` 签名不变

- [ ] **Step 1: 写失败测试**

`tests/test_detector.py` 顶部 import 行改为:

```python
from app.anomaly_detector import judge_device, plan_alarms, cooldown_key, recipients_for, OFFLINE_AFTER_SECONDS
from app.monitor_core import parse_devices
from app.sms import build_sms_text, settle_sms
```

文件末尾(短信文案小节之后)追加:

```python
# ---------- 三期：按设备订阅路由 ----------

def test_parse_devices_null_corrupt_and_normal():
    """JSON 列经 PyMySQL 读出是字符串；NULL/空/损坏一律兜底 []（别让一条脏数据停掉一轮扫描）"""
    assert parse_devices(None) == []
    assert parse_devices("") == []
    assert parse_devices('["Di-Jiu-Shui-Chang-1","Di-Jiu-Shui-Chang-3"]') == ["Di-Jiu-Shui-Chang-1", "Di-Jiu-Shui-Chang-3"]
    assert parse_devices("{oops") == []
    assert parse_devices('{"a":1}') == []  # 合法 JSON 但不是数组

def test_recipients_for_routes_by_device():
    """越限与离线告警同一路由：recipients_for 只看 device_id（离线告警 param_key=None 不影响）"""
    receivers = [
        {"phone": "13800000001", "devices": ["Di-Jiu-Shui-Chang-1"]},
        {"phone": "13800000002", "devices": ["Di-Jiu-Shui-Chang-2", "Di-Jiu-Shui-Chang-3"]},
        {"phone": "13800000003", "devices": []},
    ]
    # 订阅者收到 / 未订阅排除 / 空订阅无人 / 混合各取各的
    assert recipients_for("Di-Jiu-Shui-Chang-1", receivers) == ["13800000001"]
    assert recipients_for("Di-Jiu-Shui-Chang-3", receivers) == ["13800000002"]
    assert recipients_for("Di-Jiu-Shui-Chang-4", receivers) == []
    assert recipients_for("Di-Jiu-Shui-Chang-1", []) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_detector.py -v
```

Expected: FAIL,`ImportError: cannot import name 'parse_devices'`。

- [ ] **Step 3: 实现 monitor_core**

`app/monitor_core.py:67-72` 的 `load_receivers` 整体替换为:

```python
def parse_devices(raw):
    """subscribed_devices 列（JSON 字符串）转 list；NULL/空/损坏一律 []，别让一条脏数据停掉一轮扫描"""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []

def load_receivers():
    """全部接收人及订阅 [{phone, devices}]；告警只发订阅了对应设备的人（三期 §8，空订阅=收不到）"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT phone, subscribed_devices FROM receivers")
        rows = cur.fetchall()
    return [{"phone": r[0], "devices": parse_devices(r[1])} for r in rows]
```

(`monitor_core.py` 顶部已有 `import json`,`upsert_alarms` 在用。)

- [ ] **Step 4: 实现 anomaly_detector 接线**

`app/anomaly_detector.py` 在 `plan_alarms` 之后、"以下为扫描流程"注释之前加:

```python
def recipients_for(device_id, receivers):
    """一条告警的短信收件人：订阅了该设备的接收人（越限/离线同一路由，只看 device_id）"""
    return [r["phone"] for r in receivers if device_id in r["devices"]]
```

`scan_once` 里(`anomaly_detector.py:159`)`phones = load_receivers()` 改为 `receivers = load_receivers()`;发送循环里(`anomaly_detector.py:176`)`settle_sms(a, phones, sender)` 改为:

```python
        settle_sms(a, recipients_for(a["device_id"], receivers), sender)
```

- [ ] **Step 5: 跑全部测试确认通过**

```bash
python3 -m pytest -v
```

Expected: 全部 PASS(原 13 条 + 新 2 条)。

- [ ] **Step 6: Commit**

```bash
git add app/monitor_core.py app/anomaly_detector.py tests/test_detector.py
git commit -m "三期：检测器短信按设备订阅路由（load_receivers 带 devices + recipients_for 过滤）"
```

---

### Task 3: API 三端点 + README

**Files:**
- Modify: `app/api.py:16`(import)、`:22`(版本号)、`:205-233`(接收人端点)
- Modify: `README.md`(API 一览表 receivers 行)

**Interfaces:**
- Consumes: Task 1 的列;Task 2 的 `parse_devices`
- Produces: `GET /api/receivers` 返回含 `subscribedDevices`;`POST` 可选该字段;`PUT /api/receivers/{id}` 全量更新

- [ ] **Step 1: api.py 顶部两处小改**

import 行(api.py:16)改为:

```python
from app.monitor_core import SNAPSHOT_DEVICES, default_thresholds, upsert_alarms, parse_devices
```

版本号(api.py:22)`version="0.3.0"` 改为 `version="0.4.0"`。

- [ ] **Step 2: 改造接收人端点**

`app/api.py:205-233` 的 `ReceiverIn` 与三个端点整体替换为:

```python
class ReceiverIn(BaseModel):
    name: str
    phone: str = Field(pattern=r"^1\d{10}$")  # 后端兜底校验，前端已校验
    subscribedDevices: List[str] = []  # 设备级订阅，默认空 = 不收短信

class ReceiverFullIn(ReceiverIn):
    """PUT 全量更新：subscribedDevices 必传（覆盖父类默认值，缺了 422）"""
    subscribedDevices: List[str]

def _check_devices(devices):
    """subscribedDevices 兜底校验：设备 id 必须 ∈ 四台清单，否则 400（契约 §4.5）"""
    bad = [d for d in devices if d not in SNAPSHOT_DEVICES]
    if bad:
        raise HTTPException(status_code=400, detail=f"未知设备 id: {bad}")

@app.get("/api/receivers")
async def list_receivers():
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, phone, subscribed_devices FROM receivers ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "phone": r[2],
             "subscribedDevices": parse_devices(r[3])} for r in rows]

@app.post("/api/receivers")
async def add_receiver(receiver: ReceiverIn):
    _check_devices(receiver.subscribedDevices)
    rec = {"id": f"rcv-{uuid4().hex[:8]}", "name": receiver.name,
           "phone": receiver.phone, "subscribedDevices": receiver.subscribedDevices}
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO receivers (id, name, phone, subscribed_devices) VALUES (%s, %s, %s, %s)",
            (rec["id"], rec["name"], rec["phone"], json.dumps(rec["subscribedDevices"])),
        )
    return rec

@app.put("/api/receivers/{receiver_id}")
async def update_receiver(receiver_id: str, body: ReceiverFullIn):
    """全量更新。先 SELECT 存在性再 UPDATE——UPDATE 值未变时 rowcount 也是 0，不能拿它判 404"""
    _check_devices(body.subscribedDevices)
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM receivers WHERE id = %s", (receiver_id,))
        if cur.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="接收人不存在")
        cur.execute(
            "UPDATE receivers SET name = %s, phone = %s, subscribed_devices = %s WHERE id = %s",
            (body.name, body.phone, json.dumps(body.subscribedDevices), receiver_id),
        )
    return {"id": receiver_id, "name": body.name, "phone": body.phone,
            "subscribedDevices": body.subscribedDevices}

@app.delete("/api/receivers/{receiver_id}")
async def delete_receiver(receiver_id: str):
    """幂等删除，id 不存在也返回 200"""
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM receivers WHERE id = %s", (receiver_id,))
    return {}
```

- [ ] **Step 3: README 的 API 一览表 receivers 行更新**

`README.md` 的 `| GET / POST / DELETE | /api/receivers | 短信接收人 |` 改为 `| GET / POST / PUT / DELETE | /api/receivers | 短信接收人（设备订阅路由） |`。

- [ ] **Step 4: 重启 API 并按序 curl 验证**

```bash
./scripts/stop_api.sh; ./scripts/start_api.sh
# 1) POST 带订阅
curl -s -X POST http://127.0.0.1:8000/api/receivers -H 'Content-Type: application/json' \
  -d '{"name":"测试A","phone":"13800000001","subscribedDevices":["Di-Jiu-Shui-Chang-1"]}'
# 2) POST 不带订阅(默认 []) + GET 应看到两条,分别 subscribedDevices=[1#] 和 []
curl -s -X POST http://127.0.0.1:8000/api/receivers -H 'Content-Type: application/json' \
  -d '{"name":"测试B","phone":"13800000002"}'
curl -s http://127.0.0.1:8000/api/receivers
# 3) PUT 全量更新(把 B 订阅 2#)
curl -s -X PUT http://127.0.0.1:8000/api/receivers/<B的id> -H 'Content-Type: application/json' \
  -d '{"name":"测试B","phone":"13800000002","subscribedDevices":["Di-Jiu-Shui-Chang-2"]}'
# 4) PUT 相同数据再发一次 → 必须 200(不是 404,验证 rowcount 坑)
# 5) PUT 不存在的 id → 404
curl -s -o /dev/null -w "%{http_code}\n" -X PUT http://127.0.0.1:8000/api/receivers/rcv-notexist \
  -H 'Content-Type: application/json' -d '{"name":"x","phone":"13800000003","subscribedDevices":[]}'
# 6) 非法设备 id → 400
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/receivers \
  -H 'Content-Type: application/json' -d '{"name":"x","phone":"13800000003","subscribedDevices":["Di-Jiu-Shui-Chang-9"]}'
# 7) 坏手机号 → 422(原有行为不变)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/receivers \
  -H 'Content-Type: application/json' -d '{"name":"x","phone":"123","subscribedDevices":[]}'
# 8) DELETE 幂等
curl -s -X DELETE http://127.0.0.1:8000/api/receivers/<A的id>
```

Expected: 依次为带订阅对象、两条记录(一条 `[]`)、更新后对象、200、404、400、422、`{}`。全部符合后进下一步。

- [ ] **Step 5: 回归测试**

```bash
python3 -m pytest -v
```

Expected: 全部 PASS(测试不碰 DB,与 API 改动无耦合,确认无意外破坏)。

- [ ] **Step 6: Commit**

```bash
git add app/api.py README.md
git commit -m "三期：接收人 API 加设备订阅（GET/POST 带字段、新增 PUT 全量更新，v0.4.0）"
```

---

### Task 4: E2E 联调验证与清理(无代码改动,最终验收门)

**Files:** 无(只跑进程和 curl;需要两个终端窗口)

**Interfaces:**
- Consumes: Task 1-3 全部产出;`scripts/backfill_data.py`(幂等补数)

- [ ] **Step 1: 清场 + 起新数据 + 起服务**

```bash
python3 -c "
from app.db import get_db_conn
conn = get_db_conn()
with conn.cursor() as cur:
    cur.execute('DELETE FROM alarms'); cur.execute('DELETE FROM receivers'); cur.execute('DELETE FROM thresholds')
print('cleaned')"
python3 scripts/backfill_data.py --hours 1   # 四台设备各来 5 条新鲜读数
```

终端 1 起 API(`./scripts/start_api.sh`),终端 2 起检测器(测试用超长离线窗口,避免补数格点恰好超 13 分钟被误判离线):

```bash
DETECTOR_OFFLINE_SECONDS=86400 python3 -m app.anomaly_detector
```

- [ ] **Step 2: 配订阅 + 制造越限**

A 只订 1#,B 只订 2#;把 1# 和 3# 的 Vf 下限调到 25(基线 20±10%,必低于下限):

```bash
curl -s -X POST http://127.0.0.1:8000/api/receivers -H 'Content-Type: application/json' \
  -d '{"name":"A","phone":"13800000001","subscribedDevices":["Di-Jiu-Shui-Chang-1"]}'
curl -s -X POST http://127.0.0.1:8000/api/receivers -H 'Content-Type: application/json' \
  -d '{"name":"B","phone":"13800000002","subscribedDevices":["Di-Jiu-Shui-Chang-2"]}'
python3 -c "
import json, urllib.request
rules = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/thresholds'))
for r in rules:
    if r['paramKey'] == 'Vf' and r['deviceId'] in ('Di-Jiu-Shui-Chang-1', 'Di-Jiu-Shui-Chang-3'):
        r['low'] = 25
req = urllib.request.Request('http://127.0.0.1:8000/api/thresholds',
    data=json.dumps(rules).encode(), headers={'Content-Type': 'application/json'}, method='PUT')
urllib.request.urlopen(req)
print('thresholds set')"
```

- [ ] **Step 3: 等 15 秒后验收**

```bash
curl -s http://127.0.0.1:8000/api/alarms
tail -20 logs/detector.log
```

Expected(Review Focus #5 在此钉死):

- 1# 的 Vf-low warning:`sms.receivers == ["13800000001"]`、`status == "sent"`(dryrun 视为成功)
- 3# 的 Vf-low warning:`sms.receivers == []`、`status == "failed"`——**告警存在,没人订阅不吞**
- 2#、4# 无告警
- `logs/detector.log` 有 `[SMS-dryrun] → 13800000001`,且**没有** `→ 13800000002`

- [ ] **Step 4: 清场收工**

```bash
python3 -c "
from app.db import get_db_conn
conn = get_db_conn()
with conn.cursor() as cur:
    cur.execute('DELETE FROM alarms'); cur.execute('DELETE FROM receivers'); cur.execute('DELETE FROM thresholds')
print('cleaned')"
```

两个终端 Ctrl+C 停掉检测器与 API(`./scripts/stop_api.sh`)。无代码改动,无需 commit。

---

## 收尾(不在任务内,交付时提醒用户)

Windows 生产库要执行 Task 1 Step 2 的同款 ALTER,并按需跑设计文档里的预填 SQL(存量接收人先收全部再逐个调),然后 `git pull` 重启三个服务。
