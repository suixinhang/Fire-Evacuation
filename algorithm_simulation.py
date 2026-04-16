import os
import time
import math
from tqdm import tqdm

import logging
from smoke_simulation import SmokeSimulation
logger = logging.getLogger("FIRE_EVACUATION")

from map_data_service import MapDataServiceObj

speed = float(os.getenv('FIRE_EVACUATION_SPEED', '1.0'))
dt = 1  # 模拟系统基础步长

# 地图目前只看一层 第一层
floor_name = MapDataServiceObj.map_obj.get_data()[0]["name"]

logger.info("Waiting the inital....")
# 等待网页服务器开启
while not MapDataServiceObj.is_init_end:
    ...

# 地图初始放人 目前不需要
# MapDataServiceObj.spawn_occupants_by_probability(n_per_floor=10)

logger.info(f"Starting simulation. Speed Multiplier: {speed}x")


step_counter = 0
target_dt = dt

responder_targets = {}
last_positions = {}  # ⭐ 防抖关键

# ===== occupants =====
def get_occupants_set():
    s = set()

    for floor in MapDataServiceObj.map_obj.get_data():
        if floor["name"] != floor_name:
            continue

        for p in floor["points"]:
            if p.get("type") == "occupant":
                s.add(f"{p['x']},{p['y']}")

    return s

# ===== 清除 uncertain =====
def clear_uncertain():
    for k in MapDataServiceObj.block_status[floor_name]:
        if MapDataServiceObj.block_status[floor_name][k] == "uncertain":
            MapDataServiceObj.block_status[floor_name][k] = "unwatched"

# ===== 标记 uncertain =====
def mark_uncertain_circle(center, radius):
    cx, cy = center
    for key in MapDataServiceObj.block_status[floor_name]:
        if MapDataServiceObj.block_status[floor_name][key] != "unwatched":
            continue

        x, y = map(int, key.split(","))

        if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
            MapDataServiceObj.block_status[floor_name][key] = "uncertain"

# ===== 区域 =====
def compute_regions():
    visited = set()
    regions = []

    for key, status in MapDataServiceObj.block_status[floor_name].items():
        if status != "unwatched" or key in visited:
            continue

        queue = [key]
        comp = []
        visited.add(key)

        while queue:
            cur = queue.pop()
            comp.append(cur)

            x, y = map(int, cur.split(","))

            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
                nk = f"{x+dx},{y+dy}"

                if nk in visited:
                    continue

                if MapDataServiceObj.block_status[floor_name].get(nk) == "unwatched":
                    visited.add(nk)
                    queue.append(nk)

        boundary = []
        for c in comp:
            x, y = map(int, c.split(","))

            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
                nk = f"{x+dx},{y+dy}"

                if MapDataServiceObj.block_status[floor_name].get(nk) in ("watched", "uncertain"):
                    boundary.append((x, y))
                    break

        regions.append((comp, boundary))

    return regions

# ===== frontier =====
def build_candidates(regions):
    candidates = []
    for comp, boundary in regions:
        area = len(comp)
        for (x, y) in boundary:
            candidates.append((x, y, area))
    return candidates


while True:
    start_time = time.time()
    step_counter += 1

    responders = MapDataServiceObj.get_responders(floor_name)

    # ===== 1. 清理 uncertain =====
    clear_uncertain()

    # ===== 2. 更新视野 =====
    vis_map = MapDataServiceObj.compute_visibility_for_responders(
        floor_name,
        responders
    )

    for rid, (rx, ry) in responders:
        MapDataServiceObj.block_status[floor_name][rid] = "watched"

    for rid, visible in vis_map.items():
        for key in visible:
            MapDataServiceObj.block_status[floor_name][key] = "watched"

    occupants_set = get_occupants_set()

    # ===== 3. 分配 =====
    regions = compute_regions()
    candidates = build_candidates(regions)

    free_responders = [
        (rid, pos) for rid, pos in responders
        if rid not in responder_targets
    ]

    assigned_targets = set()

    while free_responders and candidates:
        best = None
        best_score = float("inf")

        for rid, (rx, ry) in free_responders:
            for (tx, ty, area) in candidates:
                if (tx, ty) in assigned_targets:
                    continue

                d = MapDataServiceObj.astar_distance(
                    floor_name,
                    (rx, ry),
                    [(tx, ty)]
                )

                if d is None:
                    continue

                score = d / (area + 1e-5)

                if score < best_score:
                    best_score = score
                    best = (rid, (tx, ty))

        if not best:
            break

        rid, (tx, ty) = best
        responder_targets[rid] = (tx, ty)
        assigned_targets.add((tx, ty))

        # ===== uncertain =====
        vis_area = len(vis_map.get(rid, []))
        radius = max(1, int(math.sqrt(vis_area / math.pi)))

        mark_uncertain_circle((tx, ty), radius)

        free_responders = [r for r in free_responders if r[0] != rid]

    # ===== 4. 移动 =====
    for rid, (rx, ry) in responders:
        visible = vis_map.get(rid, [])

        # ===== 找人 =====
        nearest_occ = None
        best_d = float("inf")

        for key in visible:
            if key in occupants_set:
                x, y = map(int, key.split(","))
                d = abs(x - rx) + abs(y - ry)

                if d < best_d:
                    best_d = d
                    nearest_occ = (x, y)

        # ===== 🚑 救援模式 =====
        if nearest_occ:
            tx, ty = nearest_occ
        else:
            target = responder_targets.get(rid)
            if not target:
                continue
            tx, ty = target

        # ===== 到达目标 =====
        if (rx, ry) == (tx, ty):
            if nearest_occ:
                for floor in MapDataServiceObj.map_obj.get_data():
                    if floor["name"] != floor_name:
                        continue

                    # ⭐ 删除 occupant（修正为 tx, ty）
                    floor["points"] = [
                        p for p in floor["points"]
                        if not (p.get("type") == "occupant" and p["x"] == tx and p["y"] == ty)
                    ]

            responder_targets.pop(rid, None)
            continue

        # ===== ⭐ 防抖移动 =====
        prev = last_positions.get(rid)

        best_next = None
        best_d = float("inf")

        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            nx, ny = rx + dx, ry + dy

            if prev and (nx, ny) == prev:
                continue

            nk = f"{nx},{ny}"

            if nk not in MapDataServiceObj.block_status[floor_name]:
                continue

            if MapDataServiceObj.block_status[floor_name][nk] == "null":
                continue

            d = abs(nx - tx) + abs(ny - ty)

            if d < best_d:
                best_d = d
                best_next = (nx, ny)

        # ===== ⭐ 核心修复：防重复点 =====
        if best_next:
            nx, ny = best_next

            for floor in MapDataServiceObj.map_obj.get_data():
                if floor["name"] != floor_name:
                    continue

                # ⭐ 1. 删除目标格子已有 responder（关键！！！）
                floor["points"] = [
                    p for p in floor["points"]
                    if not (p.get("type") == "responder" and p["x"] == nx and p["y"] == ny)
                ]

                # ⭐ 2. 移动当前 responder
                for p in floor["points"]:
                    if p.get("type") == "responder" and p["x"] == rx and p["y"] == ry:
                        p["x"], p["y"] = nx, ny
                        break

            # ⭐ 记录上一位置
            last_positions[rid] = (rx, ry)


    for floor in MapDataServiceObj.map_obj.get_data():
        if floor["name"] != floor_name:
            continue

        coord_map = {}

        for p in floor["points"]:
            key = (p["x"], p["y"])

            # ===== 优先级（谁保留）=====
            priority = {
                "responder": 3,
                "occupant": 2,
                "wall": 1
            }

            t = p.get("type")
            pr = priority.get(t, 0)

            if key not in coord_map:
                coord_map[key] = (pr, p)
            else:
                # ⭐ 保留优先级更高的
                if pr > coord_map[key][0]:
                    coord_map[key] = (pr, p)

        # ⭐ 重建 points（彻底无重复）
        floor["points"] = [v[1] for v in coord_map.values()]

    # ===== 5. 时间控制 =====
    elapsed = time.time() - start_time
    if elapsed < target_dt:
        time.sleep(target_dt - elapsed)