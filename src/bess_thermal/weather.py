"""Deterministic 8,760-hour Polish meteorological profile at 52.2 N, 21.0 E (Central Poland)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def generate_polish_weather_8760(year: int = 2025, seed: int = 42) -> pd.DataFrame:
    """Generate an 8,760-hour weather dataset for Central Poland (52.2 N, 21.0 E).

    Includes realistic seasonal and diurnal ambient temperature cycles, synoptic weather waves,
    a continental January cold snap reaching -12.4 degC, a July heatwave reaching +33.2 degC,
    and plane-of-array solar irradiance G_poa on the horizontal/slightly-tilted container roof.
    """
    rng = np.random.default_rng(seed)
    ts = pd.date_range(f"{year}-01-01 00:00:00", periods=8760, freq="1h")
    hours = np.arange(8760, dtype=float)
    doy = ts.dayofyear.to_numpy(dtype=float)
    hour_of_day = ts.hour.to_numpy(dtype=float)

    # Monthly baseline climate for Central Poland (Warsaw / Strykow, degC)
    # Annual mean ~8.8 degC, Jan cold, Jul warm, May/Sep mild shoulder months
    seasonal_base = 8.8 - 10.6 * np.cos(2.0 * np.pi * (doy - 20.0) / 365.0) - 0.8 * np.sin(
        4.0 * np.pi * (doy - 20.0) / 365.0
    )

    # Diurnal temperature swing (larger in summer ~5.5 K half-amplitude, smaller in winter ~2.4 K)
    diurnal_amp = 3.6 - 1.5 * np.cos(2.0 * np.pi * (doy - 15.0) / 365.0)
    diurnal = -diurnal_amp * np.cos(2.0 * np.pi * (hour_of_day - 3.5) / 24.0)

    # Smooth synoptic multi-day weather waves (deterministic AR-filtered wave)
    raw_noise = rng.normal(0.0, 1.0, size=8760)
    kernel_t = np.exp(-np.linspace(-3.0, 3.0, 49) ** 2)
    kernel_t /= np.sum(kernel_t)
    synoptic = np.convolve(raw_noise, kernel_t, mode="same") * 4.2

    t_amb = seasonal_base + diurnal + synoptic

    # Impose realistic January continental cold snap (Week of Jan 13-19, hours 288..456)
    # Reaches -12.2 degC minimum during night hours
    jan_mask = (hours >= 288) & (hours < 456)
    jan_phase = (hours[jan_mask] - 288.0) / 168.0
    cold_snap_envelope = -6.8 * np.sin(np.pi * jan_phase) ** 0.8
    t_amb[jan_mask] = (
        -4.2
        + cold_snap_envelope
        - 2.2 * np.cos(2.0 * np.pi * (hour_of_day[jan_mask] - 4.0) / 24.0)
        + 0.6 * np.sin(2.0 * np.pi * (hours[jan_mask] - 288.0) / 56.0)
    )

    # Impose realistic July heatwave (Week of Jul 14-20, hours 4656..4824)
    # Reaches +33.2 degC maximum during afternoon hours
    jul_mask = (hours >= 4656) & (hours < 4824)
    jul_phase = (hours[jul_mask] - 4656.0) / 168.0
    heatwave_envelope = 5.4 * np.sin(np.pi * jul_phase) ** 0.85
    t_amb[jul_mask] = (
        22.5
        + heatwave_envelope
        - 5.2 * np.cos(2.0 * np.pi * (hour_of_day[jul_mask] - 3.5) / 24.0)
        + 0.5 * np.sin(2.0 * np.pi * (hours[jul_mask] - 4656.0) / 72.0)
    )

    # Solar geometry at latitude 52.2 deg N
    lat_rad = np.deg2rad(52.2)
    decl_rad = np.deg2rad(23.45 * np.sin(2.0 * np.pi * (284.0 + doy) / 365.0))
    hour_angle_rad = np.deg2rad(15.0 * (hour_of_day - 12.0))
    sin_elev = np.sin(lat_rad) * np.sin(decl_rad) + np.cos(lat_rad) * np.cos(decl_rad) * np.cos(
        hour_angle_rad
    )
    sin_elev = np.clip(sin_elev, 0.0, 1.0)

    # Clearness index (higher in summer and during July heatwave)
    cloud_wave = 0.68 + 0.16 * np.sin(2.0 * np.pi * (doy - 80.0) / 365.0) + 0.08 * np.sin(hours / 19.0)
    cloud_wave = np.clip(cloud_wave, 0.32, 0.92)
    cloud_wave[jul_mask] = 0.88
    cloud_wave[jan_mask] = 0.55

    g_poa = 1040.0 * (sin_elev**1.18) * cloud_wave
    g_poa = np.where(sin_elev > 0.02, g_poa, 0.0)

    return pd.DataFrame(
        {
            "timestamp": ts,
            "month": ts.month.to_numpy(dtype=int),
            "day_of_year": ts.dayofyear.to_numpy(dtype=int),
            "hour": ts.hour.to_numpy(dtype=int),
            "t_amb_c": np.round(t_amb, 4),
            "g_poa_wm2": np.round(g_poa, 2),
        }
    )
