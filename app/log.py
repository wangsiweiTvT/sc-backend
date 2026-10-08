# 日志辅助：给输出统一加本地时间前缀（nohup 落到 logs/*.log 后没有时间，排查时对不上）
# 例：log("📤 发布成功") -> [2026-10-08 15:02:57] 📤 发布成功
from datetime import datetime


def log(*args, **kwargs):
    """print 的直通版，只是行首多了 [时间]；其余参数（如 flush）原样传给 print"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]", *args, **kwargs)
