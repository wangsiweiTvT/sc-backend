#!/bin/bash
# 启动定时发送端：每 13 分钟发一条（后台运行，关终端不影响）
cd "$(dirname "$0")"
if pgrep -f "python.*mqtt_client" > /dev/null; then
    echo "sender 已在运行 (PID $(pgrep -f 'python.*mqtt_client'))"
    exit 0
fi
nohup python3 -u mqtt_client.py > mqtt_client.log 2>&1 &
echo "sender 已启动，PID $!"
echo "看日志: tail -f $(pwd)/mqtt_client.log"
