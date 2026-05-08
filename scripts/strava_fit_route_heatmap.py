#!/usr/bin/env python3
"""Create an interactive Strava route heatmap from local FIT files."""

from __future__ import annotations

import argparse
from pathlib import Path

import folium
from fitparse import FitFile
from folium.plugins import Fullscreen, HeatMap, MeasureControl

SEMICIRCLES_TO_DEGREES = 180 / 2**31


def read_fit_points(path: Path) -> list[list[float]]:
    points: list[list[float]] = []
    fitfile = FitFile(str(path))

    for record in fitfile.get_messages("record"):
        values = {field.name: field.value for field in record}
        lat = values.get("position_lat")
        lon = values.get("position_long")
        if lat is None or lon is None:
            continue
        points.append([lat * SEMICIRCLES_TO_DEGREES, lon * SEMICIRCLES_TO_DEGREES])

    return points


def load_routes(fit_dir: Path, limit: int | None = None) -> dict[str, list[list[float]]]:
    routes: dict[str, list[list[float]]] = {}
    paths = sorted(fit_dir.rglob("*.fit"))
    if limit is not None:
        paths = paths[:limit]

    for index, path in enumerate(paths, start=1):
        try:
            points = read_fit_points(path)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue

        if len(points) >= 2:
            routes[path.name] = points
            print(f"Loaded {index}/{len(paths)}: {path.name} ({len(points)} points)")
        else:
            print(f"Skipping {index}/{len(paths)}: {path.name} (no GPS track)")

    return routes


def make_heatmap(routes: dict[str, list[list[float]]], output_html: Path) -> None:
    all_points = [point for route in routes.values() for point in route]
    if not all_points:
        raise RuntimeError("No GPS points found in the FIT files.")

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
        folium.PolyLine(points, weight=1, opacity=0.22, tooltip=name).add_to(traces)
    traces.add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
    route_map.save(output_html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Strava route heatmap from local FIT files.")
    parser.add_argument("fit_dir", type=Path, help="Folder containing .fit files")
    parser.add_argument("--out", type=Path, default=Path("strava_fit_route_heatmap.html"))
    parser.add_argument("--limit", type=int, default=None, help="Optional test limit, e.g. --limit 20")
    args = parser.parse_args()

    routes = load_routes(args.fit_dir, args.limit)
    print(f"Loaded {len(routes)} GPS routes from {args.fit_dir}")
    make_heatmap(routes, args.out)
    print(f"Saved heatmap: {args.out.resolve()}")


if __name__ == "__main__":
    main()
