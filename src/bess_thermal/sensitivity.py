"""One-at-a-time sensitivity of annual net RTE and auxiliary energy to the main assumptions."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from bess_thermal.config import COOLING_SYSTEMS, ChillerSpec, ContainerSpec, PcsTransformerSpec
from bess_thermal.simulation import simulate_container_year

# (label, group, field, low, high); the base value is read from the default spec
SENSITIVITY_CASES: list[tuple[str, str, str, float, float]] = [
    ("Envelope U-value [W/m2K]", "container", "u_envelope_w_per_m2_k", 0.5, 3.0),
    ("BMS/controls base load [kW]", "container", "bms_base_load_kw", 0.5, 3.0),
    ("Cell DC resistance at 25 degC [mOhm]", "container", "cell_r25_mohm", 0.3, 1.0),
    ("Cell voltage hysteresis [mV]", "container", "cell_hysteresis_mv", 0.0, 20.0),
    ("Cooling set point [degC]", "container", "t_cool_set_c", 22.0, 30.0),
    ("Heating set point [degC]", "container", "t_heat_set_c", 5.0, 20.0),
    ("Circulation power [kW]", "cooling", "circulation_kw", 0.75, 3.0),
    ("Chiller Carnot fraction [-]", "chiller", "carnot_fraction", 0.25, 0.50),
    ("PCS no-load loss [kW]", "pcs", "pcs_p0_kw", 2.5, 10.0),
    ("Transformer no-load loss [kW]", "pcs", "trafo_p0_kw", 0.8, 3.2),
    ("Ambient temperature shift [K]", "weather", "t_amb_c", -5.0, 5.0),
]


def _run(weather_df, duty_code, cooling_code, group, field, value) -> dict:
    container, pcs_spec, chiller = ContainerSpec(), PcsTransformerSpec(), ChillerSpec()
    cooling = COOLING_SYSTEMS[cooling_code]
    weather = weather_df
    if group == "container":
        container = replace(container, **{field: value})
    elif group == "pcs":
        pcs_spec = replace(pcs_spec, **{field: value})
    elif group == "chiller":
        chiller = replace(chiller, **{field: value})
    elif group == "cooling":
        cooling = replace(cooling, **{field: value})
    elif group == "weather":
        weather = weather_df.assign(t_amb_c=weather_df["t_amb_c"] + value)
    return simulate_container_year(
        weather, duty_code, cooling_code, container=container, pcs_spec=pcs_spec, chiller=chiller, cooling=cooling
    )["summary"]


def _base_value(group: str, field: str, cooling_code: str) -> float:
    if group == "container":
        return getattr(ContainerSpec(), field)
    if group == "pcs":
        return getattr(PcsTransformerSpec(), field)
    if group == "chiller":
        return getattr(ChillerSpec(), field)
    if group == "cooling":
        return getattr(COOLING_SYSTEMS[cooling_code], field)
    return 0.0


def run_sensitivity(
    weather_df: pd.DataFrame,
    base_summaries: dict[tuple[str, str], dict],
    duties: tuple[str, ...] = ("reserve_035", "da_100"),
    cooling_code: str = "liquid",
) -> pd.DataFrame:
    """Change one input to its low and high value, keep the rest at base, and record the annual results."""
    rows = []
    for duty_code in duties:
        base = base_summaries[(duty_code, cooling_code)]
        for label, group, field, low, high in SENSITIVITY_CASES:
            s_lo = _run(weather_df, duty_code, cooling_code, group, field, low)
            s_hi = _run(weather_df, duty_code, cooling_code, group, field, high)
            rows.append(
                {
                    "duty_code": duty_code,
                    "cooling_code": cooling_code,
                    "parameter": label,
                    "base_value": _base_value(group, field, cooling_code),
                    "low_value": low,
                    "high_value": high,
                    "rte_pcc_base": base["rte_pcc_net"],
                    "rte_pcc_low": s_lo["rte_pcc_net"],
                    "rte_pcc_high": s_hi["rte_pcc_net"],
                    "e_aux_base_mwh": round(base["e_aux_mwh"], 3),
                    "e_aux_low_mwh": round(s_lo["e_aux_mwh"], 3),
                    "e_aux_high_mwh": round(s_hi["e_aux_mwh"], 3),
                    "e_heater_low_mwh": round(s_lo["e_heater_mwh"], 3),
                    "e_heater_high_mwh": round(s_hi["e_heater_mwh"], 3),
                    "swing_pp": round(100.0 * abs(s_hi["rte_pcc_net"] - s_lo["rte_pcc_net"]), 2),
                }
            )
    return pd.DataFrame(rows)
