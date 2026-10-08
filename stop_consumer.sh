#!/bin/bash
# 停止 consumer
if pkill -f "python.*mqtt_consumer"; then
    # 等进程真正退出，避免紧接着的 start 误判"已在运行"
    for i in 1 2 3 4 5; do pgrep -f "python.*mqtt_consumer" > /dev/null || break; sleep 1; done
    echo "consumer 已停止"
else
    echo "consumer 没在运行"
fi
