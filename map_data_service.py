import os
import sys
import random
import threading

from flask import Flask, render_template, request, jsonify
from map import Map

import logging
from smoke_simulation import SmokeSimulation
logger = logging.getLogger("FIRE_EVACUATION")

class MapDataService:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        USE_MAP_ID = os.getenv('FIRE_EVACUATION_USE_MAP_ID')
        if not USE_MAP_ID:
            raise ValueError("No provided good map id!")

        self.map_obj = Map(map_id=USE_MAP_ID)

        self.block_status = {}
        self.smoke_density = {}
        self.global_time = 0.0 
        
        self.simulators = {}    # 存放每层的仿真实例
        self.fire_origins = {}  # 存放每层的火源坐标

        t = threading.Thread(target=self.web_server, daemon=True)
        t.start()
        logger.info("Started Map Web Server Thread.")

    def init_runtime_data(self):
        self.block_status.clear()
        self.smoke_density.clear()
        self.simulators.clear()
        self.fire_origins.clear()

        floors = self.map_obj.get_data() or []
        for floor in floors:
            fname = floor.get("name")
            points = floor.get("points", [])

            self.block_status[fname] = {}
            self.smoke_density[fname] = {}
            self.fire_origins[fname] = []

            if not points:
                continue

            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]

            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            size_x = max_x + 1
            size_y = max_y + 1
            sim = SmokeSimulation(size_x=size_x, size_y=size_y, dt=0.2)

            point_map = {}
            for p in points:
                point_map[(p["x"], p["y"])] = p

            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    key = f"{x},{y}"

                    if (x, y) in point_map:
                        p = point_map[(x, y)]
                        if p.get("type") in ("wall", "stair"):
                            self.block_status[fname][key] = "null"
                            self.smoke_density[fname][key] = -1
                            sim.wall[y, x] = 1  # 标记物理墙壁
                        elif p.get("type") == "fire_origin":
                            self.block_status[fname][key] = "fire"
                            self.smoke_density[fname][key] = 100
                            self.fire_origins[fname].append((x, y)) # 记录火源位置
                        else:
                            self.block_status[fname][key] = "unwatched"
                            self.smoke_density[fname][key] = 0
                    else:
                        # 留空区域允许烟雾扩散
                        self.block_status[fname][key] = "unwatched"
                        self.smoke_density[fname][key] = 0
            
            self.simulators[fname] = sim
    
    def update_simulation(self, dt, speed_multiplier=1.0):
        """推进全局时间与模拟器"""
        sim_dt = dt * speed_multiplier
        self.global_time += sim_dt
        
        for fname, sim in self.simulators.items():
            origins = self.fire_origins.get(fname, [])
            
            # 步进物理模拟
            sim.step(fire_origins=origins, dt=sim_dt)
            
            # 将物理矩阵里的烟雾浓度同步到 Web API 数据字典
            for key in self.smoke_density[fname]:
                x, y = map(int, key.split(","))
                # 跳过墙壁
                if sim.wall[y, x] == 1:
                    continue
                
                # 同步浓度并限制在 0-100 区间
                density_val = float(sim.smoke[y, x])
                self.smoke_density[fname][key] = min(100.0, max(0.0, density_val))
                
    def web_server(self):
        HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
        HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8001'))

        app = Flask(__name__)
        self.init_runtime_data()

        log = logging.getLogger('werkzeug')
        log.disabled = True
        sys.modules['flask.cli'].show_server_banner = lambda *x: None

        @app.route("/")
        def index():
            return render_template("map_service/index.html")

        @app.route("/api/block_type")
        def api_block_type():
            return jsonify(self.map_obj.get_data() or [])

        @app.route("/api/block_status")
        def api_block_status():
            return jsonify(self.block_status)

        @app.route("/api/smoke_density")
        def api_smoke_density():
            return jsonify(self.smoke_density)

        logger.info(f"Map Runtime Server http://{HTTP_ADDR}:{HTTP_PORT}")
        app.run(HTTP_ADDR, HTTP_PORT)


MapDataServiceObj = MapDataService()
