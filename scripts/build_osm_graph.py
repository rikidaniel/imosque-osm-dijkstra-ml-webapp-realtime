from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backend.app.infrastructure.services.osm_graph import DEFAULT_GRAPHML, build_osm_graph_for_bbox, build_osm_graph_for_route  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Download/cache OpenStreetMap road graph using OSMnx.")
    sub = parser.add_subparsers(dest="mode", required=True)

    bbox = sub.add_parser("bbox")
    bbox.add_argument("--north", type=float, required=True)
    bbox.add_argument("--south", type=float, required=True)
    bbox.add_argument("--east", type=float, required=True)
    bbox.add_argument("--west", type=float, required=True)
    bbox.add_argument("--network-type", default="drive", choices=["drive", "walk", "bike", "all"])

    route = sub.add_parser("route")
    route.add_argument("--start-lat", type=float, required=True)
    route.add_argument("--start-lon", type=float, required=True)
    route.add_argument("--end-lat", type=float, required=True)
    route.add_argument("--end-lon", type=float, required=True)
    route.add_argument("--buffer-km", type=float, default=6.0)
    route.add_argument("--network-type", default="drive", choices=["drive", "walk", "bike", "all"])

    args = parser.parse_args()
    if args.mode == "bbox":
        G = build_osm_graph_for_bbox(args.north, args.south, args.east, args.west, network_type=args.network_type)
    else:
        G = build_osm_graph_for_route(
            args.start_lat,
            args.start_lon,
            args.end_lat,
            args.end_lon,
            buffer_km=args.buffer_km,
            network_type=args.network_type,
        )

    print("OSM graph selesai dibuat.")
    print(f"Cache: {DEFAULT_GRAPHML}")
    print(f"Nodes: {len(G.nodes)}")
    print(f"Edges: {len(G.edges)}")


if __name__ == "__main__":
    main()
