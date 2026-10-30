"""Electro-thermal cell physics, chiller COP, and PCS/transformer loss functions."""

from __future__ import annotations

import numpy as np

from bess_thermal.config import (
    ChillerSpec,
    ContainerSpec,
    CoolingSystemSpec,
    PcsTransformerSpec,
)


def r25_soc_factor(soc: float | np.ndarray) -> float | np.ndarray:
    """Relative SoC dependence of the 25 degC resistance: 1.0 at 50 % SoC, higher at both ends."""
    s = np.clip(soc, 0.02, 0.98)
    out = (1.0 + 0.16 * ((1.0 - s) ** 3) + 0.08 * (s**4)) / 1.025
    return float(out) if np.ndim(soc) == 0 else out


def pack_resistance_ohm(
    t_cell_c: float | np.ndarray,
    soc: float | np.ndarray = 0.50,
    spec: ContainerSpec = ContainerSpec(),
) -> float | np.ndarray:
    """Arrhenius temperature- and SoC-dependent pack DC resistance R_int(T_cell, SoC) in Ohm.

    R_int = R_cell,25 * f(SoC) * exp((E_a / R) * (1 / T_K - 1 / 298.15)) * n_series / n_parallel
    """
    t_k = np.asarray(t_cell_c, dtype=float) + 273.15
    arrhenius = np.exp(spec.ea_over_r_k * (1.0 / t_k - 1.0 / 298.15))
    r_cell = spec.cell_r25_mohm / 1000.0 * r25_soc_factor(soc) * arrhenius
    res = r_cell * spec.n_series / spec.n_parallel
    return float(res) if np.ndim(res) == 0 else res


def cell_ocv_v(soc: float | np.ndarray) -> float | np.ndarray:
    """Open-circuit voltage of one LFP cell (V): flat plateau with steep ends."""
    s = np.clip(soc, 0.01, 0.99)
    u_v = 3.2 + 0.072 * (s - 0.50) - 0.056 * np.exp(-18.0 * s) + 0.048 * np.exp(-22.0 * (1.0 - s))
    return float(u_v) if np.ndim(soc) == 0 else u_v


def pack_ocv_v(soc: float | np.ndarray, spec: ContainerSpec = ContainerSpec()) -> float | np.ndarray:
    """Open-circuit pack voltage (V)."""
    return spec.n_series * cell_ocv_v(soc)


def cell_entropic_coeff_v_per_k(soc: float | np.ndarray) -> float | np.ndarray:
    """Entropic coefficient dU_ocv/dT of one LFP cell (V/K), order 0.1 mV/K, changing sign across SoC."""
    s = np.clip(soc, 0.01, 0.99)
    du_dt = 1e-3 * (
        0.112 * np.tanh(7.5 * (s - 0.27))
        - 0.048 * np.exp(-((s - 0.52) ** 2) / 0.018)
        - 0.064 * np.exp(-((s - 0.12) ** 2) / 0.012)
    )
    return float(du_dt) if np.ndim(soc) == 0 else du_dt


def cell_heat_generation_kw(
    p_chem_kw: float,
    t_cell_c: float,
    soc: float,
    spec: ContainerSpec = ContainerSpec(),
) -> tuple[float, float, float, float]:
    """Irreversible and reversible pack heat (kW) at chemical power p_chem (+ discharge, - charge).

    Bernardi form with I > 0 on discharge:
      Q_irr = I^2 R_int + |I| * n_s * U_hyst
      Q_rev = -I * T * n_s * dU_ocv/dT
    Returns (Q_irr, Q_rev, I, R_int in mOhm).
    """
    current_a = p_chem_kw * 1000.0 / pack_ocv_v(soc, spec)
    r_ohm = pack_resistance_ohm(t_cell_c, soc, spec)
    q_irr_kw = (current_a**2 * r_ohm + abs(current_a) * spec.n_series * spec.cell_hysteresis_mv / 1000.0) / 1000.0
    q_rev_kw = -current_a * (t_cell_c + 273.15) * spec.n_series * cell_entropic_coeff_v_per_k(soc) / 1000.0
    return q_irr_kw, q_rev_kw, current_a, r_ohm * 1000.0


def dc_rte_constant_power(
    c_rate: float,
    t_cell_c: float = 25.0,
    soc: float = 0.50,
    spec: ContainerSpec = ContainerSpec(),
) -> float:
    """DC round-trip efficiency at constant chemical power c_rate * E_rated, fixed temperature and SoC."""
    p_chem = c_rate * spec.rated_energy_kwh
    q_irr, _, _, _ = cell_heat_generation_kw(p_chem, t_cell_c, soc, spec)
    loss = q_irr / p_chem
    return (1.0 - loss) / (1.0 + loss)


def chiller_cop(
    t_cold_side_c: float | np.ndarray,
    t_amb_c: float | np.ndarray,
    cooling: CoolingSystemSpec,
    chiller: ChillerSpec = ChillerSpec(),
) -> float | np.ndarray:
    """Vapour-compression COP as a fixed fraction of Carnot between evaporating and condensing temperature.

    T_evap = T_coolant/air - approach, T_cond = T_amb + approach, COP = min(COP_max, eta_C * T_evap / (T_cond - T_evap)).
    """
    t_evap_k = np.asarray(t_cold_side_c, dtype=float) - cooling.evap_approach_k + 273.15
    t_cond_k = np.asarray(t_amb_c, dtype=float) + chiller.cond_approach_k + 273.15
    lift = t_cond_k - t_evap_k
    with np.errstate(divide="ignore", invalid="ignore"):
        cop = np.where(lift > 0.0, chiller.carnot_fraction * t_evap_k / lift, chiller.cop_max)
    cop = np.minimum(cop, chiller.cop_max)
    return float(cop) if np.ndim(cop) == 0 else cop


def pcs_loss_kw(
    p_dc_term_kw: float | np.ndarray,
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
) -> float | np.ndarray:
    """Bidirectional PCS loss P_loss = p_0 + k_1 |P| + k_2 P^2 while exchanging power (kW)."""
    p_abs = np.abs(np.asarray(p_dc_term_kw, dtype=float))
    loss = np.where(
        p_abs > 1e-3,
        pcs_spec.pcs_p0_kw + pcs_spec.pcs_k1 * p_abs + pcs_spec.pcs_k2_per_kw * p_abs**2,
        pcs_spec.pcs_standby_kw,
    )
    return float(loss) if np.ndim(loss) == 0 else loss


def pcs_efficiency(load_fraction: float, pcs_spec: PcsTransformerSpec = PcsTransformerSpec()) -> float:
    """PCS conversion efficiency at a fraction of rated DC power."""
    p = load_fraction * pcs_spec.pcs_rated_kw
    return 1.0 - float(pcs_loss_kw(p, pcs_spec)) / p


def transformer_losses_kw(
    p_lv_kw: float | np.ndarray,
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """No-load (iron) loss P_0 and load (copper) loss P_k (S / S_r)^2 of the MV transformer at unity power factor (kW)."""
    p_abs = np.abs(np.asarray(p_lv_kw, dtype=float))
    p_0 = np.full_like(p_abs, pcs_spec.trafo_p0_kw, dtype=float)
    p_k = pcs_spec.trafo_pk_kw * (p_abs / pcs_spec.trafo_rated_kva) ** 2
    if np.ndim(p_lv_kw) == 0:
        return float(p_0), float(p_k)
    return p_0, p_k


def transformer_efficiency(load_fraction: float, pcs_spec: PcsTransformerSpec = PcsTransformerSpec()) -> float:
    """MV transformer efficiency at a fraction of rated apparent power (unity power factor)."""
    p = load_fraction * pcs_spec.trafo_rated_kva
    p_0, p_k = transformer_losses_kw(p, pcs_spec)
    return 1.0 - (p_0 + p_k) / p

