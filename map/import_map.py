import json

from . import MAP_DATA_LIST, MAP_DATA_ROOT, Map, save_map_list

import logging
logger = logging.getLogger("FIRE_EVACUATION")

logger.info("================= Import Map =================")

save_path = input(">>> Save Path (relative) <<< >>>>? ")

map_data_json = {}
with open(MAP_DATA_ROOT / save_path, "r") as fp:
    map_data_json = json.loads(fp.read())

map_obj = Map(map_id = map_data_json["id"], map_name = map_data_json["name"], map_data = map_data_json["data"])

MAP_DATA_LIST.append({
    "id": map_data_json["id"],
    "name": map_data_json["name"],
    "map_obj": map_obj,
})

save_map_list()

logger.info("Import Successful!")