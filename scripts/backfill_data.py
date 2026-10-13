# 一次性补数工具：绕过 MQTT 直接向 sensor_data 表回填模拟数据（前端没数据展示时用）
# 用法（仓库根目录执行）：python scripts/backfill_data.py [--hours 48] [--interval 13]
# 时间点对齐到固定网格（interval 分钟一格），重复执行撞 (device_id, reported_at)
# 唯一键自动跳过，不会插重，可以放心多跑
import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根目录进 sys.path，才能 import app

from app.db import get_db_conn  # 数据库连接配置走根目录 .env，与各服务一致

DEVICE_IDS = [f"Di-Jiu-Shui-Chang-{i}" for i in range(1, 5)]

# 基础值与 app/mqtt_client.py 一致：取默认阈值带中间值，±10% 波动后仍在带内，不触发告警
BASE_DATA = {
    "sf": 2.0,   # 沉降速度 m/h（阈值 0.5~3.5）
    "vf": 20,    # 沉降比 %（阈值 5~35）
    "fc": 1150,  # 流量 m³/h（阈值 800~1500）
    "phf": 7.5,  # pH（阈值 6.5~8.5）
    "tf": 19,    # 温度 ℃（阈值 8~30）
    "cf": 0.6,   # 余氯 mg/L（阈值 0.2~1.0）
}

# 与 consumer.save_to_mysql 相同的幂等插入：撞唯一键时哑更新跳过
SQL = """INSERT INTO sensor_data
         (device_id, topic, sf, vf, fc, phf, tf, cf, settling_ratio, reported_at)
         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
         ON DUPLICATE KEY UPDATE id = id"""

GRID_ANCHOR = datetime(2020, 1, 1)  # 固定锚点：不同时间执行生成相同网格，保证幂等


def jitter():
    """基础值 ±10% 随机波动（round 两位，与模拟器 jittered_data 一致）"""
    return {col: round(v * (1 + random.uniform(-0.1, 0.1)), 2) for col, v in BASE_DATA.items()}


def main():
    parser = argparse.ArgumentParser(description="回填模拟数据到 sensor_data")
    parser.add_argument("--hours", type=int, default=48, help="往回补多少小时（默认 48）")
    parser.add_argument("--interval", type=int, default=13, help="上报间隔分钟（默认 13，与真实设备一致）")
    args = parser.parse_args()

    step_min = args.interval
    # 网格点：从 anchor 起每 interval 分钟一格，取最近 hours 小时内的所有格点
    now_tick = int((datetime.now() - GRID_ANCHOR).total_seconds() // (step_min * 60))
    end = GRID_ANCHOR + timedelta(minutes=now_tick * step_min)
    earliest = end - timedelta(hours=args.hours)
    points = []
    tick = now_tick
    while True:
        ts = GRID_ANCHOR + timedelta(minutes=tick * step_min)
        if ts < earliest:
            break
        points.append(ts)
        tick -= 1

    rows = []
    for device_id in DEVICE_IDS:
        for ts in points:
            v = jitter()
            rows.append((device_id, f"Chen-Su-Yi/{device_id}",
                         v["sf"], v["vf"], v["fc"], v["phf"], v["tf"], v["cf"], None, ts))

    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.executemany(SQL, rows)
        inserted = cur.rowcount
    print(f"完成：{len(DEVICE_IDS)} 台设备 x {len(points)} 个时间点"
          f"（{points[-1]:%m-%d %H:%M} 往回到 {points[0]:%m-%d %H:%M}，每 {step_min} 分钟一条）"
          f"共 {len(rows)} 条，新插入 {inserted} 条，其余撞唯一键跳过")


if __name__ == "__main__":
    main()
