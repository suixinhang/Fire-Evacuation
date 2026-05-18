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
# 格式: { rid: {"type": "explore"/"rescue"/"exit", "target": (tx, ty), "resume": (x, y) 或 None} }
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
            # 二次校验：确保候选点确实是 unwatched（动态过滤已探索区域）
            key = f"{x},{y}"
            if MapDataServiceObj.block_status[floor_name].get(key) != "unwatched":
                continue
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

        # ----- 取消已经过时的探索任务（目标区域已被探索）-----
        for rid in list(active_tasks.keys()):
            task = active_tasks[rid]
            if task["type"] == "explore":
                tx, ty = task["target"]
                if MapDataServiceObj.block_status[floor_name].get(f"{tx},{ty}") != "unwatched":
                    active_tasks.pop(rid)

        # ===== 3. 抢占机制：互斥锁防止多人争抢同一幸存者 =====
        # 收集所有已被锁定的救援目标
        all_rescue_targets = set()
        for task in active_tasks.values():
            if task["type"] == "rescue":
                all_rescue_targets.add(f"{task['target'][0]},{task['target'][1]}")

        for rid, (rx, ry) in responders:
            current_task = active_tasks.get(rid)
            all_known_occs = get_occupants_set()

            # 当前搜救者自己的救援目标，允许保留
            my_target_str = None
            if current_task and current_task["type"] == "rescue":
                my_target_str = f"{current_task['target'][0]},{current_task['target'][1]}"

            # 获取所有已知且存在的幸存者，排除已被其他搜救者锁定的目标
            scored_occs = []
            for occ_key in all_known_occs:
                if MapDataServiceObj.block_status[floor_name].get(occ_key) == "watched":
                    if occ_key in all_rescue_targets and occ_key != my_target_str:
                        continue
                    ox, oy = map(int, occ_key.split(","))
                    d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(ox, oy)])
                    if d is not None:
                        scored_occs.append(((ox, oy), d))

            if not scored_occs:
                # 没有可用幸存者 → 释放旧救援任务，回退到探索
                if current_task and current_task["type"] == "rescue":
                    resume_tgt = current_task.get("resume")
                    if resume_tgt:
                        # 先校验 resume 目标是否仍然 unwatched
                        rx_tgt, ry_tgt = resume_tgt
                        if MapDataServiceObj.block_status[floor_name].get(f"{rx_tgt},{ry_tgt}") == "unwatched":
                            active_tasks[rid] = {
                                "type": "explore",
                                "target": resume_tgt,
                                "resume": None
                            }
                        else:
                            active_tasks.pop(rid)
                    else:
                        active_tasks.pop(rid)
                continue

            # 按距离排序
            scored_occs.sort(key=lambda x: x[1])
            best_pos, best_dist = scored_occs[0]

            # --- 决策逻辑（含互斥锁更新）---
            if current_task and current_task["type"] == "rescue":
                curr_target = current_task["target"]
                target_pos_str = f"{curr_target[0]},{curr_target[1]}"

                if target_pos_str not in all_known_occs:
                    all_rescue_targets.discard(target_pos_str)
                    active_tasks[rid]["target"] = best_pos
                    all_rescue_targets.add(f"{best_pos[0]},{best_pos[1]}")
                else:
                    curr_dist = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [curr_target])

                    if curr_dist is not None and best_dist < curr_dist:
                        all_rescue_targets.discard(target_pos_str)
                        active_tasks[rid]["target"] = best_pos
                        all_rescue_targets.add(f"{best_pos[0]},{best_pos[1]}")
            else:
                resume_tgt = current_task["target"] if (current_task and current_task["type"] == "explore") else None
                active_tasks[rid] = {
                    "type": "rescue",
                    "target": best_pos,
                    "resume": resume_tgt
                }
                all_rescue_targets.add(f"{best_pos[0]},{best_pos[1]}")

        # ===== 4. 探索任务分配（m-1 循环）=====
        free_responders = [rid for rid, pos in responders if rid not in active_tasks]
        assigned_targets = set([task["target"] for task in active_tasks.values()])

        while free_responders:
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

            active_tasks[rid] = {
                "type": "explore",
                "target": (tx, ty),
                "resume": None
            }
            assigned_targets.add((tx, ty))

            vis_area = len(vis_map.get(rid, []))
            radius = max(1, int(math.sqrt(vis_area / math.pi)))
            mark_uncertain_circle((tx, ty), radius)

            free_responders.remove(rid)

        # ===== 5. 全部救出后 → 全体寻找最近的出口撤离 =====
        all_occupants_now = get_occupants_set()
        exits = MapDataServiceObj.entrances.get(floor_name, [])

        if not all_occupants_now and exits:
            # 检查是否还有 unwatched 区域（如果有，继续探索可能会发现新的幸存者）
            has_unwatched = any(
                s in ("unwatched", "uncertain")
                for s in MapDataServiceObj.block_status[floor_name].values()
            )

            if not has_unwatched:
                # 没有幸存者也没有未探索区域 → 全体撤离
                for rid, (rx, ry) in responders:
                    # 如果已经在出口任务中，跳过
                    current_task = active_tasks.get(rid)
                    if current_task and current_task["type"] == "exit":
                        continue

                    # 找最近的出口
                    best_exit = None
                    best_exit_dist = float("inf")
                    for ex, ey in exits:
                        d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(ex, ey)])
                        if d is not None and d < best_exit_dist:
                            best_exit_dist = d
                            best_exit = (ex, ey)

                    if best_exit:
                        active_tasks[rid] = {
                            "type": "exit",
                            "target": best_exit,
                            "resume": None
                        }

        # ===== 6. 移动 =====
        # 构建当前占用集合（防止重叠）
        occupied = {pos for rid, pos in responders}
        # 追踪本轮已经移动的（防止交换位置）
        moved_this_step = {}

        for rid, (rx, ry) in responders:
            if rid not in active_tasks:
                continue

            task = active_tasks[rid]
            tx, ty = task["target"]

            # --- 到达逻辑 ---
            if (rx, ry) == (tx, ty):
                if task["type"] == "rescue":
                    # 救出幸存者
                    for floor in MapDataServiceObj.map_obj.get_data():
                        if floor["name"] == floor_name:
                            floor["points"] = [p for p in floor["points"]
                                            if not (p.get("type") == "occupant" and p["x"] == tx and p["y"] == ty)]

                    if task["resume"]:
                        rx_tgt, ry_tgt = task["resume"]
                        if MapDataServiceObj.block_status[floor_name].get(f"{rx_tgt},{ry_tgt}") == "unwatched":
                            active_tasks[rid] = {
                                "type": "explore",
                                "target": task["resume"],
                                "resume": None
                            }
                        else:
                            active_tasks.pop(rid)
                    else:
                        active_tasks.pop(rid)

                elif task["type"] == "exit":
                    # 到达出口，停在出口位置
                    active_tasks.pop(rid)
                    # 不移除responder，停在出口
                    occupied.add((tx, ty))

                else:
                    # 探索任务到达，检查目标是否仍然有效
                    if MapDataServiceObj.block_status[floor_name].get(f"{tx},{ty}") == "unwatched":
                        # 目标有效，完成探索任务
                        active_tasks.pop(rid)
                    else:
                        # 目标已过时，取消任务
                        active_tasks.pop(rid)
                continue

            # --- A* 邻居选择 ---
            best_next = None
            min_astar_dist = float("inf")
            alternatives = []

            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
                nx, ny = rx + dx, ry + dy
                nk = f"{nx},{ny}"

                if nk not in MapDataServiceObj.block_status[floor_name]: continue

                is_entrance = (nx, ny) in exits

                # 出口/入口格子对 exit 任务放行，普通格子跳过 null
                if not (is_entrance and task["type"] == "exit"):
                    if MapDataServiceObj.block_status[floor_name][nk] == "null": continue

                # 碰撞检测：跳过已被其他搜救者占据的格子
                # 出口格子允许多人重合；已移走的占据者可以进入
                if (nx, ny) in occupied:
                    if is_entrance:
                        # 出口允许多人
                        pass
                    else:
                        # 检查占据者是否本轮已经移走
                        occupier_rid = None
                        for o_rid, o_pos in responders:
                            if o_pos == (nx, ny) and o_rid != rid:
                                occupier_rid = o_rid
                                break
                        if occupier_rid and occupier_rid in moved_this_step:
                            pass
                        else:
                            continue

                d = MapDataServiceObj.astar_distance(
                    floor_name,
                    (nx, ny),
                    [(tx, ty)]
                )

                if d is not None:
                    alternatives.append(((nx, ny), d))
                    if d < min_astar_dist:
                        min_astar_dist = d
                        best_next = (nx, ny)

            if best_next:
                # 移除旧位置占用，添加新位置占用
                occupied.discard((rx, ry))
                occupied.add(best_next)
                MapDataServiceObj.responders[floor_name][rid] = best_next
                moved_this_step[rid] = best_next
                last_positions[rid] = (rx, ry)
            elif alternatives:
                # 所有路径都被占据，选代价最低的那个（不会阻塞）
                alternatives.sort(key=lambda x: x[1])
                fallback = alternatives[0][0]
                occupied.discard((rx, ry))
                occupied.add(fallback)
                MapDataServiceObj.responders[floor_name][rid] = fallback
                moved_this_step[rid] = fallback
                last_positions[rid] = (rx, ry)
            else:
                # 无路可走，目标不可达
                if task["type"] == "exit":
                    # 出口不可达，尝试其他出口
                    other_exits = [e for e in exits if e != (tx, ty)]
                    if other_exits:
                        best_alt = min(
                            other_exits,
                            key=lambda e: MapDataServiceObj.astar_distance(floor_name, (rx, ry), [e]) or float("inf")
                        )
                        active_tasks[rid]["target"] = best_alt
                    else:
                        active_tasks.pop(rid, None)
                else:
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