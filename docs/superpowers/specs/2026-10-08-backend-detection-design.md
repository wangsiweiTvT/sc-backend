# 二期设计：告警判定与短信迁移到后端（2026-10-08）

## 背景与目标

v1 中异常判定和短信模拟跑在前端浏览器里，浏览器一关检测就停。二期把两者移到后端常驻进程，规则与前端 v1 完全对齐（语义由前端 `src/core/*.spec.ts` 钉死，移植时以测试为准），前端零改动能继续展示告警；高级时序分析（spike/stuck/gap/sustained_deviation 五项检查）留三期。

## 架构

```
anomaly_detector.py（新，独立进程，start_detector.sh 启动，默认每 10 秒一轮）
  直查 sensor_data 各设备最新一条
  → 判定 → 新告警写入 alarms 表（沿用现有 JSON 格式）
  → 短信（sms.py 可插拔）→ 回写 sms.status
```

- 新文件：`anomaly_detector.py`、`sms.py`、`monitor_core.py`（共享模块）、`start/stop_detector.sh`、`tests/test_detector.py`
- 共享模块 `monitor_core.py`：4 台设备清单、默认阈值、告警 upsert+裁剪 500、参数/设备中文元数据、判定常量。api.py 改为从这里 import，消除双份定义
- 不新建告警相关表；去重 = 查 alarms 最近 10 分钟记录算键集合
- 新增 `detector_status` 心跳表（一行）+ `GET /api/detector/status`，无人值守进程可观测

## 判定规则（与前端逐条对齐，等价移植）

| 规则 | 语义 |
|---|---|
| 越限比较 | 严格 `> high` / `< low`，等于边界不算；high 优先于 low |
| disabled 规则 | 跳过；参数缺失（值为 null）跳过 |
| 等级 | 越限一律 `warning`；仅整机离线 `critical` |
| 离线判定 | 最新数据距今严格 > 13 分钟（默认 780s，`DETECTOR_OFFLINE_SECONDS` 可覆盖，仅供测试）或无数据；时间基准为服务器时间 |
| 离线告警 | 边沿触发：上一轮非离线 → 本轮离线才报；进程启动首轮（prev 未知）不报；恢复上线不产生记录 |
| 防刷屏冷却 | 同 `(device_id, param_key, type)` 三元组在 10 分钟（严格 < 600000ms）内有告警即跳过；离线键 param_key 用 None；与是否恢复无关；跨参数/跨方向独立 |
| 告警记录 | `id = alarm-<毫秒>-<进程内递增序号>`，字段与现有 POST 格式完全一致（time 为毫秒时间戳） |
| 裁剪 | alarms 表保持最新 500 条（现有 SQL 复用） |
| 判定范围 | 仅 `Di-Jiu-Shui-Chang-1..4`（与前端适配层一致） |

与前端唯一已知差异：扫描间隔 2 秒 → 10 秒（`DETECTOR_POLL_SECONDS` 可调），冷却到期重报最多延后一个扫描周期。

## 短信模块（sms.py）

- `SMS_PROVIDER=dryrun`（默认）：完整状态机，日志打印文案，实际不发；无接收人 → 立即 `failed` 且不记 sent_at；有接收人 → `pending` → 发送 → `sent`/`failed` + `sent_at`（毫秒）
- `SMS_PROVIDER=aliyun`（预留）：SendSms RPC（HMAC-SHA1 签名，urllib 标准库实现，无新依赖）；`.env` 配 `SMS_ALIYUN_ACCESS_KEY_ID/SECRET/SIGN_NAME/TEMPLATE_CODE` 即真发。**账号未办，此路径未实测**，代码内标注
- 文案对齐前端 buildSmsText：`【水厂监控】九厂一期-1# 沉降比 38.0%，超上限 35%，请及时处理。`；low 为"低于下限"；离线为"设备离线超过13分钟"
- 接收人：receivers 表全部手机号（不按设备区分），与前端一致

## 流程（每轮扫描）

1. 心跳写 detector_status（last_scan_at、scans_count+1）
2. 读 thresholds（空表用默认值）、receivers、各设备最新读数
3. 判定 → 生成告警记录（经冷却去重）→ upsert 进 alarms + 裁剪 500
4. 对每条新告警走短信状态机 → upsert 回写整条记录
5. 下一轮

## 测试

- pytest 移植前端 spec 关键用例：冷却边界（恰好 10 分钟允许重报）、跨参数/跨方向独立、离线边沿（含首轮不报）、严格比较、等级映射、空接收人 failed
- E2E：调低阈值 → warning + dryrun 短信日志；短离线阈值 + 停发送端 → critical；重启检测器首轮不补报
- 依赖：pytest（requirements-dev.txt），运行时零新依赖

## 前端配合（backend-api.md §7 交接）

前端删除本地判定与短信模拟（否则双份告警）；告警页/铃铛改轮询 `GET /api/alarms`（建议 30~60s）；不再 POST/PUT 告警（DELETE 清空保留）；阈值/接收人页不变（改动约 10 秒内生效）；状态角标（在线/异常/离线）可继续前端自算，属展示不算告警。

## 明确不做（本期）

- 五项时序检查 + anomalies 表（三期）
- simulate-offline 真实现（前端无入口，维持空实现）
- 阿里云实测（等账号）
