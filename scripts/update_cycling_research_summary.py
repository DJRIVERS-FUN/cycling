#!/usr/bin/env python3
"""Generate Strava-backed data files for the cycling dashboard and footer.

Required environment variables:
  STRAVA_CLIENT_ID
  STRAVA_CLIENT_SECRET
  STRAVA_REFRESH_TOKEN

Outputs:
  data/cycling_research_summary.json
  data/strava_footer.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_OUTFILE = ROOT / "data" / "cycling_research_summary.json"
FOOTER_OUTFILE = ROOT / "data" / "strava_footer.json"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
FTP_WATTS = float(os.environ.get("CYCLING_FTP_WATTS", "220"))


@dataclass
class Ride:
    date: str
    datetime: str
    name: str
    type: str
    is_indoor: bool
    distance_km: float
    moving_h: float
    elevation_m: float
    avg_speed_kph: float | None
    avg_power_w: float | None
    avg_cadence_rpm: float | None


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def post_form(url: str, data: dict[str, str], max_retries: int = 3) -> dict[str, Any]:
    """POST form data with exponential backoff retry logic."""
    encoded = urlencode(data).encode("utf-8")
    
    for attempt in range(max_retries):
        try:
            request = Request(url, data=encoded, method="POST")
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_detail = json.loads(error_body)
            except json.JSONDecodeError:
                error_detail = error_body
            
            # Don't retry on 401/403 auth errors
            if e.code in (401, 403):
                raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nResponse: {error_detail}") from e
            
            # Retry on 5xx errors and 429 rate limiting
            if attempt < max_retries - 1 and e.code >= 500:
                wait_time = 2 ** attempt
                print(f"Attempt {attempt + 1} failed with HTTP {e.code}. Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nResponse: {error_detail}") from e


def get_json(url: str, token: str, max_retries: int = 3) -> list[dict[str, Any]]:
    """GET JSON with exponential backoff retry logic."""
    for attempt in range(max_retries):
        try:
            request = Request(url, headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_detail = json.loads(error_body)
            except json.JSONDecodeError:
                error_detail = error_body
            
            # Don't retry on 401/403 auth errors
            if e.code in (401, 403):
                raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nResponse: {error_detail}") from e
            
            # Retry on 5xx errors, 429 rate limiting, and timeout-like issues
            if attempt < max_retries - 1 and e.code >= 500:
                wait_time = 2 ** attempt
                print(f"Attempt {attempt + 1} failed with HTTP {e.code}. Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nResponse: {error_detail}") from e


def get_access_token() -> str:
    token_data = post_form(
        TOKEN_URL,
        {
            "client_id": require_env("STRAVA_CLIENT_ID"),
            "client_secret": require_env("STRAVA_CLIENT_SECRET"),
            "refresh_token": require_env("STRAVA_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
    )
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Strava token refresh did not return an access_token")
    return str(access_token)


def fetch_activities(token: str, days: int = 100) -> list[dict[str, Any]]:
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    all_items: list[dict[str, Any]] = []
    page = 1
    while True:
        params = urlencode({"after": after, "per_page": 200, "page": page})
        batch = get_json(f"{ACTIVITIES_URL}?{params}", token)
        if not batch:
            break
        all_items.extend(batch)
        if len(batch) < 200:
            break
        page += 1
    return all_items


def num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def round_or_none(value: Any, digits: int = 1) -> float | None:
    n = num(value)
    return round(n, digits) if n is not None else None


def parse_start(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_ride(activity: dict[str, Any]) -> Ride | None:
    sport_type = activity.get("sport_type") or activity.get("type") or "Ride"
    if sport_type not in {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide"}:
        return None

    start = parse_start(activity["start_date"])
    distance_km = round((num(activity.get("distance")) or 0) / 1000, 2)
    moving_h = round((num(activity.get("moving_time")) or 0) / 3600, 3)
    speed = num(activity.get("average_speed"))
    is_indoor = bool(activity.get("trainer")) or sport_type == "VirtualRide"

    return Ride(
        date=start.date().isoformat(),
        datetime=start.isoformat(),
        name=str(activity.get("name") or "Untitled ride"),
        type=str(sport_type),
        is_indoor=is_indoor,
        distance_km=distance_km,
        moving_h=moving_h,
        elevation_m=round_or_none(activity.get("total_elevation_gain"), 1) or 0,
        avg_speed_kph=round(speed * 3.6, 1) if speed is not None else None,
        avg_power_w=round_or_none(activity.get("average_watts"), 1),
        avg_cadence_rpm=round_or_none(activity.get("average_cadence"), 1),
    )


def week_start(date_value: datetime) -> str:
    d = date_value.date()
    return (d - timedelta(days=d.weekday())).isoformat()


def average(values: list[float | None], digits: int = 1) -> float | None:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), digits) if clean else None


def power_zone(avg_power_w: float | None) -> str | None:
    if avg_power_w is None or FTP_WATTS <= 0:
        return None
    ratio = avg_power_w / FTP_WATTS
    if ratio < 0.55:
        return "Z1"
    if ratio < 0.76:
        return "Z2"
    if ratio < 0.90:
        return "Z3"
    if ratio < 1.05:
        return "Z4"
    return "Z5+"


def classify_ride(ride: Ride) -> dict[str, Any]:
    """Conservative rule-based ride classification.

    This is not a substitute for interval-level file analysis. It uses ride name,
    duration, distance, climbing density and average power relative to FTP.
    """
    name = ride.name.lower()
    zone = power_zone(ride.avg_power_w)
    climbing_density = round(ride.elevation_m / ride.distance_km, 1) if ride.distance_km else 0

    signals: list[str] = []
    if zone:
        signals.append(f"avg power {zone}")
    if climbing_density >= 15:
        signals.append("high climbing density")
    if ride.is_indoor:
        signals.append("indoor/trainer context")
    else:
        signals.append("outdoor/field context")

    if any(k in name for k in ["interval", "vo2", "threshold", "ftp", "race", "ramp"]):
        return {"training_tag": "interval", "training_zone": zone, "training_notes": signals + ["keyword signal"]}
    if ride.moving_h >= 2.5 or ride.distance_km >= 60:
        return {"training_tag": "long ride", "training_zone": zone, "training_notes": signals + ["duration/distance signal"]}
    if zone == "Z2" and ride.moving_h >= 0.75:
        return {"training_tag": "Z2", "training_zone": zone, "training_notes": signals + ["endurance-zone signal"]}
    if zone in {"Z3", "Z4", "Z5+"} and ride.moving_h < 1.5:
        return {"training_tag": "tempo / hard", "training_zone": zone, "training_notes": signals + ["higher average-power signal"]}
    if ride.moving_h < 0.75 and (zone in {None, "Z1", "Z2"}):
        return {"training_tag": "short easy", "training_zone": zone, "training_notes": signals + ["short-duration signal"]}
    return {"training_tag": "general endurance", "training_zone": zone, "training_notes": signals}


def sum_window(rides: list[Ride], since: datetime) -> dict[str, Any]:
    items = [r for r in rides if parse_start(r.datetime) >= since]
    return {
        "distance_km": round(sum(r.distance_km for r in items), 1),
        "moving_h": round(sum(r.moving_h for r in items), 1),
        "elevation_m": round(sum(r.elevation_m for r in items), 1),
        "ride_count": len(items),
        "mean_power_w": average([r.avg_power_w for r in items]),
        "mean_cadence_rpm": average([r.avg_cadence_rpm for r in items]),
        "mean_speed_kph": average([r.avg_speed_kph for r in items]),
    }


def build_weekly_series(rides: list[Ride], trend_weeks: int = 12) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    first_week = now.date() - timedelta(days=now.weekday(), weeks=trend_weeks - 1)
    buckets: dict[str, list[Ride]] = defaultdict(list)
    for ride in rides:
        start = parse_start(ride.datetime)
        if start.date() >= first_week:
            buckets[week_start(start)].append(ride)

    out = []
    for i in range(trend_weeks):
        ws = (first_week + timedelta(weeks=i)).isoformat()
        items = buckets.get(ws, [])
        tag_counts = Counter(classify_ride(r)["training_tag"] for r in items)
        out.append(
            {
                "week_start": ws,
                "distance_km": round(sum(r.distance_km for r in items), 1),
                "moving_h": round(sum(r.moving_h for r in items), 1),
                "elevation_m": round(sum(r.elevation_m for r in items), 1),
                "ride_count": len(items),
                "indoor_count": sum(1 for r in items if r.is_indoor),
                "outdoor_count": sum(1 for r in items if not r.is_indoor),
                "training_tags": dict(tag_counts),
            }
        )
    return out


def asdict_ride(ride: Ride) -> dict[str, Any]:
    return {
        "date": ride.date,
        "datetime": ride.datetime,
        "name": ride.name,
        "type": ride.type,
        "is_indoor": ride.is_indoor,
        "distance_km": ride.distance_km,
        "moving_h": ride.moving_h,
        "elevation_m": ride.elevation_m,
        "avg_speed_kph": ride.avg_speed_kph,
        "avg_power_w": ride.avg_power_w,
        "avg_cadence_rpm": ride.avg_cadence_rpm,
        **classify_ride(ride),
    }


def load_state(hours: float) -> str:
    if hours < 4:
        return "light"
    if hours < 8:
        return "moderate"
    return "high"


def build_dashboard_payload(rides: list[Ride], now: datetime) -> dict[str, Any]:
    s7 = sum_window(rides, now - timedelta(days=7))
    s30 = sum_window(rides, now - timedelta(days=30))
    recent = rides[-30:]
    latest = rides[-1] if rides else None
    tag_counts = Counter(classify_ride(r)["training_tag"] for r in recent)

    power_cadence = [
        {
            "date": r.date,
            "name": r.name,
            "power_w": r.avg_power_w,
            "cadence_rpm": r.avg_cadence_rpm,
            "distance_km": r.distance_km,
            "context": "Indoor" if r.is_indoor else "Outdoor",
            **classify_ride(r),
        }
        for r in recent
        if r.avg_power_w is not None and r.avg_cadence_rpm is not None
    ]

    return {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": "Cycling behavioural regulation micro-dashboard",
        "window_days": {"short": 7, "analysis": 30, "trend_weeks": 12},
        "classification_model": {
            "method": "rule-based ride-level tagging",
            "ftp_watts": FTP_WATTS,
            "tags": ["Z2", "interval", "long ride", "tempo / hard", "short easy", "general endurance"],
        },
        "state": {
            "load_state": load_state(s7["moving_h"]),
            "context_balance": {
                "indoor": sum(1 for r in rides if r.is_indoor),
                "outdoor": sum(1 for r in rides if not r.is_indoor),
            },
            "seven_day": s7,
            "thirty_day": s30,
            "recent_training_tags": dict(tag_counts),
        },
        "weekly_series": build_weekly_series(rides),
        "recent_rides": [asdict_ride(r) for r in recent],
        "power_cadence": power_cadence,
        "latest_ride": asdict_ride(latest) if latest else {},
    }


def build_footer_payload(rides: list[Ride], now: datetime) -> dict[str, Any]:
    s7 = sum_window(rides, now - timedelta(days=7))
    latest_60 = rides[-60:]
    latest = rides[-1] if rides else None
    latest_tag = classify_ride(latest)["training_tag"] if latest else "—"
    load_label = f"{load_state(s7['moving_h']).title()} load"
    distance = s7["distance_km"]
    moving = s7["moving_h"]
    elevation = s7["elevation_m"]
    climbing = round(elevation / distance, 1) if distance else 0

    avg_power = round(s7["mean_power_w"]) if s7["mean_power_w"] is not None else None
    avg_cadence = round(s7["mean_cadence_rpm"]) if s7["mean_cadence_rpm"] is not None else None
    avg_speed = s7["mean_speed_kph"]

    return {
        "updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Strava",
        "athlete_id": 3714458,
        "scope": "latest 60 activities; 7-day research window",
        "headline": f"7-day riding state: {distance} km · {moving} h · {round(elevation)} m+",
        "state": {
            "load": load_label,
            "context": "Indoor-trainer" if s7.get("ride_count", 0) and all(r.is_indoor for r in rides if parse_start(r.datetime) >= now - timedelta(days=7)) else "Outdoor-field",
            "climbing_density": f"{climbing} m/km",
            "latest_training_tag": latest_tag,
        },
        "metrics": [
            {"label": "Distance", "value": f"{distance} km"},
            {"label": "Time", "value": f"{moving} h"},
            {"label": "Ascent", "value": f"{round(elevation)} m"},
            {"label": "Speed", "value": f"{avg_speed} kph" if avg_speed is not None else "—"},
            {"label": "Power", "value": f"{avg_power} W" if avg_power is not None else "—"},
            {"label": "Cadence", "value": f"{avg_cadence} rpm" if avg_cadence is not None else "—"},
        ],
        "seven_day": {
            "ride_count": s7["ride_count"],
            "distance_km": distance,
            "moving_hours": moving,
            "elevation_m": elevation,
            "indoor_count": sum(1 for r in rides if r.is_indoor and parse_start(r.datetime) >= now - timedelta(days=7)),
            "outdoor_count": sum(1 for r in rides if (not r.is_indoor) and parse_start(r.datetime) >= now - timedelta(days=7)),
            "avg_speed_kph": avg_speed,
            "avg_power_w": avg_power,
            "avg_cadence_rpm": avg_cadence,
            "climbing_m_per_km": climbing,
        },
        "latest_60": {
            "ride_count": len(latest_60),
            "distance_km": round(sum(r.distance_km for r in latest_60), 1),
            "moving_hours": round(sum(r.moving_h for r in latest_60), 1),
            "elevation_m": round(sum(r.elevation_m for r in latest_60), 1),
        },
        "latest_ride": {"name": latest.name, "date": latest.date, "type": latest.type, "training_tag": latest_tag} if latest else {},
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    token = get_access_token()
    raw = fetch_activities(token)
    rides = [r for a in raw if (r := normalize_ride(a)) is not None]
    rides.sort(key=lambda r: r.datetime)

    now = datetime.now(timezone.utc)
    write_json(DASHBOARD_OUTFILE, build_dashboard_payload(rides, now))
    write_json(FOOTER_OUTFILE, build_footer_payload(rides, now))

    print(f"Wrote {DASHBOARD_OUTFILE.relative_to(ROOT)} and {FOOTER_OUTFILE.relative_to(ROOT)} with {len(rides)} rides")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
