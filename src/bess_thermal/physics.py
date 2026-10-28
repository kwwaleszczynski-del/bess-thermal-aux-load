"""Electro-thermal cell physics, chiller COP, two-node thermal step, and PCS/transformer loss functions."""

from __future__ import annotations

import math
import numpy as np

from bess_thermal.config import (
    COOLING_SYSTEMS,
    ContainerSpec,
    CoolingSystemSpec,
    PcsTransformerSpec,
)


def r25_pack_ohm(soc: float | np.ndarray, spec: ContainerSpec = ContainerSpec()) -> float | np.ndarray:
    """Pack internal DC resistance at 25 degC (Ohm) as a function of state of charge."""
    if isinstance(soc, (int, float, np.floating)):
        s = min(0.98, max(0.02, float(soc)))
        return spec.r25_base_ohm * (1.0 + 0.16 * ((1.0 - s) ** 3) + 0.08 * (s**4))
    s = np.clip(soc, 0.02, 0.98)
    soc_factor = 1.0 + 0.16 * ((1.0 - s) ** 3) + 0.08 * (s**4)
    return spec.r25_base_ohm * soc_factor


def cell_internal_resistance_ohm(
    t_cell_c: float | np.ndarray,
    soc: float | np.ndarray = 0.50,
    spec: ContainerSpec = ContainerSpec(),
) -> float | np.ndarray:
    """Arrhenius temperature- and SoC-dependent pack internal resistance R_int(T_cell, SoC) in Ohm.

    R_int(T, SoC) = R_25(SoC) * exp((E_a / R) * (1 / T_K - 1 / 298.15))
    Doubles between 25 degC and 5 degC.
    """
    if isinstance(t_cell_c, (int, float, np.floating)) and isinstance(soc, (int, float, np.floating)):
        t_k = float(t_cell_c) + 273.15
        arr = math.exp(spec.ea_over_r_k * (1.0 / t_k - 1.0 / 298.15))
        return float(r25_pack_ohm(float(soc), spec)) * arr
    t_k = np.asarray(t_cell_c, dtype=float) + 273.15
    arrhenius = np.exp(spec.ea_over_r_k * (1.0 / t_k - 1.0 / 298.15))
    res = r25_pack_ohm(soc, spec) * arrhenius
    if np.ndim(t_cell_c) == 0 and np.ndim(soc) == 0:
        return float(res)
    return res


def ocv_pack_voltage_v(soc: float | np.ndarray, spec: ContainerSpec = ContainerSpec()) -> float | np.ndarray:
    """Open-circuit pack voltage U_ocv(SoC) in Volts (nominal 800 V LFP pack, 250s configuration)."""
    if isinstance(soc, (int, float, np.floating)):
        s = min(0.99, max(0.01, float(soc)))
        return (
            spec.nominal_voltage_v
            + 18.0 * (s - 0.50)
            - 14.0 * math.exp(-18.0 * s)
            + 12.0 * math.exp(-22.0 * (1.0 - s))
        )
    s = np.clip(soc, 0.01, 0.99)
    u_v = (
        spec.nominal_voltage_v
        + 18.0 * (s - 0.50)
        - 14.0 * np.exp(-18.0 * s)
        + 12.0 * np.exp(-22.0 * (1.0 - s))
    )
    if np.ndim(soc) == 0:
        return float(u_v)
    return u_v


def entropic_coeff_v_per_k(soc: float | np.ndarray) -> float | np.ndarray:
    """Reversible entropic coefficient dU_ocv/dT (V/K) for the 250s LFP pack across SoC plateaus.

    Endothermic/exothermic reversals across LFP staging plateaus:
    negative at low SoC (<0.28), positive across mid/high SoC plateau (0.30..0.88).
    """
    if isinstance(soc, (int, float, np.floating)):
        s = min(0.99, max(0.01, float(soc)))
        return (
            0.028 * math.tanh(7.5 * (s - 0.27))
            - 0.012 * math.exp(-((s - 0.52) ** 2) / 0.018)
            - 0.016 * math.exp(-((s - 0.12) ** 2) / 0.012)
        )
    s = np.clip(soc, 0.01, 0.99)
    du_dt = (
        0.028 * np.tanh(7.5 * (s - 0.27))
        - 0.012 * np.exp(-((s - 0.52) ** 2) / 0.018)
        - 0.016 * np.exp(-((s - 0.12) ** 2) / 0.012)
    )
    if np.ndim(soc) == 0:
        return float(du_dt)
    return du_dt


def cell_heat_generation_kw(
    p_chem_kw: float,
    t_cell_c: float,
    soc: float,
    spec: ContainerSpec = ContainerSpec(),
) -> tuple[float, float, float, float, float]:
    """Compute irreversible Joule heat Q_joule, reversible entropic heat Q_rev, and total Q_cell (kW)."""
    u_ocv = float(ocv_pack_voltage_v(soc, spec))
    current_a = (p_chem_kw * 1000.0) / u_ocv
    r_ohm = float(cell_internal_resistance_ohm(t_cell_c, soc, spec))
    t_k = t_cell_c + 273.15
    du_dt = float(entropic_coeff_v_per_k(soc))

    q_joule_kw = (current_a**2) * r_ohm / 1000.0
    q_rev_kw = current_a * t_k * du_dt / 1000.0
    q_cell_kw = q_joule_kw + q_rev_kw
    return q_joule_kw, q_rev_kw, q_cell_kw, r_ohm * 1000.0, current_a


def chiller_cop(
    t_amb_c: float | np.ndarray,
    cooling: CoolingSystemSpec = COOLING_SYSTEMS["liquid"],
) -> float | np.ndarray:
    """Carnot-derated chiller / DX unit COP as a function of ambient temperature T_amb (degC)."""
    slope = (cooling.cop_20c - cooling.cop_35c) / 15.0
    if isinstance(t_amb_c, (int, float, np.floating)):
        val = cooling.cop_20c - slope * (float(t_amb_c) - 20.0)
        return min(cooling.cop_max, max(cooling.cop_min, val))
    cop = cooling.cop_20c - slope * (np.asarray(t_amb_c, dtype=float) - 20.0)
    cop = np.clip(cop, cooling.cop_min, cooling.cop_max)
    if np.ndim(t_amb_c) == 0:
        return float(cop)
    return cop


def pcs_loss_kw(
    p_dc_term_kw: float | np.ndarray,
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
) -> float | np.ndarray:
    """Bidirectional PCS switching and conduction loss P_loss,pcs = p_0 + k_1 |P| + k_2 P^2 (kW)."""
    if isinstance(p_dc_term_kw, (int, float, np.floating)):
        p_abs = abs(float(p_dc_term_kw))
        if p_abs <= 1e-3:
            return pcs_spec.pcs_p0_standby_kw
        return pcs_spec.pcs_p0_active_kw + pcs_spec.pcs_k1 * p_abs + pcs_spec.pcs_k2_per_kw * (p_abs**2)
    p_abs = np.abs(np.asarray(p_dc_term_kw, dtype=float))
    active = p_abs > 1e-3
    loss = np.where(
        active,
        pcs_spec.pcs_p0_active_kw + pcs_spec.pcs_k1 * p_abs + pcs_spec.pcs_k2_per_kw * (p_abs**2),
        pcs_spec.pcs_p0_standby_kw,
    )
    if np.ndim(p_dc_term_kw) == 0:
        return float(loss)
    return loss


def transformer_losses_kw(
    p_pcs_ac_kw: float | np.ndarray,
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    include_core_loss: bool = True,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """0.8/15 kV MV step-up transformer iron core loss P_fe and copper load loss P_cu (kW)."""
    if isinstance(p_pcs_ac_kw, (int, float, np.floating)):
        p_abs = abs(float(p_pcs_ac_kw))
        p_fe = pcs_spec.trafo_p_fe_kw if include_core_loss else 0.0
        p_cu = pcs_spec.trafo_p_cu_rated_kw * ((p_abs / pcs_spec.trafo_rated_kva) ** 2)
        return p_fe, p_cu
    p_abs = np.abs(np.asarray(p_pcs_ac_kw, dtype=float))
    p_fe = np.full_like(p_abs, pcs_spec.trafo_p_fe_kw if include_core_loss else 0.0, dtype=float)
    p_cu = pcs_spec.trafo_p_cu_rated_kw * ((p_abs / pcs_spec.trafo_rated_kva) ** 2)
    if np.ndim(p_pcs_ac_kw) == 0:
        return float(p_fe), float(p_cu)
    return p_fe, p_cu


def evaluate_steady_aux_vs_temperature(
    t_amb_grid_c: np.ndarray,
    c_rates: tuple[float, ...] = (0.0, 0.25, 0.50),
    container: ContainerSpec = ContainerSpec(),
) -> dict[str, dict[str, list[float]]]:
    """Evaluate hourly auxiliary power P_aux (kW) across ambient temperature (-15 to +35 degC)
    for Liquid-Cooled vs Forced-Air containers at idle (0 C), 0.25 C, and 0.50 C operation.
    """
    out: dict[str, dict[str, list[float]]] = {"t_amb_c": [round(float(t), 2) for t in t_amb_grid_c]}
    for cool_code, cool_spec in COOLING_SYSTEMS.items():
        out[cool_code] = {}
        ua_w_kw_per_k = (cool_spec.ua_wall_w_per_k / 1000.0) * 2.8
        for c_rate in c_rates:
            p_chem = c_rate * container.rated_energy_kwh
            p_aux_curve: list[float] = []
            for t_amb in t_amb_grid_c:
                t_cell_eq = float(np.clip(12.0 + 0.42 * max(0.0, t_amb - 5.0) + 4.5 * c_rate, 12.0, 28.5))
                q_joule_raw, _, _, _, _ = cell_heat_generation_kw(p_chem, t_cell_eq, 0.50, container)
                q_joule = q_joule_raw * 0.24
                q_solar = (
                    0.0
                    if t_amb < 5.0
                    else container.solar_absorptance
                    * 320.0
                    * container.roof_area_m2
                    / 1000.0
                    * max(0.0, (t_amb - 5.0) / 30.0)
                )

                q_wall_loss_at_12 = ua_w_kw_per_k * max(0.0, 12.0 - t_amb)
                q_wall_gain_at_24 = ua_w_kw_per_k * max(0.0, t_amb - 20.0)

                q_heat_needed = max(0.0, q_wall_loss_at_12 * (1.0 + 0.8 * c_rate) - 0.12 * q_joule - q_solar)
                p_ptc_kw = q_heat_needed / cool_spec.ptc_cop

                q_cool_needed = (
                    max(0.0, 0.72 * q_joule + q_solar + q_wall_gain_at_24)
                    if (t_amb > 16.0 or c_rate > 0.0)
                    else 0.0
                )
                cop_val = float(chiller_cop(t_amb, cool_spec))
                p_chiller_kw = q_cool_needed / cop_val

                thermal_duty = min(1.0, (q_heat_needed + q_cool_needed) / 14.0 + (0.35 if c_rate > 0 else 0.0))
                p_pump_kw = cool_spec.pump_or_fan_kw * thermal_duty

                p_total_aux = container.bms_base_load_kw + p_ptc_kw + p_chiller_kw + p_pump_kw
                p_aux_curve.append(round(float(p_total_aux), 3))
            out[cool_code][f"c_{c_rate:.2f}"] = p_aux_curve
    return out
