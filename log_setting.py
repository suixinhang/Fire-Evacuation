import os
import logging

logger = logging.getLogger("FIRE_EVACUATION")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
if os.getenv('FIRE_EVACUATION_DEBUG', '0') == '0':
    console_handler.setLevel(logging.INFO)
else:
    console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s](%(module)s)[%(levelname)s] %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)