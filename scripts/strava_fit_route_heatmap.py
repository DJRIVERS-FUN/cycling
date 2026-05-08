#!/usr/bin/env python3
"""Create a branded interactive Strava route heatmap from local FIT files.

Default output is filtered to Hokkaido and thinned so the generated HTML remains
small enough for GitHub Pages.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import folium
from fitparse import FitFile
from folium.plugins import Fullscreen, HeatMap, MeasureControl

SEMICIRCLES_TO_DEGREES = 180 / 2**31

REGION_BOUNDS = {
    "hokkaido": (41.2, 45.8, 139.2, 146.2),
    "hakodate": (41.65, 42.25, 140.45, 141.25),
    "all": (-90.0, 90.0, -180.0, 180.0),
}

REGION_LABELS = {
    "hokkaido": "Hokkaido",
    "hakodate": "Hakodate",
    "all": "All recorded locations",
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


def filter_route_to_region(points: list[list[float]], bounds: tuple[float, float, float, float]) -> list[list[float]]:
    return [point for point in points if inside_bounds(point, bounds)]


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
            points = filter_route_to_region(read_fit_points(path, every=every), bounds)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue

        if len(points) >= 2:
            routes[path.name] = points
            print(f"Loaded {index}/{len(paths)}: {path.name} ({len(points)} region points)")
        else:
            print(f"Skipping {index}/{len(paths)}: {path.name} (outside selected region or no GPS track)")

    return routes


def thin_points(points: list[list[float]], max_points: int) -> list[list[float]]:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    return points[::step][:max_points]


def build_map_html(
    routes: dict[str, list[list[float]]],
    max_heat_points: int,
    draw_traces: bool,
) -> tuple[str, dict[str, int]]:
    all_points = [point for route in routes.values() for point in route]
    if not all_points:
        raise RuntimeError("No GPS points found in the selected region.")

    heat_points = thin_points(all_points, max_heat_points)
    center = [
        sum(point[0] for point in heat_points) / len(heat_points),
        sum(point[1] for point in heat_points) / len(heat_points),
    ]

    route_map = folium.Map(
        location=center,
        zoom_start=9,
        tiles="CartoDB positron",
        control_scale=True,
        prefer_canvas=True,
    )
    Fullscreen(position="topright").add_to(route_map)
    MeasureControl(position="topright").add_to(route_map)

    HeatMap(
        heat_points,
        name="Route density",
        radius=8,
        blur=13,
        min_opacity=0.22,
        max_zoom=13,
    ).add_to(route_map)

    if draw_traces:
        traces = folium.FeatureGroup(name="Route traces", show=False)
        for name, points in routes.items():
            if len(points) >= 2:
                folium.PolyLine(points, weight=1, opacity=0.16, tooltip=name).add_to(traces)
        traces.add_to(route_map)

    folium.LayerControl(collapsed=True, position="topright").add_to(route_map)

    stats = {
        "routes": len(routes),
        "raw_points": len(all_points),
        "heat_points": len(heat_points),
    }
    return route_map.get_root().render(), stats


def branded_page(map_html: str, stats: dict[str, int], region: str, every: int, max_heat_points: int) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    label = REGION_LABELS.get(region, region.title())

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} Route Density | Rivers Lab</title>
<style>
  :root {{
    --rivers-purple:#520671;
    --rivers-muted:#6e6c66;
    --rivers-soft:#f7f3fa;
    --rivers-line:#e6e2de;
    --rivers-paper:#ffffff;
    --rivers-ink:#262323;
  }}
  html, body {{
    margin:0;
    padding:0;
    background:var(--rivers-paper);
    color:var(--rivers-ink);
    font-family:Arial, Helvetica, sans-serif;
  }}
  .rivers-wrap {{
    max-width:1180px;
    margin:0 auto;
    padding:18px 14px 20px;
  }}
  .rivers-kicker {{
    font-family:'DM Mono','SFMono-Regular',Consolas,monospace;
    font-size:12px;
    letter-spacing:.08em;
    color:var(--rivers-purple);
    text-transform:uppercase;
    margin-bottom:6px;
  }}
  .rivers-title {{
    font-family:'DM Mono','SFMono-Regular',Consolas,monospace;
    font-size:24px;
    line-height:1.15;
    color:var(--rivers-purple);
    margin:0 0 6px;
    font-weight:700;
  }}
  .rivers-subtitle {{
    font-family:'DM Mono','SFMono-Regular',Consolas,monospace;
    font-size:15px;
    color:#9a9890;
    margin:0 0 14px;
  }}
  .rivers-grid {{
    display:grid;
    grid-template-columns:1.1fr .9fr;
    gap:10px;
    margin-bottom:12px;
  }}
  .rivers-card {{
    border:1px solid var(--rivers-line);
    border-radius:8px;
    padding:12px 14px;
    background:#fff;
  }}
  .rivers-card p {{
    margin:0;
    color:var(--rivers-muted);
    font-size:14px;
    line-height:1.45;
  }}
  .notation {{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:8px;
  }}
  .note-step {{
    border:1px solid var(--rivers-line);
    border-radius:7px;
    padding:9px 10px;
    background:var(--rivers-soft);
    min-height:54px;
  }}
  .note-label {{
    font-family:'DM Mono','SFMono-Regular',Consolas,monospace;
    font-size:12px;
    color:var(--rivers-purple);
    margin-bottom:4px;
    font-weight:700;
  }}
  .note-text {{
    font-size:12.5px;
    color:var(--rivers-muted);
    line-height:1.28;
  }}
  .stats {{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:8px;
  }}
  .stat {{
    border-left:3px solid var(--rivers-purple);
    padding:3px 0 3px 9px;
  }}
  .stat-number {{
    font-family:'DM Mono','SFMono-Regular',Consolas,monospace;
    color:var(--rivers-purple);
    font-size:18px;
    font-weight:700;
  }}
  .stat-label {{
    font-size:11px;
    color:var(--rivers-muted);
  }}
  .map-frame {{
    border:1px solid var(--rivers-line);
    border-radius:10px;
    overflow:hidden;
    background:#fff;
    box-shadow:0 10px 30px rgba(82,6,113,.06);
  }}
  .map-frame iframe {{
    width:100%;
    height:720px;
    border:0;
    display:block;
  }}
  .footer-note {{
    font-size:11px;
    color:#9a9890;
    margin-top:8px;
    line-height:1.35;
  }}
  @media (max-width:800px) {{
    .rivers-grid, .notation, .stats {{ grid-template-columns:1fr; }}
    .map-frame iframe {{ height:620px; }}
  }}
</style>
</head>
<body>
<div class="rivers-wrap">
  <div class="rivers-kicker">Rivers Lab · Human–Machine Cycling Systems</div>
  <h1 class="rivers-title">{label} Route Density</h1>
  <div class="rivers-subtitle">Constraint → Behaviour → Adaptation → Outcome</div>

  <div class="rivers-grid">
    <div class="rivers-card">
      <p>This visualization treats repeated cycling routes as spatial traces of behavioural regulation within a constrained human–machine system. Darker density fields indicate recurrent corridors where terrain, infrastructure, equipment, effort, and route choice interact over time.</p>
    </div>
    <div class="rivers-card stats">
      <div class="stat"><div class="stat-number">{stats['routes']:,}</div><div class="stat-label">FIT activities in region</div></div>
      <div class="stat"><div class="stat-number">{stats['heat_points']:,}</div><div class="stat-label">mapped GPS points</div></div>
      <div class="stat"><div class="stat-number">1/{every}</div><div class="stat-label">stream sampling rate</div></div>
    </div>
  </div>

  <div class="notation">
    <div class="note-step"><div class="note-label">Constraint</div><div class="note-text">Terrain, weather, roads, equipment, traffic, gradient.</div></div>
    <div class="note-step"><div class="note-label">Behaviour</div><div class="note-text">Route selection, pacing, cadence, effort distribution.</div></div>
    <div class="note-step"><div class="note-label">Adaptation</div><div class="note-text">Repeated corridor use and local adjustment over time.</div></div>
    <div class="note-step"><div class="note-label">Outcome</div><div class="note-text">Spatial density as accumulated behavioural telemetry.</div></div>
  </div>

  <div style="height:12px"></div>
  <div class="map-frame">
    <iframe srcdoc='{map_html.replace("&", "&amp;").replace("'", "&#39;").replace("<", "&lt;").replace(">", "&gt;")}'></iframe>
  </div>

  <div class="footer-note">Generated {generated}. Source data: local Strava archive FIT files filtered to {label}; GPS streams thinned for web delivery. Max embedded heatmap points: {max_heat_points:,}.</div>
</div>
</body>
</html>
"""


def make_heatmap(
    routes: dict[str, list[list[float]]],
    output_html: Path,
    region: str,
    every: int,
    max_heat_points: int = 120_000,
    draw_traces: bool = False,
) -> None:
    map_html, stats = build_map_html(routes, max_heat_points=max_heat_points, draw_traces=draw_traces)
    output_html.write_text(branded_page(map_html, stats, region, every, max_heat_points), encoding="utf-8")
    print(f"Heatmap points written: {stats['heat_points']:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a branded Hokkaido-filtered Strava route heatmap from local FIT files.")
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
    make_heatmap(
        routes,
        args.out,
        region=args.region,
        every=max(1, args.every),
        max_heat_points=args.max_heat_points,
        draw_traces=args.traces,
    )
    print(f"Saved heatmap: {args.out.resolve()}")


if __name__ == "__main__":
    main()
