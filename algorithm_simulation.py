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

# 每个搜救者认领的 uncertain 区域（rid → set of cell keys）
region_claims = {}

# ===== 标记 uncertain =====
def mark_uncertain_circle(center, radius, rid):
    cx, cy = center
    claimed = set()
    for key in MapDataServiceObj.block_status[floor_name]:
        if MapDataServiceObj.block_status[floor_name][key] != "unwatched":
            continue
        x, y = map(int, key.split(","))
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
            # 不标记目标中心本身，保持 unwatched 使任务有效
            if (x, y) == (cx, cy):
                claimed.add(key)
                continue
            MapDataServiceObj.block_status[floor_name][key] = "uncertain"
            claimed.add(key)
    region_claims[rid] = claimed

def release_claims(rid):
    """释放某个搜救者的所有探索预留"""
    if rid not in region_claims:
        return
    for key in region_claims[rid]:
        if MapDataServiceObj.block_status[floor_name].get(key) == "uncertain":
            MapDataServiceObj.block_status[floor_name][key] = "unwatched"
    del region_claims[rid]

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

# ===== 匈牙利算法：全局最优任务分配 =====
INF_COST = 1e12

def hungarian_assignment(cost):
    """
    解决指派问题：将 n 个工人分配给 m 个任务 (n <= m)，最小化总代价。
    cost: n x m 矩阵，cost[i][j] = 工人i完成任务j的代价。
          不可行的分配使用 INF_COST 表示。
    返回: [(worker_idx, job_idx), ...] 最优分配列表。
    算法: O(n^2 * m) 匈牙利算法（Kuhn-Munkres）。
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0]) if cost else 0
    if m == 0:
        return []

    size = max(n, m)
    # 构建 size x size 方阵
    a = []
    for i in range(size):
        row = []
        for j in range(size):
            if i < n and j < m:
                val = cost[i][j]
                if val >= INF_COST * 0.5:
                    val = INF_COST
            elif i >= n:
                val = 0.0  # 虚拟工人，代价为0（允许部分工人不分配）
            else:
                val = INF_COST  # 虚拟任务，不可达
            row.append(val)
        a.append(row)

    # 标准匈牙利算法 O(size^3)
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)

    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minv = [INF_COST] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF_COST
            j1 = 0
            for j in range(1, size + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    # 提取分配
    assignment = []
    for j in range(1, size + 1):
        worker = p[j]
        if worker != 0 and worker <= n and j <= m:
            wi = worker - 1
            ji = j - 1
            if cost[wi][ji] < INF_COST * 0.5:
                assignment.append((wi, ji))

    return assignment

# ================= 主循环 =================
while True:
    start_time = time.time()
    if int(os.getenv("FIRE_EVACUATION_MOVE_AFTER")) - step_counter <= 0:
        MapDataServiceObj.active_tasks = active_tasks

        responders = MapDataServiceObj.get_responders(floor_name)

        # ===== 1. 视野更新 =====
        vis_map = MapDataServiceObj.compute_visibility_for_responders(
            floor_name,
            responders
        )

        # 所有被视野覆盖的格子 → watched（包括 uncertain → watched）
        for rid, (rx, ry) in responders:
            MapDataServiceObj.block_status[floor_name][rid] = "watched"

        for rid, visible in vis_map.items():
            for key in visible:
                MapDataServiceObj.block_status[floor_name][key] = "watched"

        # 清理被 watched 覆盖后残留的 uncertain（仅清理已完成探索的区域）
        # 不再全局清除 uncertain，让探索预留标记跨步持久化
        for k in MapDataServiceObj.block_status[floor_name]:
            if MapDataServiceObj.block_status[floor_name][k] == "uncertain":
                # 如果附近 3 格内全部是 watched，则清理该 uncertain
                x, y = map(int, k.split(","))
                all_watched = True
                for dx, dy in [(0,0),(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                    nk = f"{x+dx},{y+dy}"
                    ns = MapDataServiceObj.block_status[floor_name].get(nk)
                    if ns and ns not in ("watched", "null"):
                        all_watched = False
                        break
                if all_watched:
                    MapDataServiceObj.block_status[floor_name][k] = "unwatched"

        current_real_occupants = get_occupants_set()

        # ----- 取消已经过时的探索任务（目标区域已被探索）-----
        # 注意：uncertain 表示被其他搜救者预留，不算过时；只有 watched/null 才取消
        for rid in list(active_tasks.keys()):
            task = active_tasks[rid]
            if task["type"] == "explore":
                tx, ty = task["target"]
                status = MapDataServiceObj.block_status[floor_name].get(f"{tx},{ty}")
                if status not in ("unwatched", "uncertain"):
                    release_claims(rid)
                    active_tasks.pop(rid)

        # ===== 3. 贪心链式救援任务分配 =====
        # 第一步：锁定 A* 距离最短的 (救援者, 幸存者) 对
        # 后续步骤：贪心分配剩余幸存者，已分配的救援者可链式救援
        #   增量代价 = 从最后一位幸存者到新幸存者的 A* 距离
        #   未分配救援者的代价 = 直接 A* 距离
        all_responders_dict = {rid: (rx, ry) for rid, (rx, ry) in responders}
        responder_keys = list(all_responders_dict.keys())

        # 收集所有已知且可见的幸存者位置
        all_known_occs = get_occupants_set()
        available_occs = []
        for occ_key in all_known_occs:
            if MapDataServiceObj.block_status[floor_name].get(occ_key) == "watched":
                ox, oy = map(int, occ_key.split(","))
                available_occs.append((ox, oy))

        if available_occs and responder_keys:
            n_rescuers = len(responder_keys)
            n_occs = len(available_occs)

            # 预计算所有救援者→幸存者距离
            rescuer_occ_dist = {}
            for ri in range(n_rescuers):
                rx, ry = all_responders_dict[responder_keys[ri]]
                for oi, (ox, oy) in enumerate(available_occs):
                    d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(ox, oy)])
                    rescuer_occ_dist[(ri, oi)] = d if d is not None else INF_COST

            # 预计算所有幸存者→幸存者距离（用于链式救援的增量代价）
            occ_occ_dist = {}
            for oi in range(n_occs):
                for oj in range(n_occs):
                    if oi == oj:
                        occ_occ_dist[(oi, oj)] = 0
                    elif (oj, oi) in occ_occ_dist:
                        occ_occ_dist[(oi, oj)] = occ_occ_dist[(oj, oi)]
                    else:
                        ox, oy = available_occs[oi]
                        ox2, oy2 = available_occs[oj]
                        d = MapDataServiceObj.astar_distance(floor_name, (ox, oy), [(ox2, oy2)])
                        occ_occ_dist[(oi, oj)] = d if d is not None else INF_COST

            # 贪心序列分配
            assigned_chain = {}  # rid -> [oi1, oi2, ...] （救援链，按顺序）
            unassigned_occs = set(range(n_occs))

            # 第一步：锁定全局 A* 距离最短的 (救援者, 幸存者) 对
            best_cost = INF_COST
            best_ri = None
            best_oi = None
            for ri in range(n_rescuers):
                for oi in unassigned_occs:
                    cost = rescuer_occ_dist.get((ri, oi), INF_COST)
                    if cost < best_cost:
                        best_cost = cost
                        best_ri = ri
                        best_oi = oi

            if best_ri is not None and best_cost < INF_COST * 0.5:
                rid = responder_keys[best_ri]
                assigned_chain[rid] = [best_oi]
                unassigned_occs.remove(best_oi)

                # 后续步骤：贪心分配剩余幸存者
                while unassigned_occs:
                    best_cost = INF_COST
                    best_rid = None
                    best_oi = None

                    for oi in unassigned_occs:
                        # 已分配的救援者：增量代价 = 链末端幸存者 → 新幸存者
                        for rid in assigned_chain:
                            last_oi = assigned_chain[rid][-1]
                            chain_cost = occ_occ_dist.get((last_oi, oi), INF_COST)
                            if chain_cost < best_cost:
                                best_cost = chain_cost
                                best_rid = rid
                                best_oi = oi

                        # 未分配的救援者：直接 A* 距离
                        for ri in range(n_rescuers):
                            rid_candidate = responder_keys[ri]
                            if rid_candidate in assigned_chain:
                                continue
                            cost = rescuer_occ_dist.get((ri, oi), INF_COST)
                            if cost < best_cost:
                                best_cost = cost
                                best_rid = rid_candidate
                                best_oi = oi

                    if best_rid is None or best_cost >= INF_COST * 0.5:
                        break

                    if best_rid in assigned_chain:
                        assigned_chain[best_rid].append(best_oi)
                    else:
                        assigned_chain[best_rid] = [best_oi]
                    unassigned_occs.remove(best_oi)

            # 应用分配结果到 active_tasks
            for rid in responder_keys:
                old_task = active_tasks.get(rid)

                if rid in assigned_chain:
                    chain_occs = assigned_chain[rid]
                    chain_positions = [available_occs[oi] for oi in chain_occs]
                    target = chain_positions[0]
                    chain_rest = chain_positions[1:]

                    # 提取旧的 resume（探索目标）
                    resume = None
                    if old_task:
                        if old_task.get("type") == "rescue":
                            resume = old_task.get("resume")
                        elif old_task.get("type") == "explore":
                            resume = old_task.get("target")

                    active_tasks[rid] = {
                        "type": "rescue",
                        "target": target,
                        "resume": resume,
                        "chain": chain_rest
                    }
                else:
                    # 未被分配到幸存者 → 回退
                    if old_task and old_task.get("type") == "rescue":
                        resume_tgt = old_task.get("resume")
                        if resume_tgt:
                            rx_tgt, ry_tgt = resume_tgt
                            status = MapDataServiceObj.block_status[floor_name].get(f"{rx_tgt},{ry_tgt}")
                            if status in ("unwatched", "uncertain"):
                                active_tasks[rid] = {
                                    "type": "explore",
                                    "target": resume_tgt,
                                    "resume": None
                                }
                            else:
                                release_claims(rid)
                                active_tasks.pop(rid, None)
                        else:
                            release_claims(rid)
                            active_tasks.pop(rid, None)
                    elif old_task and old_task.get("type") == "explore":
                        pass  # 保持探索任务不变
                    else:
                        active_tasks.pop(rid, None)
        else:
            # 没有可用的幸存者 → 释放所有救援任务，回退到探索
            for rid in list(active_tasks.keys()):
                task = active_tasks[rid]
                if task["type"] == "rescue":
                    resume_tgt = task.get("resume")
                    if resume_tgt:
                        rx_tgt, ry_tgt = resume_tgt
                        status = MapDataServiceObj.block_status[floor_name].get(f"{rx_tgt},{ry_tgt}")
                        if status in ("unwatched", "uncertain"):
                            active_tasks[rid] = {
                                "type": "explore",
                                "target": resume_tgt,
                                "resume": None
                            }
                        else:
                            release_claims(rid)
                            active_tasks.pop(rid)
                    else:
                        release_claims(rid)
                        active_tasks.pop(rid)

        # ===== 4. 全局最优探索任务分配（匈牙利算法）=====
        free_responders = [rid for rid, pos in responders if rid not in active_tasks]

        if free_responders:
            regions = compute_regions()
            candidates = build_candidates(regions)

            if candidates:
                responders_dict = dict(responders)

                # === 构建代价矩阵 ===
                # 代价 = distance / area（越小越好）
                cost_matrix = []
                for rid in free_responders:
                    rx, ry = responders_dict[rid]
                    row = []
                    for (tx, ty, area) in candidates:
                        d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(tx, ty)])
                        if d is not None:
                            row.append(d / (area + 1e-5))
                        else:
                            row.append(INF_COST)
                    cost_matrix.append(row)

                # === 匈牙利算法求全局最优 ===
                opt_assignments = hungarian_assignment(cost_matrix)

                # === 应用分配 ===
                for ri, ci in opt_assignments:
                    if cost_matrix[ri][ci] >= INF_COST * 0.5:
                        continue
                    rid = free_responders[ri]
                    tx, ty, area = candidates[ci]

                    active_tasks[rid] = {
                        "type": "explore",
                        "target": (tx, ty),
                        "resume": None
                    }

                    vis_area = len(vis_map.get(rid, []))
                    radius = max(1, int(math.sqrt(vis_area / math.pi)))
                    mark_uncertain_circle((tx, ty), radius, rid)

        # ===== 5. 空闲救援者 → 立即前往最近的出口撤离 =====
        all_occupants_now = get_occupants_set()
        exits = MapDataServiceObj.entrances.get(floor_name, [])

        if exits:
            for rid, (rx, ry) in responders:
                if rid in active_tasks:
                    continue  # 有任务在身，不撤离
                # 已在出口位置的救援者，不再重复分配
                if (rx, ry) in exits:
                    continue
                # 空闲救援者 → 找最近出口
                best_exit = None
                best_exit_dist = float("inf")
                for ex, ey in exits:
                    d = MapDataServiceObj.astar_distance(floor_name, (rx, ry), [(ex, ey)])
                    if d is not None and d < best_exit_dist:
                        best_exit_dist = d
                        best_exit = (ex, ey)

                if best_exit:
                    release_claims(rid)
                    active_tasks[rid] = {
                        "type": "exit",
                        "target": best_exit,
                        "resume": None
                    }

        # ===== 6. 全部救出后 → 全体寻找最近的出口撤离 =====
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
                        release_claims(rid)
                        active_tasks[rid] = {
                            "type": "exit",
                            "target": best_exit,
                            "resume": None
                        }

        # ===== 7. 移动 =====
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

                    # 检查救援链中是否还有下一个幸存者
                    chain = task.get("chain", [])
                    if chain:
                        # 继续救援链中的下一个幸存者
                        next_target = chain[0]
                        next_chain = chain[1:]
                        active_tasks[rid] = {
                            "type": "rescue",
                            "target": next_target,
                            "resume": task.get("resume"),
                            "chain": next_chain
                        }
                    elif task.get("resume"):
                        rx_tgt, ry_tgt = task["resume"]
                        status = MapDataServiceObj.block_status[floor_name].get(f"{rx_tgt},{ry_tgt}")
                        if status in ("unwatched", "uncertain"):
                            active_tasks[rid] = {
                                "type": "explore",
                                "target": task["resume"],
                                "resume": None
                            }
                        else:
                            release_claims(rid)
                            active_tasks.pop(rid)
                    else:
                        release_claims(rid)
                        active_tasks.pop(rid)

                elif task["type"] == "exit":
                    # 到达出口，停在出口位置
                    active_tasks.pop(rid)
                    release_claims(rid)
                    # 不移除responder，停在出口
                    occupied.add((tx, ty))

                else:
                    # 探索任务到达，释放预留，检查目标是否仍然有效
                    release_claims(rid)
                    status = MapDataServiceObj.block_status[floor_name].get(f"{tx},{ty}")
                    if status in ("unwatched", "uncertain"):
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
                        release_claims(rid)
                        active_tasks.pop(rid, None)
                else:
                    release_claims(rid)
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