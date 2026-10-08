#!/bin/bash
# 停止 REST API
if pkill -f "uvicorn api:app"; then
    echo "api 已停止"
else
    echo "api 没在运行"
fi
