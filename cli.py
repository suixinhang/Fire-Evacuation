import os
import click

import log_setting

@click.group()
def cli():
    ...

@click.command(help="Run draw map http server")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
def draw_map(debug, http_addr, http_port):
    """
    Run draw map http server
    """
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    import draw_map

@click.command(help="Export map")
def export_map():
    """
    Export map
    """
    from map import export_map

@click.command(help="Import map")
def import_map():
    """
    Import map
    """
    from map import import_map

@click.command(help="Replay map")
@click.option('--map-id', help="Use which map?", type = str)
@click.option('--record-id', help="Use which record?", type = str)
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
def replay(map_id, record_id, http_addr, http_port):
    """
    Replay map
    """
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_USE_RECORD_ID"] = record_id
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    import replay_service

@click.command(help="Test Smoke Distrubution")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--no-record', is_flag=True, help="Record")
@click.option('--dis-prob', is_flag=True, help="Disable init probability")
@click.option('--dis-init_fire', is_flag=True, help="Disable init fire")
@click.option('--random-occupant', help="Random Occupants", type = int, default = 0)
@click.option('--map-id', help="Use which map?", type = str)
@click.option('--use-old-fire-simulation', help="Use exist fire simulation record", default = "", type = str)
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
@click.option('--speed', help="Simulation speed multiplier", default = 1.0, type = float)
@click.option('--move-after', help="Move after what secound?", default = 0, type = int)
def algorithm_simulation(debug, no_record, dis_prob, dis_init_fire, random_occupant, map_id, use_old_fire_simulation, http_addr, http_port, speed, move_after):
    """
    Algorithm Simulation
    """
    os.environ["FIRE_EVACUATION_DISABLE_INIT_PROBABILITY"] = '1' if dis_prob else '0'
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    os.environ["FIRE_EVACUATION_NO_RECORD"] = '1' if no_record else '0'
    os.environ["FIRE_EVACUATION_DISABLE_INIT_FIRE"] = '1' if dis_init_fire else '0'
    if not map_id:
        raise ValueError("No provided good map id!")
        exit(-1)
    os.environ["FIRE_EVACUATION_RANDOM_OCCUPANT"] = str(random_occupant)
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_USE_OLD_FIRE_SIMULATION"] = use_old_fire_simulation
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    os.environ["FIRE_EVACUATION_SPEED"] = str(speed)
    os.environ["FIRE_EVACUATION_MOVE_AFTER"] = str(move_after)
    import algorithm_simulation

cli.add_command(draw_map)
cli.add_command(export_map)
cli.add_command(import_map)
cli.add_command(replay)
cli.add_command(algorithm_simulation)

if __name__ == '__main__':
    cli()