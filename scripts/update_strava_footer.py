#!/usr/bin/env python3
"""
Generate research-grade Strava footer data for the Rivers Lab website.

Required GitHub Actions secrets:
- STRAVA_CLIENT_ID
- STRAVA_CLIENT_SECRET
- STRAVA_REFRESH_TOKEN

Output:
- data/strava_footer.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
OUTPUT_PATH = Path("data/strava_footer.json")
RIDE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"}


@dataclass
class RideSummary:
    count: int
    seven_day_count: int
    seven_day_distance_km: float
    seven_day_moving_hours: float
    seven_day_elevation_m: float
    total_distance_km: float
    total_moving_hours: float
    total_elevation_m: float
    indoor_count: int
    outdoor_count: int
    avg_speed_kph: float | None
    avg_power_w: int | None
    avg_cadence_rpm: int | None
    climbing_m_per_km: float | None
    latest_name: str | None
    latest_date: str | None
    latest_type: str | None


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
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


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_access_token() -> str:
    payload = {
        "client_id": get_required_env("STRAVA_CLIENT_ID"),
        "client_secret": get_required_env("STRAVA_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": get_required_env("STRAVA_REFRESH_TOKEN"),
    }
    token_data = request_json(TOKEN_URL, method="POST", data=payload)
    if not isinstance(token_data, dict) or "access_token" not in token_data:
        raise RuntimeError("Strava token response did not include access_token")
    return str(token_data["access_token"])


def fetch_activities(access_token: str, per_page: int = 60) -> list[dict[str, Any]]:
    url = f"{ACTIVITIES_URL}?per_page={per_page}&page=1"
    data = request_json(url, headers={"Authorization": f"Bearer {access_token}"})
    if not isinstance(data, list):
        raise RuntimeError("Strava activities response was not a list")
    return data


def parse_local_date(activity: dict[str, Any]) -> datetime | None:
    raw = activity.get("start_date_local") or activity.get("start_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def weighted_mean(values: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in values if weight > 0)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values if weight > 0) / total_weight


def summarize_rides(activities: list[dict[str, Any]]) -> RideSummary:
    rides = [a for a in activities if a.get("type") in RIDE_TYPES]
    now_local = datetime.now(timezone.utc)
    cutoff = now_local - timedelta(days=7)

    def is_recent(activity: dict[str, Any]) -> bool:
        dt = parse_local_date(activity)
        if not dt:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff

    recent_rides = [a for a in rides if is_recent(a)]

    def totals(subset: list[dict[str, Any]]) -> tuple[float, float, float]:
        distance_km = sum(float(a.get("distance") or 0) for a in subset) / 1000
        moving_hours = sum(float(a.get("moving_time") or 0) for a in subset) / 3600
        elevation_m = sum(float(a.get("total_elevation_gain") or 0) for a in subset)
        return round(distance_km, 1), round(moving_hours, 1), round(elevation_m, 0)

    total_distance_km, total_moving_hours, total_elevation_m = totals(rides)
    seven_distance_km, seven_moving_hours, seven_elevation_m = totals(recent_rides)

    indoor_count = sum(1 for a in recent_rides if a.get("type") == "VirtualRide" or a.get("trainer") is True)
    outdoor_count = max(len(recent_rides) - indoor_count, 0)

    avg_speed_kph = None
    if seven_moving_hours > 0:
        avg_speed_kph = round(seven_distance_km / seven_moving_hours, 1)

    power_values: list[tuple[float, float]] = []
    cadence_values: list[tuple[float, float]] = []
    for a in recent_rides:
        moving_time = float(a.get("moving_time") or 0)
        power = a.get("weighted_average_watts") or a.get("average_watts")
        cadence = a.get("average_cadence")
        if power is not None:
            power_values.append((float(power), moving_time))
        if cadence is not None:
            cadence_values.append((float(cadence), moving_time))

    power_mean = weighted_mean(power_values)
    cadence_mean = weighted_mean(cadence_values)

    climbing_m_per_km = None
    if seven_distance_km > 0:
        climbing_m_per_km = round(seven_elevation_m / seven_distance_km, 1)

    latest = rides[0] if rides else None
    latest_date = None
    if latest and latest.get("start_date_local"):
        latest_date = str(latest["start_date_local"]).split("T")[0]

    return RideSummary(
        count=len(rides),
        seven_day_count=len(recent_rides),
        seven_day_distance_km=seven_distance_km,
        seven_day_moving_hours=seven_moving_hours,
        seven_day_elevation_m=seven_elevation_m,
        total_distance_km=total_distance_km,
        total_moving_hours=total_moving_hours,
        total_elevation_m=total_elevation_m,
        indoor_count=indoor_count,
        outdoor_count=outdoor_count,
        avg_speed_kph=avg_speed_kph,
        avg_power_w=round(power_mean) if power_mean is not None else None,
        avg_cadence_rpm=round(cadence_mean) if cadence_mean is not None else None,
        climbing_m_per_km=climbing_m_per_km,
        latest_name=str(latest.get("name")) if latest else None,
        latest_date=latest_date,
        latest_type=str(latest.get("type")) if latest else None,
    )


def classify_load(hours: float) -> str:
    if hours >= 8:
        return "High load"
    if hours >= 4:
        return "Moderate load"
    if hours > 0:
        return "Light load"
    return "No recent load"


def classify_context(indoor_count: int, outdoor_count: int) -> str:
    if indoor_count and outdoor_count:
        return "Mixed indoor/outdoor"
    if indoor_count:
        return "Indoor-controlled"
    if outdoor_count:
        return "Outdoor-field"
    return "No recent rides"


def build_footer_payload(summary: RideSummary) -> dict[str, Any]:
    updated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if summary.seven_day_count == 0:
        headline = "No rides in the last 7 days"
    else:
        headline = (
            f"7-day riding state: {summary.seven_day_distance_km:.1f} km · "
            f"{summary.seven_day_moving_hours:.1f} h · {int(summary.seven_day_elevation_m):,} m+"
        )

    metrics = [
        {"label": "Distance", "value": f"{summary.seven_day_distance_km:.1f} km"},
        {"label": "Time", "value": f"{summary.seven_day_moving_hours:.1f} h"},
        {"label": "Ascent", "value": f"{int(summary.seven_day_elevation_m):,} m"},
        {"label": "Speed", "value": f"{summary.avg_speed_kph:.1f} kph" if summary.avg_speed_kph is not None else "—"},
        {"label": "Power", "value": f"{summary.avg_power_w} W" if summary.avg_power_w is not None else "—"},
        {"label": "Cadence", "value": f"{summary.avg_cadence_rpm} rpm" if summary.avg_cadence_rpm is not None else "—"},
    ]

    return {
        "updated_utc": updated,
        "source": "Strava",
        "athlete_id": 3714458,
        "scope": "latest 60 activities; 7-day research window",
        "headline": headline,
        "state": {
            "load": classify_load(summary.seven_day_moving_hours),
            "context": classify_context(summary.indoor_count, summary.outdoor_count),
            "climbing_density": f"{summary.climbing_m_per_km:.1f} m/km" if summary.climbing_m_per_km is not None else "—",
        },
        "metrics": metrics,
        "seven_day": {
            "ride_count": summary.seven_day_count,
            "distance_km": summary.seven_day_distance_km,
            "moving_hours": summary.seven_day_moving_hours,
            "elevation_m": summary.seven_day_elevation_m,
            "indoor_count": summary.indoor_count,
            "outdoor_count": summary.outdoor_count,
            "avg_speed_kph": summary.avg_speed_kph,
            "avg_power_w": summary.avg_power_w,
            "avg_cadence_rpm": summary.avg_cadence_rpm,
            "climbing_m_per_km": summary.climbing_m_per_km,
        },
        "latest_60": {
            "ride_count": summary.count,
            "distance_km": summary.total_distance_km,
            "moving_hours": summary.total_moving_hours,
            "elevation_m": summary.total_elevation_m,
        },
        "latest_ride": {
            "name": summary.latest_name,
            "date": summary.latest_date,
            "type": summary.latest_type,
        },
    }


def main() -> int:
    access_token = get_access_token()
    activities = fetch_activities(access_token)
    summary = summarize_rides(activities)
    payload = build_footer_payload(summary)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {OUTPUT_PATH}")
    print(payload["headline"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
