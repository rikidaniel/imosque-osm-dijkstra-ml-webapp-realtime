@echo off
cd /d %~dp0
python scripts\build_osm_graph.py route --start-lat -6.1783 --start-lon 106.6319 --end-lat -6.2050 --end-lon 106.6500 --buffer-km 6
pause
