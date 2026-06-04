"""
将 map_snapshot 目录中的 JSON 地图快照转换为 .pkl 格式并注册到 map_data。
用法: python snapshot_to_pkl.py <snapshot_json_path> [--map-name <新名称>]
示例: python snapshot_to_pkl.py map_snapshot/1780499442/1780499442_1_smaller.json
"""
import sys
import json
import pickle
import pathlib


def convert_snapshot_to_pkl(json_path: str, new_name: str = None):
    json_path = pathlib.Path(json_path)
    if not json_path.exists():
        print(f"[ERROR] 文件不存在: {json_path}")
        sys.exit(1)

    # 读取 JSON 快照
    with open(json_path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    # 使用文件名（不含扩展名）作为新的 map_id，
    # 这样每个 snapshot 成为独立的新地图，保留 occupants 的随机分布
    map_id = json_path.stem  # e.g. "1780404439_1_larger"

    base_name = snapshot.get("name", "Unnamed")
    map_name = new_name or base_name
    map_data = snapshot.get("data", [])

    # 输出 .pkl 到 map_data 目录
    map_data_dir = pathlib.Path(__file__).parent / "map_data"
    map_data_dir.mkdir(parents=True, exist_ok=True)
    pkl_path = map_data_dir / f"{map_id}.pkl"

    with open(pkl_path, "wb") as f:
        pickle.dump({
            "map_id": map_id,
            "map_name": map_name,
            "map_data": map_data
        }, f)

    print(f"[OK] 已保存 PKL: {pkl_path}")
    print(f"     map_id  : {map_id}")
    print(f"     map_name: {map_name}")
    print(f"     points  : {sum(len(f.get('points', [])) for f in map_data)} 个")

    # 注册到 map_data list.json
    list_path = map_data_dir / "list.json"
    map_list = []
    if list_path.exists():
        try:
            with open(list_path, "r", encoding="utf-8") as f:
                map_list = json.load(f)
        except:
            map_list = []

    # 去重：如果相同 id 已存在则跳过
    existing = any(item.get("id") == map_id for item in map_list)
    if not existing:
        map_list.append({"id": map_id, "name": map_name})
        with open(list_path, "w", encoding="utf-8") as f:
            json.dump(map_list, f, ensure_ascii=False, indent=2)
        print(f"[OK] 已注册到 map_data/list.json")
    else:
        print(f"[INFO] map_id={map_id} 已在 list.json 中，跳过注册")

    return str(pkl_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="将 map_snapshot JSON 转换为 PKL 并注册")
    parser.add_argument("snapshot_path", help="snapshot JSON 文件路径")
    parser.add_argument("--map-name", default=None, help="可选：自定义地图名称")
    args = parser.parse_args()

    convert_snapshot_to_pkl(args.snapshot_path, args.map_name)