from flask import (
    Flask,
    render_template,
    Response,
    request,
    jsonify,
    redirect
)

import os
import sys
import logging

import map
from map import MAP_DATA_LIST

HTTP_ADDR = os.getenv('FIRE_EVACUATION_HTTP_ADDR', '0.0.0.0')
HTTP_PORT = int(os.getenv('FIRE_EVACUATION_HTTP_PORT', '8000'))

import logging
logger = logging.getLogger("FIRE_EVACUATION")

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('draw/index.html')

@app.route('/create_map/', methods=["GET", "POST"])
def create_map_view():
        
    map.create_map("New Untitled Map", [])

    return redirect('/')

@app.route('/del_map/', methods = ["GET"])
def del_map():
    map.delete_map(request.args.get('id'))
    return redirect('/')

@app.route('/edit_map/', methods=["GET", "POST"])
def edit_map():
    if request.method == "POST":
        map_json = request.get_json()
        if not map_json:
            return jsonify({"error": "No JSON data received"}), 400

        map_id = map_json.get("id")
        for i in MAP_DATA_LIST:
            if i["id"] == map_id:
                
                i["map_obj"].change_name(map_json.get("title", i["map_obj"].get_name()))

                floors = map_json.get("floors", [])
                if isinstance(floors, list):
                    i["map_obj"].change_data_all(floors)

                i["name"] = i["map_obj"].get_name()
                map.save_map_list()
                break

        return redirect("/")

    return render_template('draw/edit_map.html')

@app.route('/api/map_data_list/')
def api_map_data_list():
    return jsonify(map.get_map_list())

@app.route('/api/get_map_data/')
def api_get_map_data():
    tmp = None
    for i in MAP_DATA_LIST:
        if i["id"] == request.args.get('id'):
            tmp = i
            break
    if not tmp:
        return jsonify({"error": "map not found"}), 404

    return jsonify({
        "title": tmp["name"],
        "id": tmp["id"],
        "data": tmp["map_obj"].get_data()
    })

log = logging.getLogger('werkzeug')
log.disabled = True
cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None
logger.info(f"Start Draw Server http://{HTTP_ADDR}:{HTTP_PORT} .")

app.run(HTTP_ADDR, HTTP_PORT)
