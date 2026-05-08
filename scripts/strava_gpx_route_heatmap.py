#!/usr/bin/env python3
"""Create an interactive route heatmap from local Strava GPX files."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import folium
from folium.plugins import Fullscreen, HeatMap, MeasureControl


def read_gpx_points(path: Path) -> list[list[float]]:
    root = ET.parse(path).getroot()
    points: list[list[float]] = []
    for elem in root.iter():
        if elem.tag.endswith("trkpt"):
            lat = elem.attrib.get("lat")
            lon = elem.attrib.get("lon")
            if lat is not None and lon is not None:
                points.append([float(lat), float(lon)])
    return points


def load_routes(gpx_dir: Path) -> dict[str, list[list[float]]]:
    routes: dict[str, list[list[float]]] = {}
    for path in sorted(gpx_dir.rglob("*.gpx")):
        try:
            points = read_gpx_points(path)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue
        if len(points) >= 2:
            routes[path.name] = points
    return routes


def make_heatmap(routes: dict[str, list[list[float]]], output_html: Path) -> None:
    all_points = [point for route in routes.values() for point in route]
    if not all_points:
        raise RuntimeError("No GPX route points found.")

    center = [
        sum(point[0] for point in all_points) / len(all_points),
        sum(point[1] for point in all_points) / len(all_points),
    ]

    route_map = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")
    Fullscreen().add_to(route_map)
    MeasureControl().add_to(route_map)

    HeatMap(
        all_points,
        name="Route density heatmap",
        radius=9,
        blur=14,
        min_opacity=0.25,
    ).add_to(route_map)

    traces = folium.FeatureGroup(name="Route traces", show=False)
    for name, points in routes.items():
        folium.PolyLine(points, weight=1, opacity=0.25, tooltip=name).add_to(traces)
    traces.add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
    route_map.save(output_html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Strava route heatmap from local GPX files.")
    parser.add_argument("gpx_dir", type=Path, help="Folder containing Strava GPX files")
    parser.add_argument("--out", type=Path, default=Path("strava_route_heatmap.html"))
    args = parser.parse_args()

    routes = load_routes(args.gpx_dir)
    print(f"Loaded {len(routes)} GPX routes from {args.gpx_dir}")
    make_heatmap(routes, args.out)
    print(f"Saved heatmap: {args.out.resolve()}")


if __name__ == "__main__":
    main()
