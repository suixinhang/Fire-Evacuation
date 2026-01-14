import json

from . import MAP_DATA_LIST, MAP_DATA_ROOT

import logging
logger = logging.getLogger("FIRE_EVACUATION")

logger.info("================= Export Map =================")
for i in range(len(MAP_DATA_LIST)):
    logger.info(f"{i}. Map ID: {MAP_DATA_LIST[i]['map_obj'].get_id()}, Map Name: {MAP_DATA_LIST[i]['map_obj'].get_name()}.")

logger.info("")
map_index = int(input(">>> Map Index <<< >>>>? "))

map_data_json = {
    "id": MAP_DATA_LIST[map_index]['map_obj'].get_id(),
    "name": MAP_DATA_LIST[map_index]['map_obj'].get_name(),
    "data": MAP_DATA_LIST[map_index]['map_obj'].get_data(),
}

save_path = input(">>> Save Path (relative) <<< >>>>? ")

with open(MAP_DATA_ROOT / save_path, "w+") as fp:
    fp.write(json.dumps(map_data_json))
