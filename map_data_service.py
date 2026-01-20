import os
import sys
import random
import threading

from flask import Flask, render_template, request, jsonify
from map import Map

import logging
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

        self.block_status = {}
        self.smoke_density = {}

        t = threading.Thread(target=self.web_server, daemon=True)
        t.start()
        logger.info("Started Map Web Server Thread.")

    def init_runtime_data(self):
        self.block_status.clear()
        self.smoke_density.clear()

        floors = self.map_obj.get_data() or []
        for floor in floors:
            fname = floor.get("name")
            points = floor.get("points", [])

            self.block_status[fname] = {}
            self.smoke_density[fname] = {}

            if not points:
                continue

            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]

            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            point_map = {}
            for p in points:
                point_map[(p["x"], p["y"])] = p

            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    key = f"{x},{y}"

                    if (x, y) in point_map:
                        p = point_map[(x, y)]
                        if p.get("type") in ("wall", "stair"):
                            self.block_status[fname][key] = "null"
                            self.smoke_density[fname][key] = -1
                        else:
                            self.block_status[fname][key] = "unwatched"
                            self.smoke_density[fname][key] = random.randint(0,100)
                    else:
                        self.block_status[fname][key] = "unwatched"
                        self.smoke_density[fname][key] = random.randint(0,100)

    def web_server(self):
        HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
        HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8001'))

        app = Flask(__name__)
        self.init_runtime_data()

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

        logger.info(f"Map Runtime Server http://{HTTP_ADDR}:{HTTP_PORT}")
        app.run(HTTP_ADDR, HTTP_PORT)


MapDataServiceObj = MapDataService()
