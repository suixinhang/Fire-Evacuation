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

logger = logging.getLogger("FIRE_EVACUATION")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
if os.getenv('FIRE_EVACUATION_DRAW_DEBUG', '0') == '0':
    console_handler.setLevel(logging.INFO)
else:
    console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s](%(module)s)[%(levelname)s] %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


app = Flask(__name__)

@app.route('/')
def index():
    return render_template('draw/index.html')

@app.route('/create_map/', methods=["GET", "POST"])
def create_map_view():
    if request.method == "POST":
        map_json = request.get_json()
        if not map_json:
            return jsonify({"error": "No JSON data received"}), 400
        
        title = map_json.get("title", "Untitled Map")
        floors = map_json.get("floors", [])
        if not isinstance(floors, list):
            return jsonify({"error": "floors must be a list"}), 400
        
        map.create_map(title, floors)

        return jsonify({"success": True, "redirect": "/"})
    
    return render_template('draw/create_map.html')

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
logger.info(f"Start Draw Window.")

app.run("0.0.0.0", 8000, debug=True)
