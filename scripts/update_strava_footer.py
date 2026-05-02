#!/usr/bin/env python3
"""
Generate Strava footer data for the Rivers Lab website.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
OUTPUT_PATH = Path("data/strava_footer.json")


@dataclass
class RideSummary:
    count: int
    distance_km: float
    moving_hours: float
    elevation_m: float
    latest_name: str | None
    latest_date: str | None


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


def fetch_activities(access_token: str, per_page: int = 30) -> list[dict[str, Any]]:
    url = f"{ACTIVITIES_URL}?per_page={per_page}&page=1"
    data = request_json(url, headers={"Authorization": f"Bearer {access_token}"})
    if not isinstance(data, list):
        raise RuntimeError("Strava activities response was not a list")
    return data


def summarize_rides(activities: list[dict[str, Any]]) -> RideSummary:
    ride_types = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide"}
    rides = [a for a in activities if a.get("type") in ride_types]

    distance_km = sum(float(a.get("distance") or 0) for a in rides) / 1000
    moving_hours = sum(float(a.get("moving_time") or 0) for a in rides) / 3600
    elevation_m = sum(float(a.get("total_elevation_gain") or 0) for a in rides)

    latest = rides[0] if rides else None
    latest_date = None
    if latest and latest.get("start_date_local"):
        latest_date = str(latest["start_date_local"]).split("T")[0]

    return RideSummary(
        count=len(rides),
        distance_km=round(distance_km, 1),
        moving_hours=round(moving_hours, 1),
        elevation_m=round(elevation_m, 0),
        latest_name=str(latest.get("name")) if latest else None,
        latest_date=latest_date,
    )


def build_footer_payload(summary: RideSummary) -> dict[str, Any]:
    updated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if summary.count == 0:
        line = "Recent cycling data unavailable"
    else:
        line = (
            f"Recent riding: {summary.distance_km:.1f} km · "
            f"{summary.moving_hours:.1f} h · {int(summary.elevation_m):,} m ascent"
        )

    return {
        "updated_utc": updated,
        "source": "Strava",
        "scope": "latest 30 activities",
        "message": line,
        "ride_count": summary.count,
        "distance_km": summary.distance_km,
        "moving_hours": summary.moving_hours,
        "elevation_m": summary.elevation_m,
        "latest_ride": {
            "name": summary.latest_name,
            "date": summary.latest_date,
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
    print(payload["message"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
