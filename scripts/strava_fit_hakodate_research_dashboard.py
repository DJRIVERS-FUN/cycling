#!/usr/bin/env python3
"""Create a branded Hakodate cycling telemetry research dashboard from FIT files.

Layers included:
- temporal route density by year
- cadence-weighted spatial density
- power-weighted spatial density
- grid-based behavioural corridor extraction
- Hakodate micro-region density modelling

The script intentionally uses simple grid/cell aggregation rather than heavy GIS
or machine-learning dependencies so it can run locally with fitparse + folium.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import folium
from fitparse import FitFile
from folium.plugins import Fullscreen, HeatMap, MeasureControl

SEMICIRCLES_TO_DEGREES = 180 / 2**31
HAKODATE_BOUNDS = (41.65, 42.25, 140.45, 141.25)  # min_lat, max_lat, min_lon, max_lon
PURPLE = "#520671"
MUTED = "#6e6c66"
SOFT = "#f7f3fa"
LINE = "#e6e2de"


def in_bounds(lat: float, lon: float, bounds: tuple[float, float, float, float]) -> bool:
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def norm(value: float | None, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    if value <= lo:
        return 0.05
    if value >= hi:
        return 1.0
    return 0.05 + 0.95 * ((value - lo) / (hi - lo))


def grid_key(lat: float, lon: float, cell: float) -> tuple[float, float]:
    return (round(math.floor(lat / cell) * cell, 5), round(math.floor(lon / cell) * cell, 5))


def cell_center(key: tuple[float, float], cell: float) -> list[float]:
    return [key[0] + cell / 2, key[1] + cell / 2]


def read_fit_records(path: Path, every: int, bounds: tuple[float, float, float, float]) -> list[dict]:
    records: list[dict] = []
    fitfile = FitFile(str(path))
    gps_index = 0

    for record in fitfile.get_messages("record"):
        values = {field.name: field.value for field in record}
        lat_raw = values.get("position_lat")
        lon_raw = values.get("position_long")
        if lat_raw is None or lon_raw is None:
            continue

        gps_index += 1
        if gps_index % every != 0:
            continue

        lat = lat_raw * SEMICIRCLES_TO_DEGREES
        lon = lon_raw * SEMICIRCLES_TO_DEGREES
        if not in_bounds(lat, lon, bounds):
            continue

        ts = values.get("timestamp")
        if isinstance(ts, datetime):
            year = ts.year
        else:
            year = 0

        altitude = values.get("enhanced_altitude", values.get("altitude"))

        records.append(
            {
                "lat": lat,
                "lon": lon,
                "year": year,
                "cadence": values.get("cadence"),
                "power": values.get("power"),
                "altitude": altitude,
                "activity": path.stem,
            }
        )

    return records


def load_records(fit_dir: Path, every: int, limit: int | None) -> list[dict]:
    paths = sorted(fit_dir.rglob("*.fit"))
    if limit:
        paths = paths[:limit]

    all_records: list[dict] = []
    activity_count = 0

    for i, path in enumerate(paths, start=1):
        try:
            records = read_fit_records(path, every=every, bounds=HAKODATE_BOUNDS)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}")
            continue

        if records:
            activity_count += 1
            all_records.extend(records)
            print(f"Loaded {i}/{len(paths)}: {path.name} ({len(records)} Hakodate records)")
        else:
            print(f"Skipping {i}/{len(paths)}: {path.name} (outside Hakodate or no GPS)")

    print(f"Activities with Hakodate GPS: {activity_count}")
    return all_records


def aggregate_cells(records: list[dict], cell: float) -> dict[tuple[float, float], dict]:
    cells: dict[tuple[float, float], dict] = defaultdict(lambda: {"count": 0, "cadence": [], "power": [], "years": set()})
    for r in records:
        key = grid_key(r["lat"], r["lon"], cell)
        cells[key]["count"] += 1
        if r["cadence"] is not None:
            cells[key]["cadence"].append(r["cadence"])
        if r["power"] is not None:
            cells[key]["power"].append(r["power"])
        if r["year"]:
            cells[key]["years"].add(r["year"])
    return cells


def add_micro_region_layer(m: folium.Map, cells: dict, cell: float, top_n: int) -> None:
    layer = folium.FeatureGroup(name="Hakodate concentrated actions", show=False)
    ranked = sorted(cells.items(), key=lambda item: item[1]["count"], reverse=True)[:top_n]
    max_count = max((item[1]["count"] for item in ranked), default=1)

    for key, info in ranked:
        center = cell_center(key, cell)
        cadence = mean(info["cadence"]) if info["cadence"] else None
        power = mean(info["power"] ) if info["power"] else None
        persistence = len(info["years"])
        radius = 4 + 18 * (info["count"] / max_count)
        popup = (
            f"<b>Concentrated action region</b><br>"
            f"Records: {info['count']:,}<br>"
            f"Years active: {persistence}<br>"
            f"Mean cadence: {cadence:.1f} rpm<br>" if cadence is not None else f"<b>Concentrated action region</b><br>Records: {info['count']:,}<br>Years active: {persistence}<br>Mean cadence: —<br>"
        )
        popup += f"Mean power: {power:.1f} W" if power is not None else "Mean power: —"
        folium.CircleMarker(center, radius=radius, color=PURPLE, fill=True, fill_opacity=0.20, weight=1, popup=popup).add_to(layer)
    layer.add_to(m)


def add_behavioural_corridor_layer(m: folium.Map, cells: dict, cell: float, top_n: int) -> None:
    layer = folium.FeatureGroup(name="Predictive behavioural routes", show=False)
    ranked = sorted(cells.items(), key=lambda item: (item[1]["count"] * max(1, len(item[1]["years"]))), reverse=True)[:top_n]

    for rank, (key, info) in enumerate(ranked, start=1):
        center = cell_center(key, cell)
        cadence = mean(info["cadence"]) if info["cadence"] else None
        power = mean(info["power"] ) if info["power"] else None
        folium.CircleMarker(
            center,
            radius=7,
            color=PURPLE,
            fill=True,
            fill_opacity=0.75,
            weight=1,
            tooltip=f"Route {rank}: {info['count']:,} records",
            popup=(
                f"<b>Predictive behavioural route #{rank}</b><br>"
                f"Density records: {info['count']:,}<br>"
                f"Persistence across years: {len(info['years'])}<br>"
                f"Mean cadence: {cadence:.1f} rpm<br>" if cadence is not None else f"<b>Predictive behavioural route #{rank}</b><br>Density records: {info['count']:,}<br>Persistence across years: {len(info['years'])}<br>Mean cadence: —<br>"
            ) + (f"Mean power: {power:.1f} W" if power is not None else "Mean power: —"),
        ).add_to(layer)
    layer.add_to(m)


def build_map(records: list[dict], cell: float, max_points: int, top_cells: int) -> tuple[str, dict]:
    if not records:
        raise RuntimeError("No Hakodate records found.")

    points = [[r["lat"], r["lon"]] for r in records]
    step = max(1, len(points) // max_points)
    points = points[::step][:max_points]

    center = [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]
    m = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron", control_scale=True, prefer_canvas=True)
    Fullscreen(position="topright").add_to(m)
    MeasureControl(position="topright").add_to(m)

    HeatMap(points, name="Route density", radius=9, blur=13, min_opacity=0.24).add_to(m)

    cadence_points = [[r["lat"], r["lon"], norm(r["cadence"], 55, 105)] for r in records if r.get("cadence") is not None]
    if cadence_points:
        cadence_points = cadence_points[::step][:max_points]
        HeatMap(cadence_points, name="Cadence-weighted density", radius=9, blur=13, min_opacity=0.20, show=False).add_to(m)

    power_points = [[r["lat"], r["lon"], norm(r["power"], 80, 360)] for r in records if r.get("power") is not None]
    if power_points:
        power_points = power_points[::step][:max_points]
        HeatMap(power_points, name="Power-weighted density", radius=9, blur=13, min_opacity=0.20, show=False).add_to(m)

    by_year: dict[int, list[list[float]]] = defaultdict(list)
    for r in records:
        if r["year"]:
            by_year[r["year"]].append([r["lat"], r["lon"]])
    for year in sorted(by_year):
        year_points = by_year[year][::max(1, len(by_year[year]) // 25000)]
        HeatMap(year_points, name=f"Rivers Lab actions: {year}", radius=8, blur=12, min_opacity=0.18, show=False).add_to(m)

    cells = aggregate_cells(records, cell)
    add_behavioural_corridor_layer(m, cells, cell, top_cells)
    add_micro_region_layer(m, cells, cell, top_cells * 2)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)

    stats = {
        "records": len(records),
        "mapped_points": len(points),
        "years": len(by_year),
        "cells": len(cells),
        "corridors": min(top_cells, len(cells)),
        "activities": len({r["activity"] for r in records}),
    }
    return m.get_root().render(), stats


def escape_srcdoc(html: str) -> str:
    return html.replace("&", "&amp;").replace("'", "&#39;").replace("<", "&lt;").replace(">", "&gt;")


def branded_page(map_html: str, stats: dict, every: int, cell: float) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Hakodate Cycling Telemetry Dashboard | Rivers Lab</title><style>:root {{--purple:#520671;--muted:#6e6c66;--soft:#f7f3fa;--line:#e6e2de;--ink:#262323;}}html,body{{margin:0;background:#fff;color:var(--ink);font-family:Arial,Helvetica,sans-serif;}}.wrap{{max-width:1180px;margin:0 auto;padding:18px 14px 20px;}}.map{{border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:0 10px 30px rgba(82,6,113,.06);}}.map iframe{{width:100%;height:760px;border:0;display:block;}}.foot{{font-size:11px;color:#9a9890;margin-top:8px;line-height:1.35;}}</style></head><body><div class="wrap"><div class="map"><iframe srcdoc='{escape_srcdoc(map_html)}'></iframe></div><div class="foot">Generated {generated}. Source: local FIT telemetry filtered to Hakodate. Cell size: {cell} degrees. Layers are thinned for browser delivery.</div></div></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Hakodate cycling telemetry research dashboard from FIT files.")
    parser.add_argument("fit_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("docs/hakodate_cycling_telemetry_dashboard.html"))
    parser.add_argument("--every", type=int, default=15)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-points", type=int, default=90000)
    parser.add_argument("--cell", type=float, default=0.003)
    parser.add_argument("--top-cells", type=int, default=30)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = load_records(args.fit_dir, every=max(1, args.every), limit=args.limit)
    map_html, stats = build_map(records, cell=args.cell, max_points=args.max_points, top_cells=args.top_cells)
    args.out.write_text(branded_page(map_html, stats, every=max(1, args.every), cell=args.cell), encoding="utf-8")
    print(f"Saved dashboard: {args.out.resolve()}")


if __name__ == "__main__":
    main()
