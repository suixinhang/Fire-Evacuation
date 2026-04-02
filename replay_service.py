import os
import time
import pathlib
from tqdm import tqdm

from map_data_service import MapDataServiceObj


class ReplayReader:
    def __init__(self, record_id):
        self.frames = []
        self.index = 0

        base_path = pathlib.Path(__file__).parent / "record"

        if record_id.endswith(".txt"):
            filename = record_id
        else:
            filename = record_id + ".txt"

        self.filepath = base_path / filename

        if not self.filepath.exists():
            raise FileNotFoundError(f"Record file not found: {self.filepath}")

        print(f"[Replay] Loading: {self.filepath}")

        self.load()

    def load(self):
        with open(self.filepath, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]

        i = 0
        while i < len(lines):
            if not lines[i].startswith("STEP:"):
                i += 1
                continue

            step = int(lines[i].split(":")[1])
            t = float(lines[i + 1].split(":")[1])
            data_line = lines[i + 2][5:]

            self.frames.append(self.parse_frame(step, t, data_line))
            i += 4

        print(f"[Replay] Loaded {len(self.frames)} frames")

    def parse_frame(self, step, t, data_line):
        block_status = {}
        smoke_density = {}
        probability_map = {}

        for floor_block in data_line.split("||"):
            if "|" not in floor_block:
                continue

            fname, cells_str = floor_block.split("|", 1)

            block_status[fname] = {}
            smoke_density[fname] = {}
            probability_map[fname] = {}

            for cell in cells_str.split(";"):
                cell = cell.strip()
                if not cell:
                    continue

                try:
                    key, status, smoke, prob = cell.split(":")
                    block_status[fname][key] = status
                    smoke_density[fname][key] = float(smoke)
                    probability_map[fname][key] = float(prob)
                except:
                    continue

        return {
            "step": step,
            "time": t,
            "block_status": block_status,
            "smoke_density": smoke_density,
            "probability_map": probability_map
        }

    def next_frame(self):
        if not self.frames:
            return None

        frame = self.frames[self.index]
        self.index += 1
        return frame

    def reset(self):
        self.index = 0



record_id = os.getenv('FIRE_EVACUATION_USE_RECORD_ID')
if not record_id:
    raise ValueError("Need FIRE_EVACUATION_USE_RECORD_ID")

reader = ReplayReader(record_id)

dt = 0.2

print("[Replay] Starting...")

pbar = tqdm(total=len(reader.frames), desc="Replaying", unit="frame")

while True:
    frame = reader.next_frame()

    if frame is None:
        reader.reset()
        pbar.reset()
        continue

    MapDataServiceObj.block_status = frame["block_status"]
    MapDataServiceObj.smoke_density = frame["smoke_density"]
    MapDataServiceObj.probability_map = frame["probability_map"]
    MapDataServiceObj.global_time = frame["time"]

    pbar.update(1)
    pbar.set_postfix({
        "step": frame["step"],
        "time": round(frame["time"], 2)
    })

    time.sleep(dt)