#!/bin/bash
# 停止异常检测器
if pkill -f "python.*app.anomaly_detector"; then
    # 等进程真正退出，避免紧接着的 start 误判"已在运行"
    for i in 1 2 3 4 5; do pgrep -f "python.*app.anomaly_detector" > /dev/null || break; sleep 1; done
    echo "detector 已停止"
else
    echo "detector 没在运行"
fi
