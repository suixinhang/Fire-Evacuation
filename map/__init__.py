import time
import json
import pickle
import pathlib

MAP_DATA_ROOT = pathlib.Path(__file__).parent.parent / 'map_data'
MAP_DATA_LIST = []

import logging
logger = logging.getLogger("FIRE_EVACUATION")

class Map:

    __map_id = ""
    __map_name = ""
    __map_data = {}
    __file_path = None

    def __init__(self, map_id=None, map_name="Unamed", map_data=None):
        self.__map_id = map_id or str(int(time.time()))
        self.__map_name = map_name
        self.__map_data = map_data or {}
        self.__file_path = MAP_DATA_ROOT / f"{self.__map_id}.pkl"
        self.open_file()

    def open_file(self):
        if self.__file_path.exists():
            try:
                with open(self.__file_path, "rb") as f:
                    data = pickle.load(f)
                    self.__map_id = data["map_id"]
                    self.__map_name = data["map_name"]
                    self.__map_data = data["map_data"]
            except:
                self.save_to_disk()
        else:
            self.save_to_disk()
    
    def save_to_disk(self):
        logger.debug(f"Auto save map.")
        try:
            with open(self.__file_path, "wb") as f:
                pickle.dump({
                    "map_id": self.__map_id,
                    "map_name": self.__map_name,
                    "map_data": self.__map_data
                }, f)
        except:
            pass

    def get_id(self):
        return self.__map_id
    
    def get_name(self):
        return self.__map_name
    
    def get_data(self):
        return self.__map_data
    
    def change_name(self, new_name):
        self.__map_name = new_name
        self.save_to_disk()
    
    def change_data_all(self, data):
        self.__map_data = data
        self.save_to_disk()
    
    def change_data_for_floor(self, floor, data):
        self.__map_data[floor] = data
        self.save_to_disk()
    
    def del_data_floor(self, floor):
        if floor in self.__map_data:
            del self.__map_data[floor]
            self.save_to_disk()
    
    def clear_data(self):
        self.__map_data = {}
        self.save_to_disk()

logger.debug(f"Load map list...")
if not (MAP_DATA_ROOT / 'list.json').exists():
    with open(MAP_DATA_ROOT / 'list.json', 'w', encoding='utf-8') as fp:
        fp.write("[]")
else:
    with open(MAP_DATA_ROOT / 'list.json', 'r', encoding='utf-8') as fp:
        MAP_DATA_LIST = json.loads(fp.read())

logger.debug(f"Load maps...")
for i in range(len(MAP_DATA_LIST)):
    MAP_DATA_LIST[i]["map_obj"] = Map(MAP_DATA_LIST[i]["id"], MAP_DATA_LIST[i]["name"])

def get_map_list():
    logger.debug(f"Get map list.")
    tmp = []
    for i in MAP_DATA_LIST:
        tmp.append({"id": i["id"], "name": i["name"]})
    return tmp

def save_map_list():
    logger.debug(f"Save map list.")
    tmp = []
    for i in MAP_DATA_LIST:
        tmp.append({"id": i["id"], "name": i["name"]})
    with open(MAP_DATA_ROOT / 'list.json', 'w', encoding='utf-8') as fp:
        fp.write(json.dumps(tmp))

def create_map(map_name, map_data):
    map_obj = Map(map_name=map_name, map_data=map_data)
    MAP_DATA_LIST.append({
        "id": map_obj.get_id(),
        "name": map_obj.get_name(),
        "map_obj": map_obj,
    })
    logger.info(f"Create map, name: {map_name}, id: {map_obj.get_id()}.")
    save_map_list()

def delete_map(map_id):
    logger.info(f"Delete map, id: {map_id}")
    global MAP_DATA_LIST
    for i, item in enumerate(MAP_DATA_LIST):
        if item["id"] == map_id:

            file_path = MAP_DATA_ROOT / f"{map_id}.pkl"
            if file_path.exists():
                try:
                    file_path.unlink()
                except:
                    pass

            del MAP_DATA_LIST[i]
            save_map_list()
            return True
    return False
