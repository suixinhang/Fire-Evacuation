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
        self.probability_map = {}   # 可能地图除了watch/unwatch, 墙壁/空间 和烟雾浓度之外，还需要在增加一个视图，每个格子赋予一个不同数字，最后加起来等于1即可（概率pdf），并给出一个可以修改每个格子的概率的方法，修改完毕之后再整体归一化
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

            # 初始化概率分布
            self.probability_map[fname] = {}

            valid_cells = [
                key for key, status in self.block_status[fname].items()
                if status != "null"
            ]

            if valid_cells:
                init_prob = 1.0 / len(valid_cells)
                for key in valid_cells:
                    self.probability_map[fname][key] = init_prob

    def set_cell_probability(self, floor_name, key, value):
        """
        修改单个格子的概率值，并自动归一化
        """
        if floor_name not in self.probability_map:
            return False

        if key not in self.probability_map[floor_name]:
            return False

        if value < 0:
            return False

        self.probability_map[floor_name][key] = float(value)

        # 每次自动进行归一化
        self.normalize_probability(floor_name)
        return True
    
    def set_multiple_probabilities(self, floor_name, updates: dict):
        """
        批量修改概率
        updates = {"x,y": value, ...}
        """
        if floor_name not in self.probability_map:
            return False

        for key, value in updates.items():
            if key in self.probability_map[floor_name] and value >= 0:
                self.probability_map[floor_name][key] = float(value)

        self.normalize_probability(floor_name)
        return True
    
    def normalize_probability(self, floor_name):
        """
        将某一层的概率归一化，使总和为1
        """
        probs = self.probability_map.get(floor_name)
        if not probs:
            return

        total = sum(probs.values())

        if total <= 0:
            # 防止全0，重新均匀分布
            n = len(probs)
            if n == 0:
                return
            uniform = 1.0 / n
            for k in probs:
                probs[k] = uniform
            return

        for k in probs:
            probs[k] /= total
    
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
        
        @app.route("/api/probability_map")
        def api_probability_map():
            return jsonify(self.probability_map)

        logger.info(f"Map Runtime Server http://{HTTP_ADDR}:{HTTP_PORT}")
        app.run(HTTP_ADDR, HTTP_PORT)


MapDataServiceObj = MapDataService()
