# 三期设计：短信按设备订阅路由（2026-10-13）

## 背景与目标

当前 receivers 表里所有人收所有设备的告警（`load_receivers` 全量捞手机号）。实际需要按设备分人管：订阅了哪台设备，就只收那台的告警。本期只改"一条告警发给谁"的路由，判定、冷却、文案、短信状态机零改动。

粒度：**设备级**。参数级订阅本期不做（JSON 结构将来可平滑扩展，不动表）。

订阅语义（产品决策）：**显式订阅才收**。`subscribed_devices` 为 NULL 或空数组 = 收不到任何短信。

## 数据模型

```sql
-- schema.sql 同步更新（新装环境）；存量库执行：
ALTER TABLE receivers ADD COLUMN subscribed_devices JSON NULL;
```

- 存设备 id 数组：`["Di-Jiu-Shui-Chang-1", "Di-Jiu-Shui-Chang-3"]`，值必须 ∈ 4 台设备清单（`SNAPSHOT_DEVICES`）
- 存量行 ALTER 后为 NULL → 收不到。部署时可选预填全部设备再逐人调整：

```sql
UPDATE receivers SET subscribed_devices =
  JSON_ARRAY('Di-Jiu-Shui-Chang-1', 'Di-Jiu-Shui-Chang-2',
             'Di-Jiu-Shui-Chang-3', 'Di-Jiu-Shui-Chang-4')
WHERE subscribed_devices IS NULL;
```

## 检测器路由（唯一逻辑改动）

- `monitor_core.load_receivers()` 返回从 `[手机号]` 改为 `[{phone, devices}]`（已确认仅 `anomaly_detector.py` 一处调用，api.py 的 receivers 端点直查表不受影响）
- `scan_once` 每条告警现算收件人：

```python
receivers = load_receivers()   # 每轮一次
phones = [r["phone"] for r in receivers if a["device_id"] in r["devices"]]
settle_sms(a, phones, sender)  # 状态机不动
```

- 越限告警与离线告警都按告警的 `device_id` 路由，规则天然一致
- 告警记录 `sms.receivers` = 实际收件人（订阅了该设备的人），契约不变
- 语义点：某设备没人订阅时走现有"无接收人 → `sms.status=failed`"路径，**不新增状态值**（避免破坏前端契约）。`failed` 含"没人订阅"这种情况，接口文档注明

## API 契约（backend-api.md receivers 部分同步更新）

| 端点 | 改动 |
|---|---|
| `GET /api/receivers` | 返回加 `subscribedDevices: string[]`（库里 NULL 显示为 `[]`） |
| `POST /api/receivers` | 入参加 `subscribedDevices`（可选，默认 `[]` = 收不到） |
| `PUT /api/receivers/{id}` **新增** | 全量更新 `name` / `phone` / `subscribedDevices`（前端勾选框保存用） |

校验沿用现有兜底风格：手机号 `^1\d{10}$` 不变；`subscribedDevices` 内设备 id 不在 4 台清单内 → 400。字段名 camelCase，与 thresholds 接口风格一致。

## 测试

`tests/test_detector.py` 补订阅路由用例（纯函数级）：订阅者收到 / 未订阅者排除 / 空订阅无人收 / 混合接收人各取各的 / 离线告警同样按设备路由。API 校验不建自动化（仓库无 API 测试基建，不为此引依赖），人工联调覆盖。

## 前端配合（sc-front，另仓库）

接收人管理页每人加 4 个设备勾选框（新建走 POST 带上、编辑走新 PUT）；`docs/backend-api.md` receivers 部分按上表更新。告警页展示不动（`sms.receivers` 仍是实际收件人列表）。

## 明确不做（本期）

- 参数级订阅
- 新增 sms 状态值（无人订阅沿用 failed）
- 判定 / 冷却 / 文案 / 短信状态机改动
