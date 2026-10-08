#!/bin/bash
# 停止定时发送端
if pkill -f "python.*mqtt_client"; then
    echo "sender 已停止"
else
    echo "sender 没在运行"
fi
