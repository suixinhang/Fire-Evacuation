import os
import time
import math
import json
import pathlib

import logging
logger = logging.getLogger("FIRE_EVACUATION")

from map_data_service import MapDataServiceObj

speed = float(os.getenv('FIRE_EVACUATION_SPEED', '1.0'))
dt = 1

floor_name = MapDataServiceObj.map_obj.get_data()[0]["name"]

# ===== occupants =====
if os.getenv("FIRE_EVACUATION_RANDOM_OCCUPANT") != '0':
    MapDataServiceObj.respawn_occupants(
        floor_name,
        int(os.getenv("FIRE_EVACUATION_RANDOM_OCCUPANT"))
    )

# ===== 读取烟雾记录（时间序列）=====
use_old_fire_simulation = os.getenv("FIRE_EVACUATION_USE_OLD_FIRE_SIMULATION")

smoke_frames = []
smoke_index = 0

if use_old_fire_simulation:
    path = pathlib.Path(__file__).parent / "record" / (use_old_fire_simulation + ".txt")

    if not path.exists():
        raise FileNotFoundError(path)

    print("[Smoke] Loading replay frames...")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
                smoke_frames.append(frame)
            except:
                continue

    print(f"[Smoke] Loaded {len(smoke_frames)} frames")


logger.info("Waiting the initial....")
while not MapDataServiceObj.is_init_end:
    ...


# ===== 初始化烟雾状态 =====
if smoke_frames:
    first = smoke_frames[0]
    MapDataServiceObj.smoke_density = first.get("smoke_density", {})
    MapDataServiceObj.global_time = first.get("time", 0.0)

logger.info(f"Starting simulation. Speed Multiplier: {speed}x")

step_counter = 0
target_dt = dt

last_positions = {}

# 全局任务锁：记录搜救者当前的状态和目标
# 格式: { rid: {"type": "explore" 或 "rescue", "target": (tx, ty), "resume": (x, y) 或 None} }
active_tasks = {}

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

# ===== 区域划分 =====
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

# ================= 主循环 =================
while True:
    start_time = time.time()
    if int(os.getenv("FIRE_EVACUATION_MOVE_AFTER")) - step_counter <= 0:
        MapDataServiceObj.active_tasks = active_tasks

        responders = MapDataServiceObj.get_responders(floor_name)

        # ===== 1. 清理 uncertain =====
        clear_uncertain()

        # ===== 2. 视野更新 =====
        vis_map = MapDataServiceObj.compute_visibility_for_responders(
            floor_name,
            responders
        )

        for rid, (rx, ry) in responders:
            MapDataServiceObj.block_status[floor_name][rid] = "watched"

        for rid, visible in vis_map.items():
            for key in visible:
                MapDataServiceObj.block_status[floor_name][key] = "watched"

        current_real_occupants = get_occupants_set()

        # ===== 3. 抢占机制：支持“救更近的”，但增加判定阈值防止抖动 =====
        for rid, (rx, ry) in responders:
            current_task = active_tasks.get(rid)
            all_known_occs = get_occupants_set()
            
            # 获取所有已知且存在的幸存者，并计算 A* 距离
            scored_occs = []
            for occ_key in all_known_occs:
                if MapDataServiceObj.block_status[floor_name].get(occ_key) == "watched":
                    ox, oy = map(int, occ_key.split(","))
                    d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(ox, oy)])
                    if d is not None:
                        scored_occs.append(((ox, oy), d))
            
            if not scored_occs:
                continue

            # 按距离排序，找到当前物理路径上最近的
            scored_occs.sort(key=lambda x: x[1])
            best_pos, best_dist = scored_occs[0]

            # --- 核心改进：决策逻辑 ---
            if current_task and current_task["type"] == "rescue":
                curr_target = current_task["target"]
                target_pos_str = f"{curr_target[0]},{curr_target[1]}"
                
                # 如果当前锁定的目标已经消失了，直接换最近的
                if target_pos_str not in all_known_occs:
                    active_tasks[rid]["target"] = best_pos
                else:
                    # 计算当前目标的实时 A* 距离
                    curr_dist = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [curr_target])
    
                    if curr_dist is not None and best_dist < curr_dist :
                        # 只有显著更近，才抢占
                        active_tasks[rid]["target"] = best_pos
                    else:
                        # 否则，继续坚持当前目标
                        pass
            else:
                # 如果之前是 explore 或没任务，直接锁定最近的 rescue 任务
                resume_tgt = current_task["target"] if (current_task and current_task["type"] == "explore") else None
                active_tasks[rid] = {
                    "type": "rescue",
                    "target": best_pos,
                    "resume": resume_tgt
                }

        # ===== 4. 任务分配（m-1 循环）=====
        # 找出所有空闲（不在 active_tasks 里）的搜救者
        free_responders = [rid for rid, pos in responders if rid not in active_tasks]
        assigned_targets = set([task["target"] for task in active_tasks.values()])

        while free_responders:
            # 【关键修改】每次分配一个人后，重新计算未检索区域的面积，因为上一轮可能标记了 uncertain
            regions = compute_regions()
            candidates = build_candidates(regions)

            if not candidates:
                break

            best = None
            best_score = float("inf")

            for rid in free_responders:
                rx, ry = dict(responders)[rid]
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
            
            # 分配任务，进入锁定状态
            active_tasks[rid] = {
                "type": "explore",
                "target": (tx, ty),
                "resume": None
            }
            assigned_targets.add((tx, ty))

            # 预测视野并标记为 uncertain，影响下一次 compute_regions 的计算
            vis_area = len(vis_map.get(rid, []))
            radius = max(1, int(math.sqrt(vis_area / math.pi)))
            mark_uncertain_circle((tx, ty), radius)

            # 剔除该搜救者，下一轮在 m-1 个空闲中再次分配
            free_responders.remove(rid)


        # ===== 5. 移动（严格使用你的防抖算法）=====
        for rid, (rx, ry) in responders:
            # 如果既没有探查任务，也没发现幸存者，原地待命
            if rid not in active_tasks:
                continue
                
            task = active_tasks[rid]
            tx, ty = task["target"]

            # --- 到达逻辑 ---
            if (rx, ry) == (tx, ty):
                if task["type"] == "rescue":
                    # “到达救援者那里之后救援者就算是被救助了”
                    for floor in MapDataServiceObj.map_obj.get_data():
                        if floor["name"] == floor_name:
                            floor["points"] = [p for p in floor["points"] 
                                            if not (p.get("type") == "occupant" and p["x"] == tx and p["y"] == ty)]
                    
                    # “接着回到锁定状态，继续刚刚未完成的任务”
                    if task["resume"]:
                        active_tasks[rid] = {
                            "type": "explore",
                            "target": task["resume"],
                            "resume": None
                        }
                    else:
                        active_tasks.pop(rid)
                else:
                    # 探索任务到达，完成
                    active_tasks.pop(rid)
                continue

            # --- 原汁原味的防抖移动 ---
            if rid not in active_tasks:
                continue
                
            task = active_tasks[rid]
            tx, ty = task["target"]

            # --- 到达逻辑 ---
            if (rx, ry) == (tx, ty):
                # ... 这里保留你之前的救人成功/任务恢复逻辑 ...
                if task["type"] == "rescue":
                    for floor in MapDataServiceObj.map_obj.get_data():
                        if floor["name"] == floor_name:
                            floor["points"] = [p for p in floor["points"] 
                                            if not (p.get("type") == "occupant" and p["x"] == tx and p["y"] == ty)]
                    if task["resume"]:
                        active_tasks[rid] = {"type": "explore", "target": task["resume"], "resume": None}
                    else:
                        active_tasks.pop(rid)
                else:
                    active_tasks.pop(rid)
                continue

            # --- 核心修复：探测邻居的 A* 成本 ---
            best_next = None
            min_astar_dist = float("inf")

            # 遍历上下左右
            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
                nx, ny = rx + dx, ry + dy
                nk = f"{nx},{ny}"

                # 基础有效性检查
                if nk not in MapDataServiceObj.block_status[floor_name]: continue
                if MapDataServiceObj.block_status[floor_name][nk] == "null": continue

                # 调用你已有的 astar_distance
                # 测量“如果我走到了 (nx, ny)，我距离目标还有多远？”
                d = MapDataServiceObj.astar_distance(
                    floor_name,
                    (nx, ny),
                    [(tx, ty)]
                )

                if d is not None and d < min_astar_dist:
                    min_astar_dist = d
                    best_next = (nx, ny)

            if best_next:
                # 移动到 A* 距离缩短最明显的那个邻居
                MapDataServiceObj.responders[floor_name][rid] = best_next
                # 更新 last_positions 仅用于记录，不参与逻辑干扰
                last_positions[rid] = (rx, ry)
            else:
                # 如果四个方向都没有 A* 路径，说明目标真的不可达（被火封死等）
                active_tasks.pop(rid, None)

    step_counter += 1

    # ===== 烟雾推进（核心）=====
    if smoke_frames:
        frame = smoke_frames[smoke_index]

        MapDataServiceObj.smoke_density = frame.get("smoke_density", {})
        MapDataServiceObj.global_time = frame.get("time", MapDataServiceObj.global_time)

        smoke_index += 1

        if smoke_index >= len(smoke_frames):
            smoke_index = len(smoke_frames) - 1  # 停在最后一帧
    else:
        # 没有旧数据才用模拟
        MapDataServiceObj.update_simulation(dt)

    # ===== 时间控制 =====
    elapsed = time.time() - start_time
    if elapsed < target_dt:
        time.sleep(target_dt - elapsed)