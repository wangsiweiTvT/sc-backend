#!/bin/bash
# 停止 REST API
if pkill -f "uvicorn app.api:app"; then
    # 等进程真正退出，避免紧接着的 start 误判"已在运行"
    for i in 1 2 3 4 5; do pgrep -f "uvicorn app.api:app" > /dev/null || break; sleep 1; done
    echo "api 已停止"
else
    echo "api 没在运行"
fi
