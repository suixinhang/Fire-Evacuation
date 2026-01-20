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

@click.command(help="Test Smoke Distrubution")
@click.option('--debug', is_flag=True, help="Debug")
@click.option('--map-id', help="Use which map?", type = str)
@click.option('--http-addr', help="HTTP Server Addr", default = "0.0.0.0", type = str)
@click.option('--http-port', help="HTTP Server Port", default = 8000, type = int)
def test_smoke(debug, map_id, http_addr, http_port):
    """
    Test Smoke Distrubution
    """
    os.environ["FIRE_EVACUATION_DEBUG"] = '1' if debug else '0'
    if not map_id:
        raise ValueError("No provided good map id!")
        exit(-1)
    os.environ["FIRE_EVACUATION_USE_MAP_ID"] = map_id
    os.environ["FIRE_EVACUATION_HTTP_ADDR"] = http_addr
    os.environ["FIRE_EVACUATION_HTTP_PORT"] = str(http_port)
    import smoke_distrubution

cli.add_command(draw_map)
cli.add_command(export_map)
cli.add_command(import_map)
cli.add_command(test_smoke)

if __name__ == '__main__':
    cli()