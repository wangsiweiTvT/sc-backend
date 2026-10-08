#!/bin/bash
# 启动 REST API（FastAPI/uvicorn，后台运行，关终端不影响）
# 正式日志（含 uvicorn 请求日志）写 logs/api.log（loguru 轮转）；logs/api.err 只是兜底（平时为空）
cd "$(dirname "$0")/.."
if pgrep -f "uvicorn app.api:app" > /dev/null; then
    echo "api 已在运行 (PID $(pgrep -f 'uvicorn app.api:app'))"
    exit 0
fi
mkdir -p logs
nohup python3 -m uvicorn app.api:app --port 8000 > logs/api.err 2>&1 &
PID=$!
sleep 1
if ! kill -0 "$PID" 2>/dev/null; then
    echo "❌ api 启动失败，看 logs/api.err"
    exit 1
fi
echo "api 已启动，PID $PID"
echo "看日志: tail -f $(pwd)/logs/api.log"
