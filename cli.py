import os
import click

import log_setting

@click.group()
def cli():
    ...

@click.command(help="Run draw map http server")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
def draw_map(debug, http_addr, http_port):
    """
    Run draw map http server
    """
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    import draw_map

@click.command(help="Export map")
def export_map():
    """
    Export map
    """
    from map import export_map

@click.command(help="Import map")
def import_map():
    """
    Import map
    """
    from map import import_map

@click.command(help="Replay map")
@click.option('--map-id', help="Use which map?", type = str)
@click.option('--record-id', help="Use which record?", type = str)
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
def replay(map_id, record_id, http_addr, http_port):
    """
    Replay map
    """
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_USE_RECORD_ID"] = record_id
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    import replay_service

@click.command(help="Calculate ANN Index (Average Nearest Neighbor)")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--no-record', is_flag=True, help="No Record")
@click.option('--dis-init_fire', is_flag=True, help="Disable init fire")
@click.option('--random-occupant', help="Random Occupants per trial", type=int, default=0)
@click.option('--map-id', help="Use which map?", type=str)
@click.option('--trials', help="Number of trials", type=int, default=10)
@click.option('--ann-min', help="Target ANN min (hunt mode: keep generating until ANN in [ann-min, ann-max])", type=float, default=None)
@click.option('--ann-max', help="Target ANN max (hunt mode: keep generating until ANN in [ann-min, ann-max])", type=float, default=None)
@click.option('--max-attempts', help="Max attempts in hunt mode (0 = unlimited)", type=int, default=0)
@click.option('--http-addr', help="HTTP Server Addr", default="0.0.0.0", type=str)
@click.option('--http-port', help="HTTP Server Port", default=8000, type=int)
def ann_calculation(debug, no_record, dis_init_fire, random_occupant, map_id, trials, ann_min, ann_max, max_attempts, http_addr, http_port):
    """
    Calculate ANN Index over multiple trials, or hunt for a specific ANN range.

    Hunt mode (--ann-min and --ann-max both set):
        Keep respawning occupants until ANN falls in [ann_min, ann_max],
        then save the map snapshot to ./map_snapshot/<timestamp>/map.json
    """
    import os, math, random, time, json, pathlib
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    os.environ["FIRE_EVACUATION_NO_RECORD"] = '1' if no_record else '0'
    os.environ["FIRE_EVACUATION_DISABLE_INIT_FIRE"] = '1' if dis_init_fire else '0'
    os.environ["FIRE_EVACUATION_DISABLE_INIT_PROBABILITY"] = '0'
    os.environ["FIRE_EVACUATION_RANDOM_OCCUPANT"] = '0'
    if not map_id:
        raise ValueError("No provided good map id!")
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    os.environ["FIRE_EVACUATION_SPEED"] = "0"
    os.environ["FIRE_EVACUATION_MOVE_AFTER"] = "999999"

    from map_data_service import MapDataServiceObj

    floor_name = MapDataServiceObj.map_obj.get_data()[0]["name"]

    # ===== HUNT MODE: --ann-min 和 --ann-max 都指定时 =====
    if ann_min is not None and ann_max is not None:
        print(f"\n{'='*80}")
        print(f"ANN Hunt Mode")
        print(f"Map: {MapDataServiceObj.map_obj.get_name()} (ID: {map_id})")
        print(f"Floor: {floor_name}")
        print(f"Occupants: {random_occupant}")
        print(f"Target ANN range: [{ann_min:.4f}, {ann_max:.4f}]")
        print(f"Max attempts: {max_attempts if max_attempts > 0 else 'unlimited'}")
        print(f"{'='*80}\n")

        # 一次运行一个文件夹，里面放多个命中快照
        run_timestamp = str(int(time.time()))
        snapshot_dir = pathlib.Path(__file__).parent / "map_snapshot" / run_timestamp
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        found_larger = False
        found_smaller = False
        hits = 0

        attempt = 0
        while True:
            attempt += 1
            MapDataServiceObj.respawn_occupants(floor_name, random_occupant)
            ann = MapDataServiceObj.calculate_ann_index(floor_name)

            if ann is None:
                print(f"  Attempt {attempt}: FAILED (N<2 or no weighted area)")
            else:
                is_larger = ann["ann_index"] >= ann_max
                is_smaller = ann["ann_index"] <= ann_min
                markers = []
                if is_larger:
                    markers.append("LARGER")
                if is_smaller:
                    markers.append("SMALLER")
                marker_str = " <<< " + "+".join(markers) if markers else ""
                print(f"  Attempt {attempt}: ANN={ann['ann_index']:.4f}  k={ann['k']:.4f}  k_exp={ann['k_expected']:.4f}  N={ann['N']}{marker_str}")

                if is_larger and not found_larger:
                    found_larger = True
                    hits += 1
                    file_name = f"{run_timestamp}_{hits}_larger.json"
                    snapshot_path = snapshot_dir / file_name
                    MapDataServiceObj.export_map_snapshot(floor_name, str(snapshot_path))
                    print(f"  -> Saved (larger): {snapshot_path}")

                if is_smaller and not found_smaller:
                    found_smaller = True
                    hits += 1
                    file_name = f"{run_timestamp}_{hits}_smaller.json"
                    snapshot_path = snapshot_dir / file_name
                    MapDataServiceObj.export_map_snapshot(floor_name, str(snapshot_path))
                    print(f"  -> Saved (smaller): {snapshot_path}")

            if max_attempts == 0:
                if found_larger and found_smaller:
                    print(f"\n  -> Both targets found (larger & smaller), stopping.")
                    break
            elif attempt >= max_attempts:
                break

        print(f"\n{'='*80}")
        print(f"HUNT COMPLETE")
        print(f"  Total attempts: {attempt}")
        print(f"  Hits:           {hits}")
        print(f"  Saved to:       {snapshot_dir}")
        print(f"{'='*80}\n")
        return

    # ===== NORMAL MODE: 多轮统计 =====
    print(f"\n{'='*80}")
    print(f"ANN Index Calculation")
    print(f"Map: {MapDataServiceObj.map_obj.get_name()} (ID: {map_id})")
    print(f"Floor: {floor_name}")
    print(f"Occupants per trial: {random_occupant}")
    print(f"Trials: {trials}")
    print(f"{'='*80}\n")

    # 预计算加权面积（固定不变）
    weighted_area = sum(
        w for w in MapDataServiceObj.cell_weight.get(floor_name, {}).values() if w > 0
    )
    print(f"Weighted Area: {weighted_area} cells\n")

    results = []
    for trial in range(trials):
        MapDataServiceObj.respawn_occupants(floor_name, random_occupant)
        ann = MapDataServiceObj.calculate_ann_index(floor_name)
        if ann is None:
            print(f"  Trial {trial + 1:2d}: FAILED (N={random_occupant}, need >= 2 occupants with weight>0 area)")
            results.append(None)
            continue
        results.append(ann)
        print(f"  Trial {trial + 1:2d}: k={ann['k']:.4f}  k_exp={ann['k_expected']:.4f}  "
              f"ANN={ann['ann_index']:.4f}  N={ann['N']}  rho={ann['rho']:.6f}")

    valid = [r for r in results if r is not None]
    if valid:
        avg_ann = sum(r["ann_index"] for r in valid) / len(valid)
        avg_k = sum(r["k"] for r in valid) / len(valid)
        avg_k_expected = sum(r["k_expected"] for r in valid) / len(valid)
        print(f"\n{'='*80}")
        print(f"SUMMARY ({len(valid)}/{trials} valid trials)")
        print(f"  Average k:          {avg_k:.4f}")
        print(f"  Average k_expected: {avg_k_expected:.4f}")
        print(f"  Average ANN Index:  {avg_ann:.4f}")
        print(f"  (ANN ~ 1 -> random distribution, ANN < 1 -> clustered, ANN > 1 -> dispersed)")
        print(f"{'='*80}\n")

@click.command(help="Test Smoke Distrubution")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--no-record', is_flag=True, help="Record")
@click.option('--dis-prob', is_flag=True, help="Disable init probability")
@click.option('--dis-init_fire', is_flag=True, help="Disable init fire")
@click.option('--random-occupant', help="Random Occupants", type = int, default = 0)
@click.option('--map-id', help="Use which map?", type = str)
@click.option('--use-old-fire-simulation', help="Use exist fire simulation record", default = "", type = str)
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
@click.option('--speed', help="Simulation speed multiplier", default = 1.0, type = float)
@click.option('--move-after', help="Move after what secound?", default = 0, type = int)
@click.option('--save-map', is_flag=True, help="Save initial random map as .pkl in map_record/")
def algorithm_simulation(debug, no_record, dis_prob, dis_init_fire, random_occupant, map_id, use_old_fire_simulation, http_addr, http_port, speed, move_after, save_map):
    """
    Algorithm Simulation
    """
    os.environ["FIRE_EVACUATION_DISABLE_INIT_PROBABILITY"] = '1' if dis_prob else '0'
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    os.environ["FIRE_EVACUATION_NO_RECORD"] = '1' if no_record else '0'
    os.environ["FIRE_EVACUATION_DISABLE_INIT_FIRE"] = '1' if dis_init_fire else '0'
    if not map_id:
        raise ValueError("No provided good map id!")
        exit(-1)
    os.environ["FIRE_EVACUATION_RANDOM_OCCUPANT"] = str(random_occupant)
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_USE_OLD_FIRE_SIMULATION"] = use_old_fire_simulation
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    os.environ["FIRE_EVACUATION_SPEED"] = str(speed)
    os.environ["FIRE_EVACUATION_MOVE_AFTER"] = str(move_after)
    os.environ["FIRE_EVACUATION_SAVE_MAP"] = '1' if save_map else '0'
    import algorithm_simulation

cli.add_command(draw_map)
cli.add_command(export_map)
cli.add_command(import_map)
cli.add_command(replay)
cli.add_command(ann_calculation)
cli.add_command(algorithm_simulation)

if __name__ == '__main__':
    cli()