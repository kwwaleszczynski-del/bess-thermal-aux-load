"""Checks of the simulation that can fail, plus the piecewise-linear auxiliary surrogate.

What each check shows:
  - plant energy balance: bookkeeping. Every electrical loss must reappear as stored or rejected heat.
  - integrator vs matrix exponential: the implicit-Euler two-node solver against the exact solution.
  - time-step convergence: annual results at 15-min vs 5-min sub-steps.
  - idle heater steady state: simulated heater power against the closed-form envelope loss.
  - implied ratings: datasheet-comparable values that follow from the stated inputs.
  - surrogate: the 4-parameter auxiliary model of bess-ops-warranty fitted to this model's output
    (not a validation of the physics; no measured container data are used anywhere).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
from scipy.linalg import expm
from scipy.optimize import nnls

from bess_thermal.config import COOLING_SYSTEMS, ChillerSpec, ContainerSpec, PcsTransformerSpec
from bess_thermal.physics import chiller_cop, dc_rte_constant_power, pcs_efficiency, transformer_efficiency
from bess_thermal.simulation import run_container, simulate_container_year


def generate_synthetic_telemetry_for_recovery(
    weather_df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    b0_kw_true: float = 2.0,
    h_kw_per_k_true: float = 0.30,
    t_h_c_true: float = 5.0,
    c_kw_per_k_true: float = 0.40,
    t_c_c_true: float = 18.0,
    k_kw_per_mw_true: float = 1.20,
    noise_std_kw: float = 0.10,
    seed: int = 101,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Synthetic hourly auxiliary telemetry from known piecewise-linear parameters plus Gaussian noise.

    Used only to test that the estimator recovers parameters it was given; it says nothing about the physics.
      P_aux(t) = b_0 + h * max(T_h - T_amb(t), 0) + c * max(T_amb(t) - T_c, 0) + k * |P_pcs(t)|
    """
    rng = np.random.default_rng(seed)
    t_amb = weather_df["t_amb_c"].to_numpy(dtype=float)
    p_pcs_mw = np.abs(hourly_df["p_pcs_ac_kw"].to_numpy(dtype=float)) / 1000.0
    p_aux_clean_kw = (
        b0_kw_true
        + h_kw_per_k_true * np.maximum(t_h_c_true - t_amb, 0.0)
        + c_kw_per_k_true * np.maximum(t_amb - t_c_c_true, 0.0)
        + k_kw_per_mw_true * p_pcs_mw
    )
    p_aux_meas_kw = p_aux_clean_kw + rng.normal(0.0, noise_std_kw, size=len(t_amb))
    df_tel = pd.DataFrame({"t_amb_c": t_amb, "p_pcs_mw": p_pcs_mw, "p_aux_meas_kw": p_aux_meas_kw})
    truth = {
        "b0_kw": b0_kw_true,
        "h_kw_per_k": h_kw_per_k_true,
        "t_h_c": t_h_c_true,
        "c_kw_per_k": c_kw_per_k_true,
        "t_c_c": t_c_c_true,
        "k_kw_per_mw": k_kw_per_mw_true,
    }
    return df_tel, truth


def fit_piecewise_linear_aux_model(
    t_amb_c: np.ndarray,
    p_pcs_mw: np.ndarray,
    p_aux_kw: np.ndarray,
    t_h_candidates: np.ndarray | None = None,
    t_c_candidates: np.ndarray | None = None,
) -> dict[str, float]:
    """Fit P_aux = b_0 + h * max(T_h - T, 0) + c * max(T - T_c, 0) + k * |P_pcs|
    by two-dimensional breakpoint grid search and non-negative least squares (NNLS).
    """
    t_amb = np.asarray(t_amb_c, dtype=float)
    p_abs = np.abs(np.asarray(p_pcs_mw, dtype=float))
    y = np.asarray(p_aux_kw, dtype=float)

    if t_h_candidates is None:
        t_h_candidates = np.round(np.arange(-5.0, 15.5, 0.5), 2)
    if t_c_candidates is None:
        t_c_candidates = np.round(np.arange(5.0, 25.5, 0.5), 2)

    best_sse = float("inf")
    best_params: dict[str, float] = {}
    ones = np.ones_like(t_amb)
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))

    for th in t_h_candidates:
        x_heat = np.maximum(th - t_amb, 0.0)
        for tc in t_c_candidates:
            if tc <= th + 1.0:
                continue
            x_cool = np.maximum(t_amb - tc, 0.0)
            design = np.column_stack([ones, x_heat, x_cool, p_abs])
            coeffs, _ = nnls(design, y)
            sse = float(np.sum((y - design @ coeffs) ** 2))
            if sse < best_sse:
                best_sse = sse
                best_params = {
                    "b0_kw": round(float(coeffs[0]), 4),
                    "h_kw_per_k": round(float(coeffs[1]), 4),
                    "t_h_c": float(th),
                    "c_kw_per_k": round(float(coeffs[2]), 4),
                    "t_c_c": float(tc),
                    "k_kw_per_mw": round(float(coeffs[3]), 4),
                    "r2": round(1.0 - sse / max(ss_tot, 1e-12), 4),
                    "rmse_kw": round(float(np.sqrt(sse / len(y))), 4),
                }
    return best_params


def check_integrator_against_exact(
    container: ContainerSpec = ContainerSpec(),
    cooling_code: str = "air",
    t_amb_c: float = 0.0,
    days: int = 10,
) -> dict:
    """Free-floating two-node response (HVAC idle, no sun) from run_container vs the matrix exponential.

    The cell and enclosure start at different temperatures inside the control band, the battery is idle,
    and the band is widened so the HVAC never acts. The exact solution of C dT/dt = A T + b is
    T(t) = T_ss + expm(C^-1 A t) (T_0 - T_ss).
    """
    cooling = COOLING_SYSTEMS[cooling_code]
    wide = replace(container, t_heat_set_c=-100.0, t_cool_set_c=100.0)
    n_h = days * 24
    t0 = (24.0, 14.0)
    res = run_container(
        np.full(n_h, t_amb_c), np.zeros(n_h), np.zeros(n_h), np.full(n_h, 0.5), np.full(n_h, 0.5),
        cooling, wide, t_init_c=t0,
    )
    c = np.array([wide.c_cell_mj_per_k / 3.6, wide.c_enc_mj_per_k / 3.6])
    u, w = cooling.ua_int_kw_per_k, wide.ua_envelope_kw_per_k
    a_mat = np.array([[-u, u], [u, -u - w]])
    b_vec = np.array([0.0, w * t_amb_c + wide.bms_base_load_kw])
    t_ss = np.linalg.solve(-a_mat, b_vec)
    m = a_mat / c[:, None]
    hours = np.arange(1, n_h + 1, dtype=float)
    exact = np.array([t_ss + expm(m * t) @ (np.array(t0) - t_ss) for t in hours])
    err_cell = np.abs(res["t_cell_end"] - exact[:, 0])
    err_enc = np.abs(res["t_enc_end"] - exact[:, 1])
    return {
        "cooling_code": cooling_code,
        "days": days,
        "max_abs_err_cell_k": round(float(err_cell.max()), 4),
        "max_abs_err_enclosure_k": round(float(err_enc.max()), 4),
        "max_abs_err_after_first_hour_k": round(float(max(err_cell[1:].max(), err_enc[1:].max())), 4),
        "pass_0p1_k": bool(err_cell.max() < 0.1),
    }


def check_timestep_convergence(
    weather_df: pd.DataFrame,
    duty_code: str = "da_100",
    cooling_code: str = "air",
    coarse: int = 4,
    fine: int = 12,
) -> dict:
    """Annual results at 60/coarse-min against 60/fine-min sub-steps."""
    s_c = simulate_container_year(weather_df, duty_code, cooling_code, n_substeps=coarse)["summary"]
    s_f = simulate_container_year(weather_df, duty_code, cooling_code, n_substeps=fine)["summary"]
    d_rte_pp = 100.0 * abs(s_c["rte_pcc_net"] - s_f["rte_pcc_net"])
    d_aux_pct = 100.0 * abs(s_c["e_aux_mwh"] - s_f["e_aux_mwh"]) / s_f["e_aux_mwh"]
    return {
        "duty_code": duty_code,
        "cooling_code": cooling_code,
        "substep_min": [60 // coarse, 60 // fine],
        "rte_pcc_net": [s_c["rte_pcc_net"], s_f["rte_pcc_net"]],
        "e_aux_mwh": [round(s_c["e_aux_mwh"], 3), round(s_f["e_aux_mwh"], 3)],
        "abs_diff_rte_pp": round(d_rte_pp, 4),
        "abs_diff_aux_pct": round(d_aux_pct, 3),
        "pass": bool(d_rte_pp < 0.05 and d_aux_pct < 1.0),
    }


def check_idle_heater_steady_state(
    container: ContainerSpec = ContainerSpec(),
    cooling_code: str = "liquid",
    t_amb_c: float = -30.0,
    days: int = 10,
) -> dict:
    """Idle container at constant cold ambient, no sun: heater power vs the closed form.

    At steady state both nodes sit at T_heat, so P_heater = (UA_env (T_heat - T_amb) - P_bms - P_circ) / eta.
    """
    cooling = COOLING_SYSTEMS[cooling_code]
    n_h = days * 24
    res = run_container(
        np.full(n_h, t_amb_c), np.zeros(n_h), np.zeros(n_h), np.full(n_h, 0.5), np.full(n_h, 0.5),
        cooling, container, t_init_c=(container.t_heat_set_c, container.t_heat_set_c),
    )
    sim_kw = float(np.mean(res["hourly"]["p_heater_kwh"][-24:]))
    closed_kw = (
        container.ua_envelope_kw_per_k * (container.t_heat_set_c - t_amb_c)
        - container.bms_base_load_kw
        - cooling.circulation_kw
    ) / container.heater_efficiency
    return {
        "cooling_code": cooling_code,
        "t_amb_c": t_amb_c,
        "heater_sim_kw": round(sim_kw, 4),
        "heater_closed_form_kw": round(closed_kw, 4),
        "rel_err": round(abs(sim_kw - closed_kw) / closed_kw, 6),
        "pass": bool(abs(sim_kw - closed_kw) / closed_kw < 1e-3),
    }


def implied_ratings(
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    chiller: ChillerSpec = ChillerSpec(),
) -> dict:
    """Datasheet-comparable values that follow from the stated inputs (no fitting)."""
    return {
        "cell_dc_rte_0p5c_25c": round(dc_rte_constant_power(0.5, 25.0, 0.5, container), 4),
        "cell_dc_rte_0p2c_25c": round(dc_rte_constant_power(0.2, 25.0, 0.5, container), 4),
        "cell_dc_rte_0p2c_12c": round(dc_rte_constant_power(0.2, 12.0, 0.5, container), 4),
        "pcs_efficiency_10_25_50_100_pct": [round(pcs_efficiency(x, pcs_spec), 4) for x in (0.1, 0.25, 0.5, 1.0)],
        "transformer_efficiency_25_50_100_pct": [
            round(transformer_efficiency(x, pcs_spec), 4) for x in (0.25, 0.5, 1.0)
        ],
        "transformer_no_load_mwh_per_year": round(pcs_spec.trafo_p0_kw * 8.76, 3),
        "bms_base_mwh_per_year": round(container.bms_base_load_kw * 8.76, 3),
        "cop_liquid_coolant18_amb35": round(chiller_cop(18.0, 35.0, COOLING_SYSTEMS["liquid"], chiller), 3),
        "cop_air_air20_amb35": round(chiller_cop(20.0, 35.0, COOLING_SYSTEMS["air"], chiller), 3),
        "c_cell_mj_per_k": round(container.c_cell_mj_per_k, 2),
        "ua_envelope_w_per_k": round(container.ua_envelope_kw_per_k * 1000.0, 1),
        "envelope_time_constant_h": round(
            (container.c_cell_mj_per_k + container.c_enc_mj_per_k) / 3.6 / container.ua_envelope_kw_per_k, 1
        ),
    }


def run_verification_suite(sim_runs: dict[tuple[str, str], dict], weather_df: pd.DataFrame) -> dict:
    """Run every check and the surrogate fits; returns a JSON-serialisable dict."""
    balance_rows = []
    for (duty_code, cool_code), res in sim_runs.items():
        b = res["energy_balance"]
        balance_rows.append(
            {
                "duty_code": duty_code,
                "cooling_code": cool_code,
                "net_import_pcc_mwh": round(b["net_import_pcc_mwh"], 6),
                "sum_terms_mwh": round(b["sum_terms_mwh"], 6),
                "residual_mwh": b["residual_mwh"],
                "terms_mwh": {k: round(v, 6) for k, v in b["terms_mwh"].items()},
            }
        )
    max_residual = max(r["residual_mwh"] for r in balance_rows)

    da_liq = sim_runs[("da_100", "liquid")]["hourly_df"]
    df_tel, truth = generate_synthetic_telemetry_for_recovery(weather_df, da_liq)
    est = fit_piecewise_linear_aux_model(df_tel["t_amb_c"], df_tel["p_pcs_mw"], df_tel["p_aux_meas_kw"])

    surrogate = {}
    for cool_code in COOLING_SYSTEMS:
        h = sim_runs[("da_100", cool_code)]["hourly_df"]
        hourly_fit = fit_piecewise_linear_aux_model(
            h["t_amb_c"], np.abs(h["p_pcs_ac_kw"]) / 1000.0, h["p_aux_kw"]
        )
        day = h.groupby(h["timestamp"].dt.date).agg(
            t_amb_c=("t_amb_c", "mean"), p_aux_kw=("p_aux_kw", "mean")
        )
        day_pcs = (np.abs(h["p_pcs_ac_kw"]) / 1000.0).groupby(h["timestamp"].dt.date).mean()
        daily_fit = fit_piecewise_linear_aux_model(day["t_amb_c"], day_pcs.to_numpy(), day["p_aux_kw"])
        surrogate[f"da_100_{cool_code}"] = {"hourly": hourly_fit, "daily": daily_fit}

    return {
        "plant_energy_balance": {
            "max_residual_mwh": max_residual,
            "pass_1e6_mwh": bool(max_residual < 1e-6),
            "runs": balance_rows,
        },
        "integrator_vs_exact": [check_integrator_against_exact(cooling_code=c) for c in COOLING_SYSTEMS],
        "timestep_convergence": [check_timestep_convergence(weather_df, cooling_code=c) for c in COOLING_SYSTEMS],
        "idle_heater_steady_state": [check_idle_heater_steady_state(cooling_code=c) for c in COOLING_SYSTEMS],
        "implied_ratings": implied_ratings(),
        "estimator_self_test": {
            "truth": truth,
            "estimate": est,
            "abs_errors": {k: round(abs(est[k] - v), 4) for k, v in truth.items()},
        },
        "surrogate_fit_to_model": surrogate,
    }
