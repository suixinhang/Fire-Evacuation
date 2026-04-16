import os
import sys
import time
import math
import heapq
import random
import pathlib
import threading
from collections import deque

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
        self.is_init_end = False

        self.block_status = {}
        self.smoke_density = {}
        self.cell_weight = {}
        self.probability_map = {}   # 可能地图除了watch/unwatch, 墙壁/空间 和烟雾浓度之外，还需要在增加一个视图，每个格子赋予一个不同数字，最后加起来等于1即可（概率pdf），并给出一个可以修改每个格子的概率的方法，修改完毕之后再整体归一化
        self.entrances = {}   # 每层入口
        self.global_time = 0.0 
        self.visibility_map = {}
        
        self.simulators = {}    # 存放每层的仿真实例
        self.fire_origins = {}  # 存放每层的火源坐标

        if not os.getenv('FIRE_EVACUATION_USE_RECORD_ID') and os.getenv('FIRE_EVACUATION_NO_RECORD') == "0":
            # 定义全局地图数据动态切片变化数据文件夹名字
            self.record_name = str(int(time.time()))
            self.record_path = pathlib.Path(__file__).parent / "record" / (self.record_name + ".txt")
            self.record_fp = open(self.record_path, "w+")
            logger.info(f"Record start, file name is {self.record_name}.txt")

        t = threading.Thread(target=self.web_server, daemon=True)
        t.start()
        logger.info("Started Map Web Server Thread.")

        logger.info("Starting init runtime data.")
        self.init_runtime_data()
        logger.info("End init runtime data.")
        self.is_init_end = True

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
            self.entrances[fname] = []
            self.cell_weight[fname] = {}

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
                    self.cell_weight[fname][key] = 0

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

            for p in points:
                x, y = p["x"], p["y"]
                key = f"{x},{y}"

                t = p.get("type")
                w = p.get("weight", 0)

                self.cell_weight[fname][key] = w

                if t == "wall":
                    self.block_status[fname][key] = "null"
                    self.smoke_density[fname][key] = -1
                    sim.wall[y, x] = 1

                elif t == "fire_origin":
                    self.block_status[fname][key] = "null"
                    self.smoke_density[fname][key] = 100
                    self.fire_origins[fname].append((x, y))

                elif t == "entrance":
                    self.block_status[fname][key] = "null"
                    self.smoke_density[fname][key] = 0
                    self.entrances[fname].append((x, y))

                else:
                    self.block_status[fname][key] = "unwatched"
                    self.smoke_density[fname][key] = 0

        if os.getenv('FIRE_EVACUATION_DISABLE_INIT_PROBABILITY') == "0":
            for fname in self.simulators:
                self.update_probability_by_astar(fname)

    def astar_distance(self, floor_name, start, entrances):
        """
        从 start 到最近 entrance 的最短路径（四方向，每步=1）
        """

        def heuristic(x, y):
            return min(abs(x - ex) + abs(y - ey) for ex, ey in entrances)

        open_heap = []
        heapq.heappush(open_heap, (heuristic(*start), 0, start))  
        # (f, g, (x,y))

        visited = set()

        while open_heap:
            f, g, (x, y) = heapq.heappop(open_heap)

            if (x, y) in visited:
                continue
            visited.add((x, y))

            if (x, y) in entrances:
                return g

            for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
                nx, ny = x + dx, y + dy
                key = f"{nx},{ny}"

                if key not in self.block_status[floor_name]:
                    continue

                if self.block_status[floor_name][key] == "null":
                    continue

                if (nx, ny) in visited:
                    continue

                new_g = g + 1
                new_f = new_g + heuristic(nx, ny)

                heapq.heappush(open_heap, (new_f, new_g, (nx, ny)))

        return None

    def update_probability_by_astar(self, floor_name, weight=1.0):
        entrances = self.entrances.get(floor_name, [])
        if not entrances:
            return

        probs = self.probability_map[floor_name]

        max_d = 0
        dist_map = {}

        for key in probs:
            x, y = map(int, key.split(","))

            d = self.astar_distance(floor_name, (x, y), entrances)

            if d is None:
                d = 0

            dist_map[key] = d
            max_d = max(max_d, d)

        for key in probs:
            d = dist_map[key]
            w = self.cell_weight[floor_name].get(key, 0)

            probs[key] = d * w

        self.normalize_probability(floor_name)

    def take_record(self):
        """
        流式追加日志（四行结构，无内存缓存）
        """

        if os.getenv('FIRE_EVACUATION_NO_RECORD') == "1":
            return

        if not hasattr(self, "_record_counter"):
            self._record_counter = 0

        try:
            # ===== 构造 DATA 行 =====
            lines = []

            for fname in self.block_status:
                parts = []
                for key in self.block_status[fname]:
                    status = self.block_status[fname].get(key, "")
                    smoke = self.smoke_density[fname].get(key, 0)
                    prob = self.probability_map[fname].get(key, 0)

                    parts.append(f"{key}:{status}:{smoke:.2f}:{prob:.6f}")

                line = f"{fname}|{' ; '.join(parts)}"
                lines.append(line)

            data_line = "||".join(lines)

            # ===== 写入文件（追加）=====
            self.record_fp.write(f"STEP:{self._record_counter}\n")
            self.record_fp.write(f"TIME:{self.global_time:.3f}\n")
            self.record_fp.write(f"DATA:{data_line}\n")
            self.record_fp.write("----\n")

            self.record_fp.flush()
            os.fsync(self.record_fp.fileno())  # 强制落盘

            self._record_counter += 1

        except Exception as e:
            logger.error(f"Record write failed: {e}")

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
            
        for fname in self.simulators:
            self.update_probability_by_astar(fname)
    
    def compute_visibility_for_responders(self, floor_name, responders):
        """
        responders: [(id, (x,y)), ...]
        """
        result = {}

        for rid, (x, y) in responders:
            key = f"{x},{y}"

            if key not in self.block_status[floor_name]:
                continue

            if self.block_status[floor_name][key] == "null":
                continue

            visible = self._compute_visible_from_point(
                floor_name,
                (x, y),
                num_rays=72,
                max_range=50,
                smoke_threshold=0.3
            )

            result[rid] = visible

        return result

    def _compute_visible_from_point(self, floor_name, start, num_rays, max_range, smoke_threshold):
        visible = set()

        for i in range(num_rays):
            angle = 2 * math.pi * i / num_rays
            dx = math.cos(angle)
            dy = math.sin(angle)

            cells = self._cast_single_ray(
                floor_name,
                start,
                (dx, dy),
                max_range,
                smoke_threshold
            )

            visible.update(cells)

        return list(visible)  # ✅ 统一用 "x,y"

    def _cast_single_ray(self, floor_name, start, direction, max_range, smoke_threshold):
        x0, y0 = start
        dx, dy = direction

        length = math.hypot(dx, dy)
        if length == 0:
            return []

        dx /= length
        dy /= length

        x, y = x0 + 0.5, y0 + 0.5  # 从格子中心出发

        accumulated_smoke = 0.0
        visible_cells = []
        visited = set()

        for _ in range(max_range * 2):  # 多一点步数防止漏格
            x += dx * 0.5
            y += dy * 0.5

            gx, gy = int(x), int(y)
            key = f"{gx},{gy}"

            if key in visited:
                continue
            visited.add(key)

            if key not in self.block_status[floor_name]:
                break

            if self.block_status[floor_name][key] == "null":
                break

            smoke = self.smoke_density[floor_name].get(key, 0)
            smoke_norm = smoke

            accumulated_smoke += smoke_norm
            visible_cells.append(key)

            if accumulated_smoke >= smoke_threshold:
                break

        return visible_cells

    def get_responders(self, floor_name):
        responders = []

        floors = self.map_obj.get_data() or []

        for floor in floors:
            if floor.get("name") != floor_name:
                continue

            for p in floor.get("points", []):
                if p.get("type") == "responder":
                    x, y = p["x"], p["y"]
                    key = f"{x},{y}"
                    responders.append((key, (x, y)))

        return responders

    def spawn_occupants_by_probability(self, n_per_floor: int):
        """
        根据 probability_map，在每一层随机生成 n 个 occupant，
        并直接写入 map_obj 的数据中（points）
        """

        floors = self.map_obj.get_data() or []

        for floor in floors:
            fname = floor.get("name")
            points = floor.get("points", [])

            probs = self.probability_map.get(fname)
            if not probs:
                continue

            # ===== 过滤可放置位置 =====
            valid_keys = [
                k for k in probs
                if self.block_status[fname].get(k) != "null"
            ]

            if not valid_keys:
                continue

            weights = [probs[k] for k in valid_keys]

            # ===== 随机采样 =====
            chosen = random.choices(
                valid_keys,
                weights=weights,
                k=n_per_floor
            )

            # ===== 去重（避免重叠）=====
            chosen = list(set(chosen))

            # ===== 当前已有点（避免覆盖 responder 等）=====
            existing = {(p["x"], p["y"]) for p in points}

            for key in chosen:
                x, y = map(int, key.split(","))

                if (x, y) in existing:
                    continue

                # 🚀 核心：直接写入地图
                points.append({
                    "x": x,
                    "y": y,
                    "type": "occupant"
                })

    def web_server(self):
        HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
        HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8001'))

        app = Flask(__name__)

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
        
        @app.route("/api/responder_visibility")
        def api_responder_visibility():
            result = {}

            for fname in self.block_status:
                responders = self.get_responders(fname)

                if not responders:
                    result[fname] = {}
                    continue

                vis_map = self.compute_visibility_for_responders(fname, responders)

                result[fname] = vis_map

            return jsonify(result)

        @app.route("/api/get_time")
        def get_time():
            return jsonify({"res": self.global_time})

        logger.info(f"Map Runtime Server http://{HTTP_ADDR}:{HTTP_PORT}")
        app.run(HTTP_ADDR, HTTP_PORT)


MapDataServiceObj = MapDataService()
