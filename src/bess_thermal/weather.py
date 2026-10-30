"""Hourly ERA5 weather for Central Poland (52.2 N, 21.0 E) from the Open-Meteo archive API, cached in data/.

The cached CSV holds calendar year 2025 in CET (UTC+1, no daylight saving time), 8,760 rows:
2 m air temperature and global horizontal irradiance (mean over the preceding hour).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
LATITUDE = 52.2
LONGITUDE = 21.0
YEAR = 2025
DATA_FILE = REPO_ROOT / "data" / f"era5_central_poland_{YEAR}.csv"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_era5_weather(path: Path = DATA_FILE, year: int = YEAR) -> Path:
    """Download one year of hourly ERA5 temperature and GHI from Open-Meteo and write it as CSV in CET.

    One request, 2 variables x 366 days (Open-Meteo weight ~5).
    """
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": f"{year - 1}-12-31",
        "end_date": f"{year}-12-31",
        "hourly": "temperature_2m,shortwave_radiation",
        "models": "era5",
        "timezone": "GMT",
    }
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    hourly = payload["hourly"]
    df = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(hourly["time"]),
            "t_amb_c": hourly["temperature_2m"],
            "ghi_wm2": hourly["shortwave_radiation"],
        }
    )
    df["timestamp_cet"] = df["time_utc"] + pd.Timedelta(hours=1)
    df = df[df["timestamp_cet"].dt.year == year].reset_index(drop=True)
    if len(df) != 8760 or df[["t_amb_c", "ghi_wm2"]].isna().any().any():
        raise ValueError(f"Incomplete ERA5 year: {len(df)} rows, NaNs={int(df.isna().sum().sum())}")

    path.parent.mkdir(parents=True, exist_ok=True)
    df[["timestamp_cet", "t_amb_c", "ghi_wm2"]].to_csv(path, index=False, date_format="%Y-%m-%d %H:%M")
    return path


def load_weather(path: Path = DATA_FILE) -> pd.DataFrame:
    """Load the cached hourly weather year (CET) with calendar helper columns."""
    raw = pd.read_csv(path)
    ts = pd.to_datetime(raw["timestamp_cet"])
    return pd.DataFrame(
        {
            "timestamp": ts,
            "month": ts.dt.month.to_numpy(dtype=int),
            "day_of_year": ts.dt.day_of_year.to_numpy(dtype=int),
            "hour": ts.dt.hour.to_numpy(dtype=int),
            "t_amb_c": raw["t_amb_c"].to_numpy(dtype=float),
            "ghi_wm2": raw["ghi_wm2"].to_numpy(dtype=float),
        }
    )
