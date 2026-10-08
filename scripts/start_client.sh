#!/bin/bash
# 启动定时发送端：每 13 分钟发一条（后台运行，关终端不影响）
cd "$(dirname "$0")/.."
if pgrep -f "python.*app.mqtt_client" > /dev/null; then
    echo "sender 已在运行 (PID $(pgrep -f 'python.*app.mqtt_client'))"
    exit 0
fi
mkdir -p logs
nohup python3 -u -m app.mqtt_client > logs/mqtt_client.log 2>&1 &
echo "sender 已启动，PID $!"
echo "看日志: tail -f $(pwd)/logs/mqtt_client.log"
