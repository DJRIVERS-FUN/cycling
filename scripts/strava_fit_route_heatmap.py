#!/usr/bin/env python3
"""Create an interactive Strava route heatmap from local FIT files.

Default output is filtered to Hokkaido and thinned so the generated HTML remains
small enough for GitHub Pages.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import folium
from fitparse import FitFile
from folium.plugins import Fullscreen, HeatMap, MeasureControl

SEMICIRCLES_TO_DEGREES = 180 / 2**31

# Broad Hokkaido bounding box, including nearby riding areas around Hakodate.
# Format: min_lat, max_lat, min_lon, max_lon
REGION_BOUNDS = {
    "hokkaido": (41.2, 45.8, 139.2, 146.2),
    "hakodate": (41.65, 42.25, 140.45, 141.25),
    "all": (-90.0, 90.0, -180.0, 180.0),
}


def inside_bounds(point: list[float], bounds: tuple[float, float, float, float]) -> bool:
    lat, lon = point
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


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


def filter_route_to_region(
    points: list[list[float]],
    bounds: tuple[float, float, float, float],
    require_region_hit: bool = True,
) -> list[list[float]]:
    region_points = [point for point in points if inside_bounds(point, bounds)]
    if require_region_hit and not region_points:
        return []
    return region_points


def load_routes(
    fit_dir: Path,
    limit: int | None = None,
    every: int = 10,
    bounds: tuple[float, float, float, float] = REGION_BOUNDS["hokkaido"],
) -> dict[str, list[list[float]]]:
    routes: dict[str, list[list[float]]] = {}
    paths = sorted(fit_dir.rglob("*.fit"))
    if limit is not None:
        paths = paths[:limit]

    for index, path in enumerate(paths, start=1):
        try:
            points = read_fit_points(path, every=every)
            points = filter_route_to_region(points, bounds)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue

        if len(points) >= 2:
            routes[path.name] = points
            print(f"Loaded {index}/{len(paths)}: {path.name} ({len(points)} Hokkaido points)")
        else:
            print(f"Skipping {index}/{len(paths)}: {path.name} (outside selected region or no GPS track)")

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
        raise RuntimeError("No GPS points found in the selected region.")

    heat_points = thin_points(all_points, max_heat_points)

    center = [
        sum(point[0] for point in heat_points) / len(heat_points),
        sum(point[1] for point in heat_points) / len(heat_points),
    ]

    route_map = folium.Map(location=center, zoom_start=9, tiles="CartoDB positron")
    Fullscreen().add_to(route_map)
    MeasureControl().add_to(route_map)

    HeatMap(
        heat_points,
        name="Hokkaido route density heatmap",
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
    parser = argparse.ArgumentParser(description="Create a Hokkaido-filtered Strava route heatmap from local FIT files.")
    parser.add_argument("fit_dir", type=Path, help="Folder containing .fit files")
    parser.add_argument("--out", type=Path, default=Path("docs/strava_hokkaido_route_heatmap.html"))
    parser.add_argument("--limit", type=int, default=None, help="Optional test limit, e.g. --limit 20")
    parser.add_argument("--every", type=int, default=20, help="Keep one GPS point every N records")
    parser.add_argument("--max-heat-points", type=int, default=120000, help="Maximum points embedded in HTML")
    parser.add_argument("--region", choices=sorted(REGION_BOUNDS), default="hokkaido", help="Spatial filter region")
    parser.add_argument("--traces", action="store_true", help="Also draw individual route lines; creates a much larger HTML file")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    bounds = REGION_BOUNDS[args.region]
    routes = load_routes(args.fit_dir, args.limit, every=max(1, args.every), bounds=bounds)
    print(f"Loaded {len(routes)} GPS routes from {args.fit_dir} in region: {args.region}")
    make_heatmap(routes, args.out, max_heat_points=args.max_heat_points, draw_traces=args.traces)
    print(f"Saved heatmap: {args.out.resolve()}")


if __name__ == "__main__":
    main()
