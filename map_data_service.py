import os
import sys
import threading

from flask import (
    Flask,
    render_template,
    Response,
    request,
    jsonify,
    redirect
)

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
        HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
        HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8000'))

        USE_MAP_ID = os.getenv('FIRE_EVACUATION_USE_MAP_ID', None)
        if not USE_MAP_ID:
            raise ValueError("No provided good map id!")
            exit(-1)
        
        self.map_obj = Map(map_id=USE_MAP_ID)

        map_webserver_thread = threading.Thread(target=self.web_server)
        map_webserver_thread.daemon = True
        map_webserver_thread.start()
        logger.info(f"Started Map Web Server Thread.")
    
    def web_server(self):
        HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
        HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8000'))

        app = Flask(__name__)

        self.block_status = []

        log = logging.getLogger('werkzeug')
        log.disabled = True
        cli = sys.modules['flask.cli']
        cli.show_server_banner = lambda *x: None
        logger.info(f"Start Map Web Server http://{HTTP_ADDR}:{HTTP_PORT} .")

        @app.route("/")
        def index():
            return render_template('map_service/index.html')

        @app.route("/api/block_type", methods=["GET"])
        def api_block_type_get():
            data = self.map_obj.get_data()
            if data is None:
                return jsonify([])
            return jsonify(data)

        @app.route("/api/block_type", methods=["POST"])
        def api_block_type_post():
            body = request.get_json(force=True, silent=True)
            if body is None:
                return jsonify({"status": "error"}), 400
            self.map_obj.change_data_all(body)
            return jsonify({"status": "ok"})

        @app.route("/api/block_status", methods=["GET"])
        def api_block_status_get():
            return jsonify(self.block_status)

        app.run(HTTP_ADDR, HTTP_PORT)

MapDataServiceObj = MapDataService()