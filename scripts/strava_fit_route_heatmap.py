#!/usr/bin/env python3
"""Create an interactive Strava route heatmap from local FIT files.

This version deliberately thins the GPS stream so the generated HTML remains
small enough for GitHub Pages.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import folium
from fitparse import FitFile
from folium.plugins import Fullscreen, HeatMap, MeasureControl

SEMICIRCLES_TO_DEGREES = 180 / 2**31


def read_fit_points(path: Path, every: int = 10) -> list[list[float]]:
    points: list[list[float]] = []
    fitfile = FitFile(str(path))

    gps_index = 0
    for record in fitfile.get_messages("record"):
        values = {field.name: field.value for field in record}
        lat = values.get("position_lat")
        lon = values.get("position_long")
        if lat is None or lon is None:
            continue

        gps_index += 1
        if gps_index % every != 0:
            continue

        points.append([lat * SEMICIRCLES_TO_DEGREES, lon * SEMICIRCLES_TO_DEGREES])

    return points


def load_routes(fit_dir: Path, limit: int | None = None, every: int = 10) -> dict[str, list[list[float]]]:
    routes: dict[str, list[list[float]]] = {}
    paths = sorted(fit_dir.rglob("*.fit"))
    if limit is not None:
        paths = paths[:limit]

    for index, path in enumerate(paths, start=1):
        try:
            points = read_fit_points(path, every=every)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue

        if len(points) >= 2:
            routes[path.name] = points
            print(f"Loaded {index}/{len(paths)}: {path.name} ({len(points)} thinned points)")
        else:
            print(f"Skipping {index}/{len(paths)}: {path.name} (no GPS track)")

    return routes


def thin_points(points: list[list[float]], max_points: int) -> list[list[float]]:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    return points[::step][:max_points]


def make_heatmap(
    routes: dict[str, list[list[float]]],
    output_html: Path,
    max_heat_points: int = 120_000,
    draw_traces: bool = False,
) -> None:
    all_points = [point for route in routes.values() for point in route]
    if not all_points:
        raise RuntimeError("No GPS points found in the FIT files.")

    heat_points = thin_points(all_points, max_heat_points)

    center = [
        sum(point[0] for point in heat_points) / len(heat_points),
        sum(point[1] for point in heat_points) / len(heat_points),
    ]

    route_map = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")
    Fullscreen().add_to(route_map)
    MeasureControl().add_to(route_map)

    HeatMap(
        heat_points,
        name="Route density heatmap",
        radius=8,
        blur=13,
        min_opacity=0.25,
    ).add_to(route_map)

    if draw_traces:
        traces = folium.FeatureGroup(name="Route traces", show=False)
        for name, points in routes.items():
            if len(points) >= 2:
                folium.PolyLine(points, weight=1, opacity=0.20, tooltip=name).add_to(traces)
        traces.add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
    route_map.save(output_html)
    print(f"Heatmap points written: {len(heat_points):,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Strava route heatmap from local FIT files.")
    parser.add_argument("fit_dir", type=Path, help="Folder containing .fit files")
    parser.add_argument("--out", type=Path, default=Path("docs/strava_fit_route_heatmap.html"))
    parser.add_argument("--limit", type=int, default=None, help="Optional test limit, e.g. --limit 20")
    parser.add_argument("--every", type=int, default=20, help="Keep one GPS point every N records")
    parser.add_argument("--max-heat-points", type=int, default=120000, help="Maximum points embedded in HTML")
    parser.add_argument("--traces", action="store_true", help="Also draw individual route lines; creates a much larger HTML file")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    routes = load_routes(args.fit_dir, args.limit, every=max(1, args.every))
    print(f"Loaded {len(routes)} GPS routes from {args.fit_dir}")
    make_heatmap(routes, args.out, max_heat_points=args.max_heat_points, draw_traces=args.traces)
    print(f"Saved heatmap: {args.out.resolve()}")


if __name__ == "__main__":
    main()
