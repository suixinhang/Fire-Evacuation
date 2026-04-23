import os
import sys
import time
import math
import heapq
import random
import pathlib
import threading

from flask import Flask, render_template, jsonify
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
        self.probability_map = {}
        self.entrances = {}
        self.global_time = 0.0
        self.visibility_map = {}
        self.global_memory_occupants = {}
        self.simulators = {}
        self.fire_origins = {}
        self.active_tasks = {}

        self.responders = {}

        if not os.getenv('FIRE_EVACUATION_USE_RECORD_ID') and os.getenv('FIRE_EVACUATION_NO_RECORD') == "0":
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
        self.responders.clear()

        floors = self.map_obj.get_data() or []

        for floor in floors:
            fname = floor.get("name")
            points = floor.get("points", [])

            self.block_status[fname] = {}
            self.smoke_density[fname] = {}
            self.fire_origins[fname] = []
            self.entrances[fname] = []
            self.cell_weight[fname] = {}
            self.responders[fname] = {}

            if not points:
                continue

            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]

            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            size_x = max_x + 1
            size_y = max_y + 1
            sim = SmokeSimulation(size_x=size_x, size_y=size_y, dt=0.2)

            point_map = {(p["x"], p["y"]): p for p in points}

            # ===== 初始化 grid =====
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    key = f"{x},{y}"
                    self.cell_weight[fname][key] = 0

                    if (x, y) in point_map:
                        p = point_map[(x, y)]
                        t = p.get("type")

                        if t in ("wall", "stair"):
                            self.block_status[fname][key] = "null"
                            self.smoke_density[fname][key] = -1
                            sim.wall[y, x] = 1

                        elif t == "fire_origin":
                            self.block_status[fname][key] = "fire"
                            self.smoke_density[fname][key] = 100
                            self.fire_origins[fname].append((x, y))

                        elif t == "responder":
                            rid = f"{x},{y}"
                            self.responders[fname][rid] = (x, y)

                            self.block_status[fname][key] = "unwatched"
                            self.smoke_density[fname][key] = 0

                        else:
                            self.block_status[fname][key] = "unwatched"
                            self.smoke_density[fname][key] = 0
                    else:
                        self.block_status[fname][key] = "unwatched"
                        self.smoke_density[fname][key] = 0

            self.simulators[fname] = sim

            # ===== 概率初始化 =====
            self.probability_map[fname] = {}
            valid_cells = [
                key for key, status in self.block_status[fname].items()
                if status != "null"
            ]

            if valid_cells:
                init_prob = 1.0 / len(valid_cells)
                for key in valid_cells:
                    self.probability_map[fname][key] = init_prob

            # ===== 二次处理（权重 / entrance）=====
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

                elif t == "entrance":
                    self.block_status[fname][key] = "null"
                    self.smoke_density[fname][key] = 0
                    self.entrances[fname].append((x, y))

            # 删除 map 中 responder
            floor["points"] = [
                p for p in points if p.get("type") != "responder"
            ]

        if os.getenv('FIRE_EVACUATION_DISABLE_INIT_PROBABILITY') == "0":
            for fname in self.simulators:
                self.update_probability_by_astar(fname)

    # ================= A* =================

    def astar_distance(self, floor_name, start, entrances):

        def heuristic(x, y):
            return min(abs(x - ex) + abs(y - ey) for ex, ey in entrances)

        open_heap = []
        heapq.heappush(open_heap, (heuristic(*start), 0, start))

        visited = set()

        while open_heap:
            f, g, (x, y) = heapq.heappop(open_heap)

            if (x, y) in visited:
                continue
            visited.add((x, y))

            if (x, y) in entrances:
                return g

            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
                nx, ny = x + dx, y + dy

                if (nx, ny) in entrances:
                    return g + 1

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

    def update_probability_by_astar(self, floor_name):
        entrances = self.entrances.get(floor_name, [])
        if not entrances:
            return

        probs = self.probability_map[floor_name]
        dist_map = {}

        for key in probs:
            x, y = map(int, key.split(","))
            d = self.astar_distance(floor_name, (x, y), entrances)
            dist_map[key] = d or 0

        for key in probs:
            d = dist_map[key]
            w = self.cell_weight[floor_name].get(key, 0)
            probs[key] = d * w

        self.normalize_probability(floor_name)

    def normalize_probability(self, floor_name):
        probs = self.probability_map.get(floor_name)
        if not probs:
            return

        total = sum(probs.values())

        if total <= 0:
            n = len(probs)
            if n == 0:
                return
            uniform = 1.0 / n
            for k in probs:
                probs[k] = uniform
            return

        for k in probs:
            probs[k] /= total

    # ================= 模拟 =================

    def update_simulation(self, dt, speed_multiplier=1.0):
        sim_dt = dt * speed_multiplier
        self.global_time += sim_dt

        for fname, sim in self.simulators.items():
            origins = self.fire_origins.get(fname, [])

            sim.step(fire_origins=origins, dt=sim_dt)

            for key in self.smoke_density[fname]:
                x, y = map(int, key.split(","))

                if sim.wall[y, x] == 1:
                    continue

                val = float(sim.smoke[y, x])
                self.smoke_density[fname][key] = min(100.0, max(0.0, val))

        for fname in self.simulators:
                responders = self.get_responders(fname)
                # 1. 计算当前所有人的实时视野
                vis_results = self.compute_visibility_for_responders(fname, responders)
                
                # 2. 将这些视野合并到全局 block_status 中
                for rid, visible_cells in vis_results.items():
                    for key in visible_cells:
                        # 只要扫过，就标记为已观测，这会覆盖之前的 unwatched 或遗留状态
                        self.block_status[fname][key] = "watched"

    def respawn_occupants(self, floor_name, N):
        """
        清空地图中的 occupant，并根据 probability_map 随机生成 N 个 occupant
        直接写入 map_obj（block_type）
        """

        floors = self.map_obj.get_data()
        if not floors:
            return

        # ===== 1. 找到对应楼层 =====
        target_floor = None
        for floor in floors:
            if floor.get("name") == floor_name:
                target_floor = floor
                break

        if not target_floor:
            return

        points = target_floor.get("points", [])

        # ===== 2. 删除已有 occupant =====
        points = [p for p in points if p.get("type") != "occupant"]

        # ===== 3. 构造候选格子 =====
        probs = self.probability_map.get(floor_name, {})
        valid_cells = []
        weights = []

        for key, prob in probs.items():
            x, y = map(int, key.split(","))

            status = self.block_status[floor_name].get(key)
            if status == "null":
                continue

            valid_cells.append((x, y))
            weights.append(prob)

        if not valid_cells:
            target_floor["points"] = points
            return

        total = sum(weights)
        if total <= 0:
            weights = [1.0 for _ in valid_cells]

        # ===== 4. 概率采样 =====
        chosen = random.choices(
            population=valid_cells,
            weights=weights,
            k=N
        )

        # ===== 5. 写入 occupant =====
        for (x, y) in chosen:
            points.append({
                "x": x,
                "y": y,
                "type": "occupant"
            })

        # ===== 6. 写回地图 =====
        target_floor["points"] = points

    # ================= 可见性 =================

    def get_responders(self, floor_name):
        return [
            (rid, pos)
            for rid, pos in self.responders.get(floor_name, {}).items()
        ]

    def compute_visibility_for_responders(self, floor_name, responders):
        result = {}

        for rid, (x, y) in responders:
            key = f"{x},{y}"

            if key not in self.block_status[floor_name]:
                continue

            if self.block_status[floor_name][key] == "null":
                continue

            visible = self._compute_visible_from_point(
                floor_name, (x, y), 72, 50, 0.3
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
                floor_name, start, (dx, dy), max_range, smoke_threshold
            )

            visible.update(cells)

        return list(visible)

    def _cast_single_ray(self, floor_name, start, direction, max_range, smoke_threshold):
        x0, y0 = start
        dx, dy = direction

        length = math.hypot(dx, dy)
        if length == 0:
            return []

        dx /= length
        dy /= length

        x, y = x0 + 0.5, y0 + 0.5
        accumulated_smoke = 0.0
        visible_cells = []
        visited = set()

        for _ in range(max_range * 2):
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
            accumulated_smoke += smoke
            visible_cells.append(key)

            if accumulated_smoke >= smoke_threshold:
                break

        return visible_cells

    # ================= API =================

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

        @app.route("/api/responders")
        def api_responders():
            return jsonify(self.responders)

        @app.route("/api/responder_visibility")
        def api_responder_visibility():
            result = {}

            for fname in self.block_status:
                responders = self.get_responders(fname)
                vis_map = self.compute_visibility_for_responders(fname, responders)
                result[fname] = vis_map

            return jsonify(result)

        @app.route("/api/get_time")
        def get_time():
            return jsonify({"res": self.global_time})
        
        @app.route("/api/active_tasks")
        def api_active_tasks():
            return jsonify(self.active_tasks)

        logger.info(f"Map Runtime Server http://{HTTP_ADDR}:{HTTP_PORT}")
        app.run(HTTP_ADDR, HTTP_PORT)


MapDataServiceObj = MapDataService()