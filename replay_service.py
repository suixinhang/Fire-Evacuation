import os
import json
import time
import pathlib
from tqdm import tqdm

from map_data_service import MapDataServiceObj


class ReplayRunner:
    def __init__(self, record_id):
        base_path = pathlib.Path(__file__).parent / "record"

        filename = record_id if record_id.endswith(".txt") else record_id + ".txt"
        self.filepath = base_path / filename

        if not self.filepath.exists():
            raise FileNotFoundError(f"Record file not found: {self.filepath}")

        print(f"[Replay] Loading {self.filepath}")

        self.frames = []
        self.index = 0

        self.load()

    # ================= 加载 =================
    def load(self):
        count = 0

        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    frame = json.loads(line)
                    self.frames.append(frame)
                    count += 1
                except json.JSONDecodeError:
                    # 自动跳过损坏帧（关键稳定性）
                    continue

        print(f"[Replay] Loaded {count} frames")

    # ================= 应用一帧 =================
    def apply_frame(self, frame):
        # ===== 1. 恢复地图结构（block_type）=====
        if "block_type" in frame:
            try:
                MapDataServiceObj.map_obj.change_data_all(frame["block_type"], is_save=False)
            except Exception as e:
                print(f"[Replay] map_obj.set_data failed: {e}")

        # ===== 2. 恢复状态 =====
        MapDataServiceObj.block_status = frame.get("block_status", {})
        MapDataServiceObj.smoke_density = frame.get("smoke_density", {})
        MapDataServiceObj.probability_map = frame.get("probability_map", {})
        MapDataServiceObj.responders = frame.get("responders", {})
        MapDataServiceObj.global_time = frame.get("time", 0.0)

        # ===== 3. 恢复 occupants（关键）=====
        MapDataServiceObj.occupants = frame.get("occupants", {})

        # ===== 4. 同步 occupants → map（用于前端显示）=====
        if hasattr(MapDataServiceObj, "sync_occupants_to_map"):
            for fname in MapDataServiceObj.occupants:
                try:
                    MapDataServiceObj.sync_occupants_to_map(fname)
                except Exception as e:
                    print(f"[Replay] sync occupants failed: {e}")

    # ================= 播放 =================
    def run(self, dt=0.2):
        if not self.frames:
            print("[Replay] No frames to replay")
            return

        total = len(self.frames)

        print(f"[Replay] Start playing ({total} frames)")

        pbar = tqdm(total=total, desc="Replaying", unit="frame")

        while True:
            frame = self.frames[self.index]

            # 应用帧
            self.apply_frame(frame)

            # UI信息
            pbar.update(1)
            pbar.set_postfix({
                "step": frame.get("step"),
                "time": round(frame.get("time", 0), 2)
            })

            # 前进
            self.index += 1

            # 循环播放
            if self.index >= total:
                self.index = 0
                pbar.reset()

            time.sleep(dt)


record_id = os.getenv("FIRE_EVACUATION_USE_RECORD_ID")

if not record_id:
    raise ValueError("Need FIRE_EVACUATION_USE_RECORD_ID")

runner = ReplayRunner(record_id)
runner.run(1)