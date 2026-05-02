#!/usr/bin/env python3
"""
Generate cycling research outputs from Strava data.

Outputs:
- data/cycling_research_summary.json
- figures/cycling_methods_panel.svg

This script uses only the Python standard library so it remains robust in
GitHub Actions without extra package installation.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
SUMMARY_PATH = Path("data/cycling_research_summary.json")
FIGURE_PATH = Path("figures/cycling_methods_panel.svg")
RIDE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"}


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, data: dict[str, Any] | None = None) -> Any:
    body = None
    req_headers = headers or {}
    if data is not None:
        body = urlencode(data).encode("utf-8")
        req_headers = {**req_headers, "Content-Type": "application/x-www-form-urlencoded"}
    request = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc}") from exc


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_access_token() -> str:
    token_data = request_json(
        TOKEN_URL,
        method="POST",
        data={
            "client_id": env("STRAVA_CLIENT_ID"),
            "client_secret": env("STRAVA_CLIENT_SECRET"),
            "grant_type": "refresh_token",
            "refresh_token": env("STRAVA_REFRESH_TOKEN"),
        },
    )
    return str(token_data["access_token"])


def fetch_activities(access_token: str, pages: int = 3, per_page: int = 100) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = f"{ACTIVITIES_URL}?per_page={per_page}&page={page}"
        data = request_json(url, headers={"Authorization": f"Bearer {access_token}"})
        if not isinstance(data, list):
            raise RuntimeError("Strava activities response was not a list")
        if not data:
            break
        activities.extend(data)
    return activities


def parse_date(activity: dict[str, Any]) -> datetime | None:
    raw = activity.get("start_date_local") or activity.get("start_date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def clean_rides(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rides = []
    for a in activities:
        if a.get("type") not in RIDE_TYPES:
            continue
        dt = parse_date(a)
        if not dt:
            continue
        distance_km = float(a.get("distance") or 0) / 1000
        moving_h = float(a.get("moving_time") or 0) / 3600
        if distance_km <= 0 or moving_h <= 0:
            continue
        rides.append({
            "date": dt.date().isoformat(),
            "datetime": dt.isoformat(),
            "name": str(a.get("name") or "Untitled ride"),
            "type": str(a.get("type") or "Ride"),
            "is_indoor": bool(a.get("trainer")) or a.get("type") == "VirtualRide",
            "distance_km": round(distance_km, 2),
            "moving_h": round(moving_h, 3),
            "elevation_m": round(float(a.get("total_elevation_gain") or 0), 1),
            "avg_speed_kph": round(distance_km / moving_h, 1),
            "avg_power_w": round(float(a.get("weighted_average_watts") or a.get("average_watts") or 0), 1) or None,
            "avg_cadence_rpm": round(float(a.get("average_cadence") or 0), 1) or None,
        })
    return sorted(rides, key=lambda r: r["datetime"])


def weekly_series(rides: list[dict[str, Any]], weeks: int = 12) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=weeks * 7 - 1)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rides:
        d = datetime.fromisoformat(r["date"]).date()
        if d < start:
            continue
        monday = d - timedelta(days=d.weekday())
        buckets[monday.isoformat()].append(r)

    result = []
    first_monday = start - timedelta(days=start.weekday())
    for i in range(weeks):
        wk = first_monday + timedelta(days=i * 7)
        subset = buckets.get(wk.isoformat(), [])
        distance = sum(r["distance_km"] for r in subset)
        hours = sum(r["moving_h"] for r in subset)
        ascent = sum(r["elevation_m"] for r in subset)
        result.append({
            "week_start": wk.isoformat(),
            "distance_km": round(distance, 1),
            "moving_h": round(hours, 1),
            "elevation_m": round(ascent, 0),
            "ride_count": len(subset),
            "indoor_count": sum(1 for r in subset if r["is_indoor"]),
            "outdoor_count": sum(1 for r in subset if not r["is_indoor"]),
        })
    return result


def recent_window(rides: list[dict[str, Any]], days: int = 30) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return [r for r in rides if datetime.fromisoformat(r["date"]).date() >= cutoff]


def mean_or_none(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return round(statistics.mean(clean), 1)


def build_summary(rides: list[dict[str, Any]]) -> dict[str, Any]:
    recent30 = recent_window(rides, 30)
    recent7 = recent_window(rides, 7)
    weekly = weekly_series(rides, 12)

    total_distance_30 = round(sum(r["distance_km"] for r in recent30), 1)
    total_hours_30 = round(sum(r["moving_h"] for r in recent30), 1)
    total_ascent_30 = round(sum(r["elevation_m"] for r in recent30), 0)

    total_distance_7 = round(sum(r["distance_km"] for r in recent7), 1)
    total_hours_7 = round(sum(r["moving_h"] for r in recent7), 1)
    total_ascent_7 = round(sum(r["elevation_m"] for r in recent7), 0)

    power_cadence = [
        {
            "date": r["date"],
            "name": r["name"],
            "power_w": r["avg_power_w"],
            "cadence_rpm": r["avg_cadence_rpm"],
            "distance_km": r["distance_km"],
            "context": "Indoor" if r["is_indoor"] else "Outdoor",
        }
        for r in recent30
        if r["avg_power_w"] is not None and r["avg_cadence_rpm"] is not None
    ]

    latest = rides[-1] if rides else None
    load_state = "high" if total_hours_7 >= 8 else "moderate" if total_hours_7 >= 4 else "light" if total_hours_7 > 0 else "none"

    return {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "model": "Cycling behavioural regulation micro-dashboard",
        "window_days": {"short": 7, "analysis": 30, "trend_weeks": 12},
        "state": {
            "load_state": load_state,
            "context_balance": {
                "indoor": sum(1 for r in recent30 if r["is_indoor"]),
                "outdoor": sum(1 for r in recent30 if not r["is_indoor"]),
            },
            "seven_day": {
                "distance_km": total_distance_7,
                "moving_h": total_hours_7,
                "elevation_m": total_ascent_7,
                "ride_count": len(recent7),
            },
            "thirty_day": {
                "distance_km": total_distance_30,
                "moving_h": total_hours_30,
                "elevation_m": total_ascent_30,
                "ride_count": len(recent30),
                "mean_power_w": mean_or_none([r["avg_power_w"] for r in recent30]),
                "mean_cadence_rpm": mean_or_none([r["avg_cadence_rpm"] for r in recent30]),
                "mean_speed_kph": mean_or_none([r["avg_speed_kph"] for r in recent30]),
            },
        },
        "weekly_series": weekly,
        "recent_rides": recent30[-30:],
        "power_cadence": power_cadence,
        "latest_ride": latest,
    }


def scale(value: float, domain_min: float, domain_max: float, range_min: float, range_max: float) -> float:
    if domain_max <= domain_min:
        return (range_min + range_max) / 2
    return range_min + (value - domain_min) * (range_max - range_min) / (domain_max - domain_min)


def svg_text(x: float, y: float, text: str, size: int = 13, fill: str = "#6e6c66", weight: int = 400, family: str = "Arial") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{fill}">{escape(text)}</text>'


def escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_svg(summary: dict[str, Any]) -> str:
    weekly = summary["weekly_series"]
    recent = summary["recent_rides"]
    pc = summary["power_cadence"]
    state = summary["state"]

    width, height = 1200, 760
    panel_w, panel_h = 520, 245
    left_x, right_x = 70, 650
    top_y, bottom_y = 110, 430
    purple = "#520671"
    grey = "#6e6c66"
    light = "#e8e5eb"
    mid = "#9a9890"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(70, 48, "Cycling Behavioural Regulation: Methods-Ready Summary", 24, purple, 600),
        svg_text(70, 75, f"Generated from Strava activity data · {summary['generated_utc']} · rolling windows: 7 days / 30 days / 12 weeks", 13, grey),
    ]

    def panel(x: int, y: int, label: str, title: str) -> None:
        parts.append(f'<rect x="{x}" y="{y}" width="{panel_w}" height="{panel_h}" fill="#ffffff" stroke="{light}" stroke-width="1"/>')
        parts.append(svg_text(x + 16, y + 28, label, 15, purple, 600))
        parts.append(svg_text(x + 46, y + 28, title, 15, grey, 600))

    panel(left_x, top_y, "A", "12-week load timeline")
    panel(right_x, top_y, "B", "Power–cadence regulation space")
    panel(left_x, bottom_y, "C", "Indoor/outdoor context balance")
    panel(right_x, bottom_y, "D", "Current behavioural system state")

    # A: weekly load bars
    vals = [w["moving_h"] for w in weekly]
    max_v = max(vals) if vals else 1
    gx, gy, gw, gh = left_x + 40, top_y + 65, 440, 145
    parts.append(f'<line x1="{gx}" y1="{gy+gh}" x2="{gx+gw}" y2="{gy+gh}" stroke="{light}"/>')
    bar_w = gw / max(len(weekly), 1) * 0.58
    for i, w in enumerate(weekly):
        bx = gx + i * gw / len(weekly) + 7
        bh = scale(w["moving_h"], 0, max_v, 0, gh)
        by = gy + gh - bh
        parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{purple}" opacity="0.82"/>')
    parts.append(svg_text(gx, gy + gh + 25, "weeks", 11, mid))
    parts.append(svg_text(gx + 320, gy + gh + 25, f"max {max_v:.1f} h/week", 11, mid))

    # B: power-cadence scatter
    gx, gy, gw, gh = right_x + 55, top_y + 65, 405, 145
    xs = [p["cadence_rpm"] for p in pc]
    ys = [p["power_w"] for p in pc]
    xmin, xmax = (min(xs), max(xs)) if xs else (60, 100)
    ymin, ymax = (min(ys), max(ys)) if ys else (100, 260)
    xmin -= 5; xmax += 5; ymin -= 15; ymax += 15
    parts.append(f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" fill="#ffffff" stroke="{light}"/>')
    for p in pc:
        cx = scale(p["cadence_rpm"], xmin, xmax, gx, gx + gw)
        cy = scale(p["power_w"], ymin, ymax, gy + gh, gy)
        fill = purple if p["context"] == "Indoor" else grey
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{fill}" opacity="0.75"/>')
    parts.append(svg_text(gx, gy + gh + 25, "cadence (rpm)", 11, mid))
    parts.append(svg_text(gx + 300, gy - 10, "power (W)", 11, mid))

    # C: context balance
    ctx = state["context_balance"]
    indoor = ctx["indoor"]
    outdoor = ctx["outdoor"]
    total = max(indoor + outdoor, 1)
    cx, cy = left_x + 260, bottom_y + 140
    radius = 75
    indoor_angle = 360 * indoor / total
    # Simple two-segment approximation using stroke dasharray
    circ = 2 * math.pi * radius
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{light}" stroke-width="26"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{purple}" stroke-width="26" stroke-dasharray="{circ * indoor / total:.1f} {circ:.1f}" transform="rotate(-90 {cx} {cy})"/>')
    parts.append(svg_text(left_x + 70, bottom_y + 105, f"Indoor: {indoor}", 15, purple, 600))
    parts.append(svg_text(left_x + 70, bottom_y + 138, f"Outdoor: {outdoor}", 15, grey, 600))
    parts.append(svg_text(left_x + 70, bottom_y + 171, "30-day observation window", 12, mid))

    # D: key state cards
    d = state["thirty_day"]
    s7 = state["seven_day"]
    cards = [
        ("7-day load", f"{s7['moving_h']:.1f} h · {s7['distance_km']:.1f} km"),
        ("30-day volume", f"{d['moving_h']:.1f} h · {d['distance_km']:.1f} km"),
        ("Mean power", f"{d['mean_power_w']} W" if d['mean_power_w'] is not None else "—"),
        ("Mean cadence", f"{d['mean_cadence_rpm']} rpm" if d['mean_cadence_rpm'] is not None else "—"),
    ]
    start_x, start_y = right_x + 40, bottom_y + 75
    for i, (label, val) in enumerate(cards):
        x = start_x + (i % 2) * 230
        y = start_y + (i // 2) * 80
        parts.append(f'<rect x="{x}" y="{y}" width="200" height="55" fill="#ffffff" stroke="{light}"/>')
        parts.append(svg_text(x + 12, y + 22, label, 11, mid, 500))
        parts.append(svg_text(x + 12, y + 43, val, 18, purple, 600))
    latest = summary.get("latest_ride") or {}
    parts.append(svg_text(right_x + 40, bottom_y + 220, f"Latest: {latest.get('name') or '—'} · {latest.get('date') or '—'}", 12, mid))

    parts.append('</svg>')
    return "\n".join(parts)


def main() -> int:
    token = get_access_token()
    activities = fetch_activities(token)
    rides = clean_rides(activities)
    summary = build_summary(rides)

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    FIGURE_PATH.write_text(generate_svg(summary) + "\n", encoding="utf-8")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {FIGURE_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
