#!/bin/bash
# 启动异常检测器（后台运行，关终端不影响；配置读 .env，如 SMS_PROVIDER）
cd "$(dirname "$0")"
if pgrep -f "python.*anomaly_detector" > /dev/null; then
    echo "detector 已在运行 (PID $(pgrep -f 'python.*anomaly_detector'))"
    exit 0
fi
nohup python3 -u anomaly_detector.py > detector.log 2>&1 &
echo "detector 已启动，PID $!"
echo "看日志: tail -f $(pwd)/detector.log"
