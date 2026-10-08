#!/bin/bash
# 启动常驻 consumer：后台运行，关掉终端也活着，日志写 logs/consumer.log
cd "$(dirname "$0")/.."
if pgrep -f "python.*app.mqtt_consumer" > /dev/null; then
    echo "consumer 已在运行 (PID $(pgrep -f 'python.*app.mqtt_consumer'))"
    exit 0
fi
mkdir -p logs
nohup python3 -u -m app.mqtt_consumer > logs/consumer.log 2>&1 &
echo "consumer 已启动，PID $!"
echo "看日志: tail -f $(pwd)/logs/consumer.log"
