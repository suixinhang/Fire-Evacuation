import os
import time
from map_data_service import MapDataServiceObj

# 提取环境倍速，默认为 1.0 (原速)
speed = float(os.getenv('FIRE_EVACUATION_SPEED', '1.0'))
dt = 0.2  # 模拟系统基础步长

print(f"Starting smoke distribution simulation. Speed Multiplier: {speed}x")

while True:
    # 按照倍速推进全局时间和烟雾模拟
    MapDataServiceObj.update_simulation(dt, speed_multiplier=speed)
    
    # 强制真实等待 dt 秒，让推演按照真实时钟运行
    time.sleep(dt)