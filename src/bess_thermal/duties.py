"""Operating duty profile generators for the 2.5 MW / 5.0 MWh LFP container."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bess_thermal.config import DUTY_SPECS, ContainerSpec


def build_hourly_duty_profile(
    duty_code: str,
    container: ContainerSpec = ContainerSpec(),
    n_hours: int = 8760,
) -> pd.DataFrame:
    """Build an 8,760-hour chemical charge/discharge schedule and SoC trajectory.

    Sign convention:
      - p_chem_kw > 0 : chemical discharge rate (kW leaving stored chemical energy)
      - p_chem_kw < 0 : chemical charge rate (kW entering stored chemical energy)
      - Each 24-hour day is charge-neutral (starts and ends at 50.0% SoC), with SoC
        strictly within [0.10, 1.00], so daily, monthly, and annual dE_stored == 0.0 MWh.
    """
    if duty_code not in DUTY_SPECS:
        raise ValueError(f"Unknown duty_code '{duty_code}'. Expected one of {list(DUTY_SPECS)}")

    cap_kwh = container.rated_energy_kwh
    n_days = n_hours // 24
    day_p_chem = np.zeros(24, dtype=float)

    if duty_code == "reserve_035":
        # 0.35 cycles/day = 1,750 kWh/day chemical throughput each way
        # Starts at 2500 kWh (0.50 SoC), stays within [0.325, 0.675] SoC
        day_p_chem[2] = -450.0
        day_p_chem[3] = -425.0
        day_p_chem[7] = 425.0
        day_p_chem[8] = 450.0
        day_p_chem[12] = -425.0
        day_p_chem[13] = -450.0
        day_p_chem[18] = 450.0
        day_p_chem[19] = 425.0
    elif duty_code == "da_100":
        # 1.0 cycle/day = 5,000 kWh/day chemical throughput each way
        # Starts at 2500 kWh (0.50 SoC):
        # 01:00-04:00 charge 2000 kWh -> 4500 kWh (0.90 SoC)
        # 07:00-10:00 discharge 2500 kWh -> 2000 kWh (0.40 SoC)
        # 11:00-15:00 charge 3000 kWh -> 5000 kWh (1.00 SoC)
        # 17:00-20:00 discharge 2500 kWh -> 2500 kWh (0.50 SoC)
        day_p_chem[1] = -700.0
        day_p_chem[2] = -700.0
        day_p_chem[3] = -600.0
        day_p_chem[7] = 800.0
        day_p_chem[8] = 900.0
        day_p_chem[9] = 800.0
        day_p_chem[11] = -750.0
        day_p_chem[12] = -750.0
        day_p_chem[13] = -750.0
        day_p_chem[14] = -750.0
        day_p_chem[17] = 800.0
        day_p_chem[18] = 900.0
        day_p_chem[19] = 800.0
    elif duty_code == "heavy_200":
        # 2.0 cycles/day = 10,000 kWh/day chemical throughput each way
        # Starts at 2500 kWh (0.50 SoC), stays within [0.10, 1.00] SoC:
        # 00:00-02:00 discharge 2000 kWh -> 500 kWh (0.10 SoC)
        # 02:00-06:00 charge 4500 kWh -> 5000 kWh (1.00 SoC)
        # 06:00-10:00 discharge 4500 kWh -> 500 kWh (0.10 SoC)
        # 11:00-15:00 charge 4500 kWh -> 5000 kWh (1.00 SoC)
        # 16:00-20:00 discharge 3500 kWh -> 1500 kWh (0.30 SoC)
        # 21:00-23:00 charge 1000 kWh -> 2500 kWh (0.50 SoC)
        day_p_chem[0:2] = 1000.0
        day_p_chem[2:6] = -1125.0
        day_p_chem[6:10] = 1125.0
        day_p_chem[11:15] = -1125.0
        day_p_chem[16:20] = 875.0
        day_p_chem[21:23] = -500.0

    p_chem_kw = np.tile(day_p_chem, n_days)

    e_stored_kwh = np.empty(n_hours + 1, dtype=float)
    e_stored_kwh[0] = 0.50 * cap_kwh
    e_stored_kwh[1:] = e_stored_kwh[0] - np.cumsum(p_chem_kw)

    soc_start = e_stored_kwh[:-1] / cap_kwh
    soc_end = e_stored_kwh[1:] / cap_kwh
    soc_mid = 0.5 * (soc_start + soc_end)

    return pd.DataFrame(
        {
            "p_chem_kw": p_chem_kw,
            "soc_start": soc_start,
            "soc_end": soc_end,
            "soc_mid": soc_mid,
            "e_stored_start_kwh": e_stored_kwh[:-1],
            "e_stored_end_kwh": e_stored_kwh[1:],
            "c_rate_abs": np.abs(p_chem_kw) / cap_kwh,
        }
    )
