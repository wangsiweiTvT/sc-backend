#!/bin/bash
# 启动 REST API（FastAPI/uvicorn，后台运行，关终端不影响）
cd "$(dirname "$0")"
if pgrep -f "uvicorn api:app" > /dev/null; then
    echo "api 已在运行 (PID $(pgrep -f 'uvicorn api:app'))"
    exit 0
fi
nohup python3 -m uvicorn api:app --port 8000 > api.log 2>&1 &
echo "api 已启动，PID $!"
echo "看日志: tail -f $(pwd)/api.log"
