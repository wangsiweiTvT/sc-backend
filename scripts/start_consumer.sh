#!/bin/bash
# 启动常驻 consumer：后台运行，关掉终端也活着
# 正式日志写 logs/consumer.log（loguru 轮转）；logs/consumer.err 只是兜底（进程崩溃时才有内容，平时为空）
cd "$(dirname "$0")/.."
if pgrep -f "python.*app.mqtt_consumer" > /dev/null; then
    echo "consumer 已在运行 (PID $(pgrep -f 'python.*app.mqtt_consumer'))"
    exit 0
fi
mkdir -p logs
nohup python3 -u -m app.mqtt_consumer > logs/consumer.err 2>&1 &
PID=$!
sleep 1
if ! kill -0 "$PID" 2>/dev/null; then
    echo "❌ consumer 启动失败，看 logs/consumer.err"
    exit 1
fi
echo "consumer 已启动，PID $PID"
echo "看日志: tail -f $(pwd)/logs/consumer.log"
