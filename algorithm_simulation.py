import os
import time
from tqdm import tqdm

from map_data_service import MapDataServiceObj

speed = float(os.getenv('FIRE_EVACUATION_SPEED', '1.0'))
dt = 1  # 模拟系统基础步长

# 地图初始放人 目前不需要
# MapDataServiceObj.spawn_occupants_by_probability(n_per_floor=10)

# 地图目前只看一层 第一层
floor_name = MapDataServiceObj.map_obj.get_data()[0]["name"]

# 等待网页服务器开启
while not MapDataServiceObj.is_web_server_on:
    ...

print(f"Starting simulation. Speed Multiplier: {speed}x")


step_counter = 0
target_dt = dt

responder_targets = {}  # rid -> (tx, ty)

while True:
    start_time = time.time()
    step_counter += 1

    # print(f"\n[STEP {step_counter}]")

    responders = MapDataServiceObj.get_responders(floor_name)

    # ===== 1/6 视野更新 =====
    # print("1/6 Updating visibility...")

    vis_map = MapDataServiceObj.compute_visibility_for_responders(
        floor_name,
        responders
    )

    for rid, (rx, ry) in responders:
        MapDataServiceObj.block_status[floor_name][rid] = "watched"

    for rid, visible in vis_map.items():
        for key in visible:
            MapDataServiceObj.block_status[floor_name][key] = "watched"

    # ===== 2/6 连通区域 =====
    # print("2/6 Finding regions...")

    visited = set()
    regions = []

    for key, status in tqdm(
        MapDataServiceObj.block_status[floor_name].items(),
        desc="Scanning regions",
        leave=False,
        ncols=80
    ):
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

                if MapDataServiceObj.block_status[floor_name].get(nk) == "watched":
                    boundary.append((x, y))
                    break

        regions.append((comp, boundary))

    # ===== 3/6 frontier =====
    # print("3/6 Building frontiers...")

    candidates = []
    for comp, boundary in regions:
        area = len(comp)
        for (x, y) in boundary:
            candidates.append((x, y, area))

    # ===== 4/6 分配任务 =====
    # print("4/6 Assigning tasks...")

    free_responders = []
    for rid, pos in responders:
        if rid not in responder_targets:
            free_responders.append((rid, pos))

    assigned_targets = set()

    while free_responders and candidates:
        best = None
        best_score = float("inf")

        for rid, (rx, ry) in tqdm(
            free_responders,
            desc="Assigning responders",
            leave=False,
            ncols=80
        ):
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
                    best = (rid, (rx, ry), (tx, ty))

        if not best:
            break

        rid, _, target = best
        responder_targets[rid] = target
        assigned_targets.add(target)

        free_responders = [r for r in free_responders if r[0] != rid]

    # ===== 5/6 移动 + 救援 =====
    # print("5/6 Moving responders...")

    for rid, (rx, ry) in responders:
        visible = vis_map.get(rid, [])

        # 找最近 occupant
        nearest_occ = None
        best_d = float("inf")

        for key in visible:
            if MapDataServiceObj.block_status[floor_name].get(key) == "occupant":
                x, y = map(int, key.split(","))
                d = abs(x - rx) + abs(y - ry)
                if d < best_d:
                    best_d = d
                    nearest_occ = (x, y)

        # 如果看到人 → 覆盖目标
        if nearest_occ:
            responder_targets[rid] = nearest_occ

        target = responder_targets.get(rid)
        if not target:
            continue

        # 到达目标（可能是人）
        if target == (rx, ry):
            floors = MapDataServiceObj.map_obj.get_data()
            for floor in floors:
                if floor["name"] != floor_name:
                    continue

                floor["points"] = [
                    p for p in floor["points"]
                    if not (p.get("type") == "occupant" and p["x"] == rx and p["y"] == ry)
                ]

            responder_targets.pop(rid, None)
            continue

        # 正常走一步
        best_next = None
        best_d = float("inf")

        tx, ty = target

        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            nx, ny = rx + dx, ry + dy
            nk = f"{nx},{ny}"

            if nk not in MapDataServiceObj.block_status[floor_name]:
                continue

            if MapDataServiceObj.block_status[floor_name][nk] == "null":
                continue

            d = MapDataServiceObj.astar_distance(
                floor_name,
                (nx, ny),
                [target]
            )

            if d is None:
                continue

            if d < best_d:
                best_d = d
                best_next = (nx, ny)

        if best_next:
            nx, ny = best_next

            floors = MapDataServiceObj.map_obj.get_data()
            for floor in floors:
                if floor["name"] != floor_name:
                    continue

                for p in floor["points"]:
                    if p.get("type") == "responder" and p["x"] == rx and p["y"] == ry:
                        p["x"], p["y"] = nx, ny
                        break

    # ===== 6/6 推进模拟 =====
    # print("6/6 Simulation step...")

    elapsed = time.time() - start_time
    # print("before simulation: ", elapsed)

    MapDataServiceObj.update_simulation(dt, speed_multiplier=speed)

    # ===== 动态 sleep =====
    elapsed = time.time() - start_time
    # print("after simulation: ", elapsed)
    if elapsed < target_dt:
        time.sleep(target_dt - elapsed)