#!/bin/bash
# 启动异常检测器（后台运行，关终端不影响；配置读 .env，如 SMS_PROVIDER）
# 正式日志写 logs/detector.log（loguru 轮转）；logs/detector.err 只是兜底（平时为空）
cd "$(dirname "$0")/.."
if pgrep -f "python.*app.anomaly_detector" > /dev/null; then
    echo "detector 已在运行 (PID $(pgrep -f 'python.*app.anomaly_detector'))"
    exit 0
fi
mkdir -p logs
nohup python3 -u -m app.anomaly_detector > logs/detector.err 2>&1 &
PID=$!
sleep 1
if ! kill -0 "$PID" 2>/dev/null; then
    echo "❌ detector 启动失败，看 logs/detector.err"
    exit 1
fi
echo "detector 已启动，PID $PID"
echo "看日志: tail -f $(pwd)/logs/detector.log"
