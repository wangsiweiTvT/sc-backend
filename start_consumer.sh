#!/bin/bash
# 启动常驻 consumer：后台运行，关掉终端也活着，日志写 consumer.log
cd "$(dirname "$0")"
if pgrep -f "python.*mqtt_consumer" > /dev/null; then
    echo "consumer 已在运行 (PID $(pgrep -f 'python.*mqtt_consumer'))"
    exit 0
fi
nohup python3 -u mqtt_consumer.py > consumer.log 2>&1 &
echo "consumer 已启动，PID $!"
echo "看日志: tail -f $(pwd)/consumer.log"
