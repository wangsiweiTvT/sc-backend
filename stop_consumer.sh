#!/bin/bash
# 停止 consumer
if pkill -f "python.*mqtt_consumer"; then
    echo "consumer 已停止"
else
    echo "consumer 没在运行"
fi
